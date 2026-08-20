#!/usr/bin/env python3
"""
Enhanced Pump.fun Lifecycle CLI
================================
Complete token lifecycle management from creation to profit extraction.
Designed to run alongside Hermes — standalone CLI with full failsafes.

Phases:
  1. CREATE  — Create token on Pump.fun via API (or fallback to test mint)
  2. FUND    — Distribute SOL from creator wallet to bot wallets
  3. BUY     — Initial buy sequence with anti-detection staggered timing
  4. TRADE   — Active trading loop with momentum/bubble detection
  5. TAKE_PROFIT — Tiered profit-taking at MC multipliers (2x, 3x, 5x, 10x, 20x, 100x)
  6. CASH_OUT — Convert all tokens to SOL via Jupiter (multi-route)
  7. CLOSE    — Sweep all SOL from bot wallets back to creator

Failsafes built-in:
  - Emergency exit on Ctrl+C / 'quit' command (immediate sell + collect)
  - State file persists progress between runs (crash recovery)
  - Stuck wallet detection + fund recovery mode
  - Network timeout guards (15s max per API call)
  - Automatic fallback: Jupiter -> Pump.fun direct -> manual
  - Balance floor enforcement (never drain gas money)
  - Phase skip/resume (start at any phase with --phase N)
  - Alternate faucets for devnet (QuickNode, Solfaucet, CLI airdrop)
  - Dry-run mode for all phases (simulate before spending real SOL)

Usage:
  python3 pumpfun_lifecycle_cli.py --devnet --auto --budget-usd 6 --full
  python3 pumpfun_lifecycle_cli.py --create --budget-usd 20 --image assets/token.png
  python3 pumpfun_lifecycle_cli.py --trade --mint <TOKEN_MINT> --trade-minutes 5
  python3 pumpfun_lifecycle_cli.py --emergency --mint <TOKEN_MINT>
  python3 pumpfun_lifecycle_cli.py --recover-stuck --mint <TOKEN_MINT>
  python3 pumpfun_lifecycle_cli.py --faucet-request  # Request devnet SOL from all faucets
  python3 pumpfun_lifecycle_cli.py --status            # Resume from last saved state

Author: Matthew A. Baker
"""

import argparse
import base64
import json
import os
import signal
import sys
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

# ─── Constants ───

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAMPORTS_PER_SOL = 1_000_000_000
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1zY8XbapC8G4wEGGkZwyTDt1v"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJybhwavoYi2rAmm3j3bA8Gi1W8"

# Pump.fun protocol constants (VERIFIED from pump-fun-sdk)
PUMP_FUN_API = "https://api.pump.fun"
PUMP_FUN_CREATE_API = "https://api.pump.fun/api/tokens"
PUMP_FUN_TOKEN_API = "https://api.pump.fun/api/tokens"
PUMP_FUN_COMMENT_URL = "https://pumpfun.com/thread/{thread_id}/comment"

# Jupiter API (public, no key required)
JUPITER_QUOTE_API = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP_API = "https://api.jup.ag/swap/v1/swap"

# Pump.fun protocol params
PUMP_CREATION_FEE_LAMPORTS = 200000  # 0.002 SOL (rent only, NO launch fee)
PUMP_VIRTUAL_SOL_INITIAL = 30.0  # Initial virtual SOL reserves
PUMP_VIRTUAL_TOKEN_INITIAL = 1_073_000_000  # Initial virtual token reserves
PUMP_TOKEN_SUPPLY = 1_000_000_000  # 1B tokens
PUMP_REAL_TOKEN_AVAILABLE = 793_100_000  # Real tokens for sale
PUMP_GRADUATION_SOL = 115.0  # Virtual SOL at graduation
PUMP_GRADUATION_MC_USD = 69000  # MC at graduation (~$69K)

# Fee tiers
PUMP_FEE_TIERS = {
    "low_mc": {"threshold": 28.0, "fee": 0.03},  # 3% at launch
    "mid_mc": {"threshold": 1496.0, "fee": 0.015},  # 1.5% at mid
    "high_mc": {"threshold": float('inf'), "fee": 0.0075},  # 0.75% at high
}

# Take-profit tiers (extended from 5 to 7)
TAKE_PROFIT_TIERS = [
    {"mc_mult": 2.0, "sell_pct": 0.10, "desc": "Early partial exit"},
    {"mc_mult": 3.0, "sell_pct": 0.10, "desc": "Continue partial selling"},
    {"mc_mult": 5.0, "sell_pct": 0.15, "desc": "Lock in solid gains"},
    {"mc_mult": 10.0, "sell_pct": 0.15, "desc": "Scale up exit"},
    {"mc_mult": 15.0, "sell_pct": 0.20, "desc": "Aggressive partial"},
    {"mc_mult": 20.0, "sell_pct": 0.20, "desc": "Majority exit"},
    {"mc_mult": 100.0, "sell_pct": 0.10, "desc": "Final full exit"},
]

# Emergency exit MC threshold
EMERGENCY_MC_THRESHOLD = 65000  # Just below graduation at $69K
STOP_LOSS_PCT = 0.30  # 30% stop loss

# RPC endpoints
MAINNET_RPC = "https://api.mainnet-beta.solana.com"
DEVNET_RPC = "https://api.devnet.solana.com"

# Alternate devnet faucets (for SOL airdrop requests)
DEVNET_FAUCETS = [
    {"name": "QuickNode Faucet", "url": "https://faucet.quicknode.com/solana/devnet",
     "type": "browser", "note": "Most reliable, requires tweet"},
    {"name": "Solfaucet", "url": "https://solfaucet.com", "type": "browser", "note": "Browser-based"},
    {"name": "CLI Airdrop (fallback)", "url": DEVNET_RPC, "type": "rpc", "note": "Rate-limited HTTP 429"},
]

# Gas budgeting (2-10% of budget for transaction fees)
GAS_BUDGET_MIN_PCT = 0.02
GAS_BUDGET_MAX_PCT = 0.10

# ─── Image Upload Constants ───
WEB3_STORAGE_API = "https://api.web3.storage/upload"
ARWEAVE_BUNDLR = "https://node1.bundlr.network"


def upload_token_image(image_path: str, api_token: Optional[str] = None,
                       dry_run: bool = False) -> Optional[str]:
    """Upload a token image to IPFS via web3.storage or Arweave via Bundlr.

    Args:
        image_path: Path to the image file (PNG/JPG/SVG)
        api_token: web3.storage API token (if None, tries env var WEB3_STORAGE_TOKEN)
        dry_run: If True, skip actual upload and return mock URL

    Returns:
        IPFS/Arweave gateway URL, or None on failure
    """
    if dry_run:
        print(f"  [DRY RUN] Would upload {image_path} to IPFS (mock URL)")
        return "https://ipfs.io/ipfs/QmDRYMEDITATION0000000000000000000000"

    if not os.path.exists(image_path):
        print(f"  [ERROR] Image not found: {image_path}")
        return None

    token = api_token or os.environ.get("WEB3_STORAGE_TOKEN", "")
    if not token:
        print(f"  [WARN] WEB3_STORAGE_TOKEN not set. Using Arweave via Bundlr (pay with SOL).")
        return _upload_to_arweave(image_path)

    try:
        with open(image_path, "rb") as f:
            image_data = f.read()

        boundary = "----FormBoundary7MA4YWxkTrZu0gW"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(image_path)}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + image_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            WEB3_STORAGE_API,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            cid = result.get("cid", "")
            if cid:
                url = f"https://ipfs.io/ipfs/{cid}"
                print(f"  [OK] Image uploaded: {url}")
                return url
            else:
                print(f"  [ERROR] Upload failed: {result}")
                return None
    except Exception as e:
        print(f"  [ERROR] Image upload failed: {e}")
        return _upload_to_arweave(image_path)


def _upload_to_arweave(image_path: str) -> Optional[str]:
    """Upload image to Arweave via Bundlr (pay with SOL)."""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()

        # Get cost estimate from Bundlr
        size = len(image_data)
        req = urllib.request.Request(
            f"{ARWEAVE_BUNDLR}/api/v2/balance",
            method="GET",
        )
        # Bundlr upload is complex — in dry-run mode, skip
        print(f"  [INFO] Arweave upload requires SOL payment (~{size/1000:.1f} bytes)")
        print(f"  [INFO] Use web3.storage for free uploads (set WEB3_STORAGE_TOKEN)")
        return None
    except Exception as e:
        print(f"  [ERROR] Arweave upload failed: {e}")
        return None


def _push_dashboard_state(phase: str = None, price: float = None,
                          mc_usd: float = None, log_entry: dict = None,
                          alert: str = None, wallets: list = None,
                          bubble_risk: float = None, tp_tier: str = None,
                          diversity_score: float = None, strategy: str = None):
    """Push state to the web_viz dashboard if it's running."""
    try:
        from web_viz import update_state
        update_state(phase=phase, price=price, mc_usd=mc_usd,
                     log_entry=log_entry, alert=alert, wallets=wallets,
                     bubble_risk=bubble_risk, tp_tier=tp_tier,
                     diversity_score=diversity_score, strategy=strategy)
    except (ImportError, ConnectionError):
        pass  # Dashboard not running — silently skip

# ─── State Management ───

STATE_FILE = os.path.join(SCRIPT_DIR, ".lifecycle_state.json")

@dataclass
class PhaseState:
    """Tracks state of a single phase for crash recovery."""
    phase: str
    status: str  # pending, running, completed, failed, skipped
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


# ─── Anti-Sniper Detection ───

def detect_sniping_activity(token_mint: str, lookback_secs: int = 30,
                            whitelisted_wallets: Optional[List[str]] = None,
                            dry_run: bool = False) -> Dict[str, Any]:
    """Detect potential sniper activity by monitoring concurrent transactions.

    Uses Solana FM API to fetch recent transactions for the token account.
    Snipers are identified as wallets that:
    1. Buy + sell within lookback window (in/out sniping)
    2. Buy in the same block as the launch (copy sniping)
    3. Are not in the whitelisted_wallets list

    Args:
        token_mint: Token mint to monitor
        lookback_secs: Time window to check for sniping (default: 30s)
        whitelisted_wallets: Known bot wallets to exclude from sniping detection
        dry_run: If True, return mock results

    Returns:
        Dict with sniping analysis: detected, suspicious_wallets, confidence
    """
    if dry_run:
        return {
            "detected": False,
            "suspicious_wallets": [],
            "confidence": 0.0,
            "note": "[DRY RUN] Sniping detection skipped",
        }

    whitelisted = set(w.lower() for w in (whitelisted_wallets or []))

    try:
        url = f"https://api.solana.fm/v0/token/micro/tokens/{token_mint}/txs"
        params = urllib.parse.urlencode({"limit": 100, "type[]": "SWAP"})

        req = urllib.request.Request(
            f"{url}?{params}",
            headers={
                "Accept": "application/json",
                "User-Agent": "PumpFun-Lifecycle/2.0",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            tx_data = data.get("data", {})
            txs = tx_data.get("transactions", []) if isinstance(tx_data, dict) else tx_data

        if not txs:
            return {"detected": False, "suspicious_wallets": [], "confidence": 0.0, "note": "No recent transactions"}

        now = time.time()
        recent_txs = [tx for tx in txs if now - tx.get("timestamp", now) <= lookback_secs]

        wallet_actions = {}
        for tx in recent_txs:
            wallet = tx.get("signer", "").lower()
            if wallet in whitelisted:
                continue
            if wallet not in wallet_actions:
                wallet_actions[wallet] = {"buy": None, "sell": None}
            action = tx.get("action", "").lower()
            if "buy" in action or tx.get("type") == "SWAP_IN":
                wallet_actions[wallet]["buy"] = tx.get("timestamp", now)
            elif "sell" in action or tx.get("type") == "SWAP_OUT":
                wallet_actions[wallet]["sell"] = tx.get("timestamp", now)

        suspicious = []
        for wallet, actions in wallet_actions.items():
            if actions["buy"] and actions["sell"]:
                time_diff = abs(actions["buy"] - actions["sell"])
                if time_diff <= lookback_secs:
                    confidence = min(0.95, 0.6 + (1.0 - time_diff / lookback_secs) * 0.35)
                    suspicious.append({
                        "wallet": wallet[:16] + "...",
                        "buy_time": actions["buy"],
                        "sell_time": actions["sell"],
                        "hold_duration": f"{time_diff:.1f}s",
                        "confidence": round(confidence, 3),
                    })

        confidence = max((w["confidence"] for w in suspicious), default=0.0)
        return {
            "detected": len(suspicious) > 0,
            "suspicious_wallets": suspicious,
            "confidence": confidence,
            "recent_tx_count": len(recent_txs),
            "note": f"Monitored {len(recent_txs)} txs in {lookback_secs}s window" if recent_txs else "No recent transactions",
        }

    except Exception as e:
        return {"detected": False, "suspicious_wallets": [], "confidence": 0.0, "error": str(e)}


# ─── Liquidity Lock Verification ───

def verify_liquidity_lock(token_mint: str, dry_run: bool = False) -> Dict[str, Any]:
    """Check if the token liquidity pool is locked using RugCheck data.

    Args:
        token_mint: Token mint to check
        dry_run: If True, return mock results

    Returns:
        Dict with lock status: locked, lp_token, lock_expiry, provider
    """
    if dry_run:
        return {
            "locked": True,
            "lp_token": "LOCKED_LP_TOKEN_0000",
            "lock_expiry": 0,
            "provider": "Team Finance / Unicrypt",
            "note": "[DRY RUN] Liquidity lock verification skipped",
        }

    report = rugcheck_token_report(token_mint)
    if report:
        lp_info = report.get("lp", {})
        lock_info = report.get("lp_locks", [])
        locked = len(lock_info) > 0 or lp_info.get("locked", False)
        return {
            "locked": locked,
            "lp_token": lp_info.get("lp_token", "unknown"),
            "lock_expiry": lock_info[0].get("end_time", 0) if lock_info else 0,
            "provider": lock_info[0].get("locker", "unknown") if lock_info else "unknown",
            "total_lp_usd": lp_info.get("lp_usd", 0),
        }
    return {"locked": False, "error": "Could not fetch RugCheck report"}


# ─── Holder Analysis ───

def analyze_holders(token_mint: str, top_n: int = 50,
                    bundler_detection: bool = True,
                    dry_run: bool = False) -> Dict[str, Any]:
    """Analyze token holder concentration and detect potential bundlers.

    Uses Solana FM API to fetch top holders and analyzes:
    - Top 10 concentration (should be <50% for healthy distribution)
    - Bundler detection (multiple wallets from same source)
    - Sniper wallets (recent large buys with quick sells)

    Args:
        token_mint: Token mint to analyze
        top_n: Number of top holders to fetch (default: 50)
        bundler_detection: Whether to run bundler detection algorithm
        dry_run: If True, return mock results

    Returns:
        Dict with holder analysis: concentration, bundlers, sniper_holders, top_holders
    """
    if dry_run:
        return {
            "concentration": {"top_10_pct": 45.0, "top_50_pct": 80.0, "gini": 0.65},
            "bundlers": [{"wallet": "0x1...", "wallet_count": 5, "total_pct": 12.5}],
            "sniper_holders": [],
            "top_holders": [{"rank": 1, "pct": 10.5, "label": "whale"}],
            "note": "[DRY RUN] Holder analysis skipped",
        }

    try:
        url = f"https://api.solana.fm/v0/token/{token_mint}/holders"
        params = urllib.parse.urlencode({"limit": top_n})

        req = urllib.request.Request(
            f"{url}?{params}",
            headers={
                "Accept": "application/json",
                "User-Agent": "PumpFun-Lifecycle/2.0",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            holders = data.get("data", {}).get("holders", []) if isinstance(data.get("data"), dict) else data.get("data", [])

        if not holders:
            return {"error": "No holders data"}

        total_supply = sum(h.get("balance", 0) for h in holders)
        top_10_pct = sum(h.get("balance", 0) for h in holders[:10]) / total_supply * 100 if total_supply else 0
        top_50_pct = sum(h.get("balance", 0) for h in holders[:50]) / total_supply * 100 if total_supply else 0

        sorted_h = sorted(holders, key=lambda x: x.get("balance", 0))
        n = len(sorted_h)
        cumsum = sum((i + 1) * h.get("balance", 0) for i, h in enumerate(sorted_h))
        gini = (2 * cumsum / (n * total_supply) - (n + 1) / n) if total_supply and n else 0
        gini = abs(gini)

        bundlers = []
        if bundler_detection:
            creation_times = {}
            for h in holders:
                ts = h.get("created_at", h.get("minted_at", ""))
                if ts:
                    creation_times.setdefault(ts, []).append(h.get("address", ""))
            for ts, wallets in creation_times.items():
                if len(wallets) > 3:
                    total_pct = sum(h.get("balance", 0) for h in holders if h.get("address", "") in wallets) / total_supply * 100 if total_supply else 0
                    bundlers.append({
                        "timestamp": ts,
                        "wallet_count": len(wallets),
                        "total_pct": round(total_pct, 2),
                        "wallets": wallets[:5],
                    })

        sniper_holders = []
        for h in holders[:20]:
            txns = h.get("tx_count", 0)
            if txns == 1 and h.get("balance", 0) / total_supply * 100 > 2.0:
                sniper_holders.append({
                    "wallet": h.get("address", "")[:16] + "...",
                    "pct": round(h.get("balance", 0) / total_supply * 100, 2) if total_supply else 0,
                })

        top_holders = [{"rank": i + 1, "pct": round(h.get("balance", 0) / total_supply * 100, 2) if total_supply else 0,
                        "label": h.get("label", "holder")}
                       for i, h in enumerate(holders[:20])]

        return {
            "concentration": {
                "top_10_pct": round(top_10_pct, 2),
                "top_50_pct": round(top_50_pct, 2),
                "gini": round(gini, 3),
            },
            "bundlers": bundlers,
            "sniper_holders": sniper_holders,
            "top_holders": top_holders,
            "total_holders": len(holders),
            "total_supply": total_supply,
        }

    except Exception as e:
        return {"error": str(e), "concentration": {}, "bundlers": [], "sniper_holders": [], "top_holders": []}


@dataclass
class LifecycleState:
    """Full lifecycle state for crash recovery and resume."""
    token_mint: Optional[str] = None
    token_name: str = ""
    token_symbol: str = ""
    token_image: Optional[str] = None
    creator_pubkey: Optional[str] = None
    creator_seed_b58: Optional[str] = None
    bot_wallets: List[Dict[str, Any]] = field(default_factory=list)
    budget_usd: float = 20.0
    budget_sol: float = 0.0
    network: str = "mainnet"
    current_phase: int = 0
    phases: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    last_updated: str = ""
    wallet_diversity_score: float = 0.0
    wallet_strategy: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        obj = cls(**d)
        obj.phases = {k: PhaseState.from_dict(v) if isinstance(v, dict) else v
                      for k, v in obj.phases.items()}
        return obj

    def save(self):
        """Save state to disk for crash recovery."""
        self.last_updated = datetime.now(timezone.utc).isoformat()
        with open(STATE_FILE, 'w') as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls) -> Optional['LifecycleState']:
        """Load state from disk if available."""
        if not os.path.exists(STATE_FILE):
            return None
        try:
            with open(STATE_FILE) as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return None

    def start_phase(self, phase_name: str):
        """Mark a phase as started."""
        self.phases[phase_name] = PhaseState(
            phase=phase_name, status="running", started_at=time.time()
        ).to_dict()
        self.current_phase = len(self.phases)
        self.save()

    def complete_phase(self, phase_name: str, data: Dict = None):
        """Mark a phase as completed."""
        if phase_name in self.phases:
            self.phases[phase_name]["status"] = "completed"
            self.phases[phase_name]["completed_at"] = time.time()
            if data:
                self.phases[phase_name]["data"] = data
            self.save()
        else:
            self.phases[phase_name] = PhaseState(
                phase=phase_name, status="completed",
                started_at=time.time(), completed_at=time.time(),
                data=data or {}
            ).to_dict()
            self.save()

    def fail_phase(self, phase_name: str, error: str):
        """Mark a phase as failed with error details."""
        if phase_name in self.phases:
            self.phases[phase_name]["status"] = "failed"
            self.phases[phase_name]["error"] = error
            self.phases[phase_name]["completed_at"] = time.time()
            self.save()

    def phase_status(self, phase_name: str) -> str:
        """Get status of a phase."""
        if phase_name not in self.phases:
            return "pending"
        return self.phases[phase_name].get("status", "pending") if isinstance(self.phases[phase_name], dict) else self.phases[phase_name].status


# ─── Emergency / Ctrl+C Handler ───

class EmergencyExit(Exception):
    """Raised when user triggers emergency exit (Ctrl+C or 'quit')."""
    pass


_emergency_triggered = False


def _signal_handler(signum, frame):
    global _emergency_triggered
    if _emergency_triggered:
        print("\n[EMERGENCY] Second Ctrl+C — force exit (recovery state saved)")
        sys.exit(1)
    _emergency_triggered = True
    print("\n[EMERGENCY] Ctrl+C received — raising EmergencyExit")
    raise EmergencyExit("User initiated emergency exit")


def set_emergency_handler():
    """Install Ctrl+C handler for graceful emergency exit."""
    signal.signal(signal.SIGINT, _signal_handler)


def clear_emergency():
    """Clear the emergency flag."""
    global _emergency_triggered
    _emergency_triggered = False


def check_emergency(prompt_interval: float = 0) -> bool:
    """Check if emergency exit was triggered. Call periodically in loops."""
    return _emergency_triggered


# ─── Utility Functions ───

def load_env_file(filepath: str = None):
    """Load .env file into environment."""
    if filepath is None:
        filepath = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(filepath):
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())


def call_node(cmd: List[str], timeout: int = 30) -> Optional[str]:
    """Run a Node.js helper command and return stdout."""
    import subprocess
    try:
        # Start the process in a new process group so SIGINT goes to us, not node
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=SCRIPT_DIR, env={**os.environ},
                                start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            print(f"  [ERROR] Node timeout ({timeout}s)")
            return None

        if proc.returncode == 0:
            return stdout.strip()
        else:
            print(f"  [ERROR] Node: {stderr.strip()[:200]}")
            return None
    except FileNotFoundError:
        print("  [ERROR] Node.js not found")
        return None


def rpc_request(rpc_url: str, method: str, params: List = None) -> Optional[Any]:
    """Make a JSON-RPC request to Solana."""
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 100000,
        "method": method,
        "params": params or [],
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(rpc_url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "PumpFun-Lifecycle/2.0",
        }, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            if "error" in result:
                return None
            return result.get("result")
    except urllib.error.HTTPError as e:
        print(f"  [RPC ERROR] {method}: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"  [RPC ERROR] {method}: {e}")
        return None


def get_balance(rpc: str, pubkey: str) -> float:
    """Get SOL balance in SOL (float)."""
    result = rpc_request(rpc, "getBalance", [pubkey])
    if result:
        return result["value"] / LAMPORTS_PER_SOL
    return 0.0


def get_token_balance(rpc: str, wallet: str, token_mint: str) -> float:
    """Get SPL token balance."""
    result = rpc_request(rpc, "getTokenAccountsByOwner", [
        wallet, {"mint": token_mint}, {"encoding": "jsonParsed"}
    ])
    if result and result.get("value"):
        accounts = result["value"]
        if accounts:
            try:
                amount = int(accounts[0]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
                decimals = int(accounts[0]["data"]["parsed"]["info"]["tokenAmount"]["decimals"])
                return amount / (10 ** decimals)
            except (KeyError, TypeError):
                pass
    return 0.0


def get_token_accounts(rpc: str, wallet: str) -> List[Dict[str, Any]]:
    """Get all SPL token accounts for a wallet."""
    result = rpc_request(rpc, "getTokenAccountsByOwner", [
        wallet, {"programId": TOKEN_PROGRAM_ID}, {"encoding": "jsonParsed"}
    ])
    accounts = []
    if result and result.get("value"):
        for acct in result["value"]:
            try:
                info = acct["account"]["data"]["parsed"]["info"]
                mint = info["mint"]
                amount = int(info["tokenAmount"]["amount"])
                decimals = int(info["tokenAmount"]["decimals"])
                accounts.append({
                    "mint": mint,
                    "balance": amount / (10 ** decimals),
                    "decimals": decimals,
                    "acct": mint[:16] + "..." + mint[-8:],
                })
            except (KeyError, TypeError):
                pass
    return accounts


def jup_quote(input_mint: str, output_mint: str, amount_lamports: int,
              slippage_bps: int = 300) -> Optional[dict]:
    """Get swap quote from Jupiter API."""
    url = JUPITER_QUOTE_API + "?" + urllib.parse.urlencode({
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_lamports),
        "slippageBps": str(slippage_bps),
        "filterZeroLiquidityPools": "true",
    })
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "PumpFun-Lifecycle/2.0",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            if data.get("data") and len(data["data"]) > 0:
                data["data"].sort(key=lambda x: int(x.get("outAmount", 0)), reverse=True)
                return data["data"][0]
    except Exception as e:
        print(f"  [WARN] Jupiter quote: {e}")
    return None


def jup_build_swap(route: dict, user_pubkey: str, slippage_bps: int = 300,
                   priority_fee_micro_lamports: int = 500_000) -> Optional[str]:
    """Build unsigned swap transaction via Jupiter."""
    payload = {
        "route": route,
        "userPublicKey": user_pubkey,
        "wrapUnwrapSol": True,
        "feeBps": 0,
        "computeUnitPriceMicroLamports": priority_fee_micro_lamports,
        "preference": "jitter",
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(JUPITER_SWAP_API, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "PumpFun-Lifecycle/2.0",
        }, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return result.get("swapTransaction")
    except Exception as e:
        print(f"  [WARN] Jupiter swap build: {e}")
    return None


def sign_and_send(rpc: str, seed_b58: str, unsigned_tx_b64: str) -> Optional[str]:
    """Sign and submit a transaction using local Node helper."""
    result = call_node([
        "node", os.path.join(SCRIPT_DIR, "sign_sender.js"),
        "sign_send", rpc, unsigned_tx_b64, seed_b58,
    ], timeout=20)
    if result:
        data = json.loads(result)
        if "error" in data:
            print(f"  [ERROR] Sign/send: {data['error']}")
            return None
        return data.get("signature")
    return None


def batch_transfer_sol(rpc: str, main_seed: str,
                       transfers: List[Tuple[str, int]]) -> Optional[dict]:
    """Transfer SOL to multiple recipients via Node helper."""
    recipients_str = ",".join(f"{addr}:{amt}" for addr, amt in transfers)
    result = call_node([
        "node", os.path.join(SCRIPT_DIR, "sign_sender.js"),
        "batch_transfer", rpc, main_seed, recipients_str,
    ], timeout=60)
    if result:
        return json.loads(result)
    return None


def derive_sub_wallet(main_seed_b58: str, index: int) -> Optional[Dict]:
    """Derive a deterministic sub-wallet from the main seed."""
    result = call_node([
        "node", os.path.join(SCRIPT_DIR, "wallet_utils.js"),
        "derive", "--seed", main_seed_b58, "--index", str(index),
    ], timeout=10)
    if result:
        return json.loads(result)
    return None


def get_pub_from_seed(seed_b58: str) -> Optional[str]:
    """Get public key from a base58 seed."""
    result = call_node([
        "node", os.path.join(SCRIPT_DIR, "wallet_utils.js"),
        "get_pub", "--seed", seed_b58,
    ], timeout=10)
    if result:
        data = json.loads(result)
        return data.get("pubkey")
    return None


def get_token_decimals(rpc: str, token_mint: str) -> int:
    """Get token decimals from on-chain mint account."""
    account = rpc_request(rpc, "getAccountInfo", [
        token_mint, {"encoding": "jsonParsed", "commitment": "confirmed"}
    ])
    if account and account.get("value"):
        try:
            return int(account["value"]["data"]["parsed"]["info"]["decimals"])
        except (KeyError, TypeError):
            pass
    return 6  # Default assumption


# ─── RugCheck Integration ───

def rugcheck_token_report(token_mint: str) -> Dict[str, Any]:
    """
    Fetch RugCheck report for a token.
    Free API — no authentication required.

    Returns risk assessment including:
    - Token safety score (0-100)
    - Mint authority status (renounced or not)
    - Freeze authority status
    - Top holder concentration
    - Creator holdings
    - Risk flags

    Source: https://api.rugcheck.xyz/v1/tokens/{mint}/report
    """
    url = f"https://api.rugcheck.xyz/v1/tokens/{token_mint}/report"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "PumpFun-Lifecycle/2.0",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

            # Parse key fields
            report = {
                "mint": token_mint,
                "score": data.get("score_normal", 0) / 100.0,  # Normalize 0-1
                "token_name": data.get("tokenMeta", {}).get("name", ""),
                "symbol": data.get("tokenMeta", {}).get("symbol", ""),
                "mint_authority": data.get("token", {}).get("mintAuthority", "unknown"),
                "freeze_authority": data.get("token", {}).get("freezeAuthority", "unknown"),
                "is_initialized": data.get("token", {}).get("isInitialized", False),
                "supply": data.get("token", {}).get("supply", 0),
                "top_holders": data.get("topHolders", []),
                "risks": data.get("risks", []),
                "creator": data.get("creator", ""),
                "creator_balance": data.get("creatorBalance", 0),
                "ok": True,
            }

            # Risk assessment summary
            risks = report["risks"] if report["risks"] else []
            high_risk_count = sum(1 for r in risks if r.get("level", "low") in ("high", "danger"))

            report["risk_level"] = "HIGH" if high_risk_count > 2 or report["score"] < 0.3 else \
                                   "MEDIUM" if report["score"] < 0.7 else "LOW"
            report["risk_count"] = len(risks)
            report["high_risk_count"] = high_risk_count

            # Holder concentration check
            if report["top_holders"]:
                top1_pct = report["top_holders"][0].get("pct", 0) if report["top_holders"] else 0
                report["top1_holder_pct"] = top1_pct
                if top1_pct > 20:
                    report["risk_level"] = "HIGH"
                    report["risks"].append({"name": "High holder concentration", "level": "high"})

            return report

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "Token not found on Raydium/Pump.fun"}
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def print_rugcheck_report(report: Dict[str, Any], verbose: bool = False):
    """Print a formatted RugCheck risk report."""
    if not report.get("ok"):
        print(f"  [RUGCHECK] Error: {report.get('error', 'unknown')}")
        return

    score_pct = report["score"] * 100
    print(f"\n  [RUGCHECK] Safety Score: {score_pct:.0f}/100 ({report['risk_level']})")
    print(f"  Mint Authority:   {report['mint_authority'] or 'NOT SET (renounced)'}")
    print(f"  Freeze Authority: {report['freeze_authority'] or 'NOT SET (frozen disabled)'}")
    print(f"  Risks detected:   {report['risk_count']} ({report['high_risk_count']} high)")

    if verbose and report["risks"]:
        print("  Risk details:")
        for r in report["risks"][:5]:  # Show top 5
            level = r.get("level", "unknown").upper()
            name = r.get("name", r.get("risk", "unknown"))
            print(f"    [{level}] {name}")

    if report["top_holders"]:
        print(f"  Top holder:        {report['top_holders'][0].get('pct', 0):.1f}%")
        @classmethod
        def from_dict(cls, d):
            return cls(**d)


@dataclass
class StopLossConfig:
    """Configuration for stop-loss monitoring."""
    enabled: bool = True
    max_drawdown_pct: float = 0.30  # 30% from peak
    max_loss_pct: float = 0.30      # 30% from entry
    check_interval_sec: float = 15.0
    emergency_at_loss_pct: float = 0.50  # 50% loss triggers emergency exit
    cooldown_after_trigger_sec: float = 60.0  # Don't re-trigger within 60s


@dataclass
class StopLossState:
    """Runtime state for stop-loss monitoring."""
    entry_price: float = 0.0
    peak_price: float = 0.0
    current_price: float = 0.0
    trigger_count: int = 0
    last_trigger_time: float = 0.0
    last_alert_time: float = 0.0
    triggered: bool = False
    trigger_reason: str = ""
    triggered_at_mc: float = 0.0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


# ─── Stop-Loss Monitoring ───

def check_stop_loss(state: LifecycleState, token_mint: str,
                    sl_config: StopLossConfig, sl_state: StopLossState,
                    rpc: str) -> Tuple[bool, str]:
    """
    Check if stop-loss should be triggered based on current price and entry.

    Triggers on:
    1. Drawdown from peak exceeds max_drawdown_pct
    2. Loss from entry exceeds max_loss_pct
    3. Loss from entry exceeds emergency_at_loss_pct (emergency exit)

    Returns:
        Tuple of (should_trigger, reason)
    """
    if not sl_config.enabled:
        return False, ""

    mc_usd = _get_token_mc_usd(token_mint, rpc)
    if mc_usd <= 0:
        return False, ""

    # Estimate token price from MC and known supply
    if state.token_mint:
        supply = _get_token_supply(token_mint, rpc)
    else:
        supply = PUMP_TOKEN_SUPPLY

    if supply > 0:
        current_price = mc_usd / supply
    else:
        current_price = mc_usd / PUMP_TOKEN_SUPPLY

    sl_state.current_price = current_price

    # Initialize entry/peak if first check
    if sl_state.entry_price == 0:
        sl_state.entry_price = current_price
        sl_state.peak_price = current_price
        return False, ""

    # Update peak
    if current_price > sl_state.peak_price:
        sl_state.peak_price = current_price

    now = time.time()

    # Check drawdown from peak
    drawdown_pct = (sl_state.peak_price - current_price) / sl_state.peak_price if sl_state.peak_price > 0 else 0
    if drawdown_pct >= sl_config.max_drawdown_pct:
        if now - sl_state.last_trigger_time > sl_config.cooldown_after_trigger_sec:
            return True, f"Drawdown {drawdown_pct*100:.1f}% exceeds {sl_config.max_drawdown_pct*100:.0f}% threshold"

    # Check loss from entry
    loss_pct = (sl_state.entry_price - current_price) / sl_state.entry_price if sl_state.entry_price > 0 else 0
    if loss_pct >= sl_config.emergency_at_loss_pct:
        return True, f"EMERGENCY: Loss {loss_pct*100:.1f}% exceeds {sl_config.emergency_at_loss_pct*100:.0f}% threshold"

    if loss_pct >= sl_config.max_loss_pct:
        if now - sl_state.last_trigger_time > sl_config.cooldown_after_trigger_sec:
            return True, f"Loss {loss_pct*100:.1f}% exceeds {sl_config.max_loss_pct*100:.0f}% threshold"

    return False, ""


# ─── Phase 1: Token Creation ───

def create_pumpfun_token(name: str, symbol: str, description: str,
                         image_path: str = None,
                         devnet: bool = False,
                         dry_run: bool = False) -> Optional[str]:
    """
    Create a token on Pump.fun via API.
    Falls back to generating a test mint on devnet.

    Pump.fun creation fee: ~0.002 SOL (rent only, NO launch fee)
    """
    print(f"\n[PHASE 1] Creating token: {name} ({symbol})")

    if dry_run:
        mint = "DRYMINT00000000000000000000000000000000000"[:32]
        print(f"  [DRY RUN] Mock mint: {mint}")
        return mint

    if devnet:
        # Devnet: Generate a test token mint via SPL-token simulation
        # In a real devnet environment, you'd use `spl-token create-token`
        print("  [DEVNET] Generating test token mint...")
        result = call_node([
            "node", os.path.join(SCRIPT_DIR, "wallet_utils.js"), "generate",
        ], timeout=10)
        if result:
            data = json.loads(result)
            # Use a derived address as mock mint for devnet
            mint = data["pubkey"]
            print(f"  [DEVNET] Test mint created: {mint}")
            return mint
        else:
            print("  [ERROR] Could not generate test mint")
            return None

    # Mainnet: Try Pump.fun API
    # Upload image if provided
    image_url = None
    if image_path and os.path.exists(image_path):
        print("  Uploading token image...")
        image_url = upload_token_image(image_path, dry_run=dry_run)

    token_data = {
        "tokenName": name,
        "symbol": symbol,
        "description": description,
        "image": image_url or image_path,
        "twitterUsername": "",
        "telegramUsername": "",
        "discord": "",
    }

    try:
        url = "https://api.pump.fun/api/tokens"
        payload = json.dumps(token_data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            mint = result.get("mint") or result.get("tokenAddress") or result.get("token")
            if mint:
                print(f"  [OK] Token created on Pump.fun: {mint}")
                return mint
    except urllib.error.HTTPError as e:
        if e.code == 530:
            print("  [WARN] Pump.fun API returned 530 (overloaded)")
            print("  [FALLBACK] Using manual transaction construction")
        else:
            print(f"  [WARN] Pump.fun API: HTTP {e.code}")
    except Exception as e:
        print(f"  [WARN] Pump.fun API error: {e}")

    # Fallback: Manual token creation on Solana
    # This is the "alternate" approach - create SPL token directly
    print("  [FALLBACK] Creating token as standard SPL token...")
    # In production, this would build and sign a CreateInitializeAccount tx
    # For now, we simulate with a generated address
    result = call_node([
        "node", os.path.join(SCRIPT_DIR, "wallet_utils.js"), "generate",
    ], timeout=10)
    if result:
        data = json.loads(result)
        mint = data["pubkey"]
        print(f"  [FALLBACK] Mock token mint: {mint}")
        print("  [NOTE] On mainnet, use Pump.fun web UI to create, then pass --mint")
        return mint

    print("  [ERROR] Token creation failed on all paths")
    return None


# ─── Phase 1.5: Wallet Warmup ───

def wallet_warmup(state: LifecycleState, token_mint: Optional[str] = None,
                  dry_run: bool = False) -> Dict[str, Any]:
    """
    Execute small test trades to build organic-looking trading history.

    From cicere/pumpfun-bundler research:
    - Small buys/sells ($0.01-0.05 SOL) before main launch
    - Builds organic trading history
    - Simulates different trading patterns per wallet
    - Reduces "fresh wallet" detection during main launch

    If token_mint is provided, uses it for test trades.
    If not, just sends small SOL transfers between wallets to create activity.
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 1.5] Wallet Warmup — small test transactions")

    if not state.bot_wallets:
        print("  [WARN] No wallets to warm up")
        return {"error": "no_wallets"}

    import random

    warmup_results = []

    for w in state.bot_wallets:
        if check_emergency():
            print("\n  [EMERGENCY] Warmup interrupted")
            break

        # Small SOL transfer to another wallet (simulates organic funding)
        target_w = state.bot_wallets[
            random.randint(0, len(state.bot_wallets) - 1)
        ]
        if target_w["pubkey"] == w["pubkey"]:
            target_w = state.bot_wallets[0] if w["index"] > 0 else state.bot_wallets[-1]

        warmup_amount = random.uniform(0.001, 0.005)  # $0.15-0.75 at $150/SOL

        print(f"  W{w['index']+1}: Warmup transfer {warmup_amount:.6f} SOL → W{target_w['index']+1}")

        if dry_run:
            print(f"  [DRY RUN] Would transfer {warmup_amount:.6f} SOL")
            warmup_results.append({
                "from_wallet": w["pubkey"][:16],
                "to_wallet": target_w["pubkey"][:16],
                "amount_sol": warmup_amount,
                "dry_run": True,
            })
            continue

        result = batch_transfer_sol(rpc, w["seed_b58"],
                                    [(target_w["pubkey"], int(warmup_amount * LAMPORTS_PER_SOL))])
        if result and "results" in result:
            for r in result["results"]:
                if "signature" in r:
                    warmup_results.append({
                        "from_wallet": w["pubkey"][:16],
                        "to_wallet": target_w["pubkey"][:16],
                        "amount_sol": warmup_amount,
                        "signature": r["signature"][:32],
                    })
                    print(f"  [WARMUP OK] {r['signature'][:32]}...")
                else:
                    print(f"  [WARMUP FAIL] {r.get('error', 'unknown')}")

        # Small randomized delay (anti-detection)
        delay = random.uniform(1.0, 3.0)
        time.sleep(delay)

    state.save()
    print(f"  Warmup complete: {len(warmup_results)} transfers")
    return {"results": warmup_results, "wallets_warmed": len(state.bot_wallets)}


# ─── Phase 2: Fund Wallets ───

def fund_wallets(state: LifecycleState, budget_usd: float,
                 num_wallets: int = 3, max_wallet_pct: float = 0.80,
                 dry_run: bool = False) -> Dict[str, Any]:
    """
    Distribute SOL from creator wallet to bot wallets.

    Uses tier-based allocation (5-wallet system):
    - Whale (1): 35% — large dip buys
    - Mid (2): 20% each — steady volume
    - Small (2): 7.5% each — small trades, comments
    - Buffer: 10% — transaction fees

    Args:
        state: Current lifecycle state
        budget_usd: Total USD budget
        num_wallets: Number of bot wallets to fund
        max_wallet_pct: Max % of budget per wallet (caps at 80%)
        dry_run: If True, simulate without sending real transactions
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 2] Funding {num_wallets} wallets from creator")

    if not state.creator_seed_b58:
        print("  [ERROR] No creator seed configured")
        return {"error": "no_creator_seed"}

    if dry_run:
        creator_pub = "DRYRUN_CREATOR000000000000000000000000000000000"[:44]
        # In dry-run, skip actual balance check
    else:
        creator_pub = get_pub_from_seed(state.creator_seed_b58)
        if not creator_pub:
            print("  [ERROR] Could not derive creator pubkey")
            return {"error": "creator_pubkey_failed"}

    state.creator_pubkey = creator_pub
    if dry_run:
        creator_balance = 99.0
    else:
        creator_balance = get_balance(rpc, creator_pub)
    print(f"  Creator balance: {creator_balance:.6f} SOL")

    sol_price_usd = 150.0  # Approximate; could fetch from CoinGecko
    budget_sol = budget_usd / sol_price_usd
    state.budget_sol = budget_sol

    # Gas budget: 2-10% of budget
    gas_budget_pct = min(max(0.05, budget_sol * 0.05 / budget_sol), 0.10)
    gas_budget_sol = budget_sol * gas_budget_pct
    print(f"  Budget: ${budget_usd} = {budget_sol:.6f} SOL (gas reserve: {gas_budget_sol:.6f} SOL)")

    if creator_balance < budget_sol * 0.9:
        print(f"  [WARN] Creator has {creator_balance:.4f} SOL, budget needs {budget_sol:.4f} SOL")
        if not dry_run:
            if creator_balance < gas_budget_sol:
                print("  [ERROR] Insufficient funds for gas. Cannot proceed.")
                return {"error": "insufficient_gas"}

    # Generate sub-wallets (or mock them in dry-run)
    print(f"  Generating {num_wallets} sub-wallets...")
    state.bot_wallets = []
    for i in range(num_wallets):
        if dry_run:
            # Use mock wallets for dry run (no Node.js needed)
            mock_pub = f"DRYWALLET{ i+1:03d}00000000000000000000000000"[:44]
            state.bot_wallets.append({
                "index": i,
                "pubkey": mock_pub,
                "seed_b58": "DRYRUN_SEED_" + str(i),
                "role": "bot",
                "allocated_sol": 0.0,
                "spent_sol": 0.0,
                "tokens_held": 0.0,
            })
            print(f"    Wallet {i+1}/{num_wallets}: {mock_pub[:16]}... [DRY RUN]")
        else:
            w = derive_sub_wallet(state.creator_seed_b58, i)
            if w:
                state.bot_wallets.append({
                    "index": i,
                    "pubkey": w["pubkey"],
                    "seed_b58": w["seed_b58"],
                    "role": "bot",
                    "allocated_sol": 0.0,
                    "spent_sol": 0.0,
                    "tokens_held": 0.0,
                })

    # Generate human-like profiles for each wallet
    if state.bot_wallets:
        print(f"\n  Generating wallet profiles...")
        try:
            from profile_gen import generate_profiles_for_bundle
            profiles_data = generate_profiles_for_bundle(
                num_wallets=len(state.bot_wallets),
                seed=None  # Random each time for variety
            )
            for i, wallet in enumerate(state.bot_wallets):
                if i < len(profiles_data["profiles"]):
                    wallet["profile"] = profiles_data["profiles"][i]
            # Store diversity info in state
            meta = profiles_data["metadata"]
            state.wallet_diversity_score = meta.get("diversity_score", 0.0)
            state.wallet_strategy = meta.get("recommended_strategy", "Round Robin")
            print(f"  Profiles generated: diversity={meta['diversity_score']:.3f}, strategy={meta['recommended_strategy']}")
            if meta["diversity_score"] < 0.5:
                print(f"  [WARN] Low diversity score ({meta['diversity_score']:.3f}) — wallets may appear similar")
        except ImportError:
            print(f"  [WARN] profile_gen.py not available — using generic profiles")
            for w in state.bot_wallets:
                w["profile"] = {"username": f"Wallet{w['index']+1}", "trading_style": "moderate", "buy_probability": 0.5, "sell_probability": 0.3}

    if not state.bot_wallets:
        return {"error": "wallet_generation_failed"}

    # Allocate funds per wallet
    # Whale gets more, small wallets get less, keep buffer
    if num_wallets >= 5:
        # Tiered allocation: Whale 35%, Mid x2 20% each, Small x2 7.5% each
        allocations = _tiered_allocation(num_wallets, budget_sol, gas_budget_sol)
    else:
        # Simple equal split with buffer
        per_wallet = (budget_sol - gas_budget_sol) / num_wallets
        allocations = [per_wallet] * num_wallets

    # Distribute
    transfers = []
    total_allocated = 0.0
    for i, wallet in enumerate(state.bot_wallets):
        alloc = allocations[i] if i < len(allocations) else allocations[-1]
        wallet["allocated_sol"] = alloc
        total_allocated += alloc
        if alloc > 0:
            transfers.append((wallet["pubkey"], int(alloc * LAMPORTS_PER_SOL)))

    print(f"\n  Allocation summary:")
    for w in state.bot_wallets:
        print(f"    W{w['index']+1} ({w['role']}): {w['allocated_sol']:.6f} SOL")
    print(f"  Total allocated: {total_allocated:.6f} SOL")
    print(f"  Gas buffer: {gas_budget_sol:.6f} SOL")

    if dry_run:
        print("  [DRY RUN] Skipping actual transfers")
    else:
        print("  Sending batch transfer...")
        result = batch_transfer_sol(rpc, state.creator_seed_b58, transfers)
        if result and "results" in result:
            success_count = sum(1 for r in result["results"] if "signature" in r)
            print(f"  [OK] {success_count}/{len(transfers)} transfers successful")
            for i, r in enumerate(result["results"]):
                if "signature" in r:
                    state.bot_wallets[i]["spent_sol"] = allocations[i] * 0.000005  # tx fee
                elif "error" in r:
                    print(f"    [ERROR] Wallet {i+1}: {r['error']}")
        else:
            print("  [WARN] Batch transfer failed, trying individual transfers")
            for w in state.bot_wallets:
                individual_result = batch_transfer_sol(rpc, state.creator_seed_b58,
                                                       [(w["pubkey"], int(w["allocated_sol"] * LAMPORTS_PER_SOL))])
                if individual_result and "results" in individual_result:
                    if "signature" in individual_result["results"][0]:
                        print(f"    [OK] Wallet {w['index']+1} funded individually")

    state.save()
    return {
        "wallets_funded": len(state.bot_wallets),
        "total_allocated_sol": total_allocated,
        "gas_buffer_sol": gas_budget_sol,
        "wallet_details": state.bot_wallets,
    }

    # Push state to web dashboard if running
    _push_dashboard_state(phase="FUNDED", wallets=state.bot_wallets,
                          diversity_score=state.wallet_diversity_score,
                          strategy=state.wallet_strategy)


def _tiered_allocation(num_wallets: int, total_sol: float,
                       gas_sol: float) -> List[float]:
    """Calculate tiered allocation for 5-wallet system."""
    tradeable = total_sol - gas_sol
    if num_wallets >= 5:
        # Whale 35%, Mid x2 20% each, Small x2 7.5% each
        allocations = [
            tradeable * 0.35,  # Whale
            tradeable * 0.20,  # Mid 1
            tradeable * 0.20,  # Mid 2
            tradeable * 0.075, # Small 1
            tradeable * 0.075, # Small 2
        ]
        # Distribute remaining to extra wallets if > 5
        remaining = tradeable - sum(allocations)
        for i in range(5, num_wallets):
            allocations.append(remaining / (num_wallets - 5) if num_wallets > 5 else 0)
        return allocations[:num_wallets]
    else:
        per = tradeable / num_wallets
        return [per] * num_wallets


# ─── Phase 3: Initial Buy ───

def initial_buy_sequence(state: LifecycleState, token_mint: str,
                         buy_pct: float = 0.5, dry_run: bool = False,
                         min_trade_sol: float = 0.01) -> Dict[str, Any]:
    """
    Initial buy sequence with anti-detection staggered timing.
    - Whale buys first (large), others follow with small staggered buys
    - Random jitter between 1-5 seconds per wallet
    - Mix of buy amounts to look organic
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 3] Initial buy sequence for {token_mint[:16]}...")

    if not state.bot_wallets:
        print("  [ERROR] No wallets funded. Run --fund first.")
        return {"error": "no_wallets"}

    # Check balances
    print("  Checking balances...")
    for w in state.bot_wallets:
        if dry_run:
            bal = w.get("allocated_sol", 0.01)  # Use allocated as mock balance
        else:
            bal = get_balance(rpc, w["pubkey"])
        w["current_sol"] = bal
        if bal < min_trade_sol:
            print(f"  [WARN] Wallet {w['index']+1} has only {bal:.6f} SOL — skipping")
            w["skipped"] = True
        else:
            w["skipped"] = False
        print(f"    W{w['index']+1}: {bal:.6f} SOL")

    if dry_run:
        token_decimals = 6
    else:
        token_decimals = get_token_decimals(rpc, token_mint)
    print(f"  Token decimals: {token_decimals}")

    buy_results = []
    for w in state.bot_wallets:
        if w.get("skipped"):
            continue

        if check_emergency():
            print("  [EMERGENCY] Buy sequence interrupted by user")
            break

        # Calculate buy amount (varies by role)
        alloc = w["allocated_sol"] - w.get("spent_sol", 0)
        buy_amount = alloc * buy_pct  # Use 50% for initial buy

        # Add randomness: ±20%
        import random
        buy_amount = buy_amount * random.uniform(0.8, 1.2)
        buy_amount = max(buy_amount, min_trade_sol)

        buy_lamports = int(buy_amount * LAMPORTS_PER_SOL)
        print(f"\n  W{w['index']+1} buying {buy_amount:.6f} SOL worth of tokens...")

        if dry_run:
            print(f"  [DRY RUN] Would buy {buy_amount:.6f} SOL → tokens")
            buy_results.append({
                "wallet": w["pubkey"][:16],
                "buy_sol": buy_amount,
                "tokens_received": buy_amount * 100000,  # Mock
                "dry_run": True,
            })
            time.sleep(0.5)  # Fast for dry run
            continue

        # Get Jupiter quote
        quote = jup_quote(WRAPPED_SOL_MINT, token_mint, buy_lamports, slippage_bps=500)
        if not quote:
            print(f"  [WARN] No quote for W{w['index']+1}, trying Pump.fun direct...")
            # Fallback: direct pump.fun swap
            quote = _pumpfun_direct_quote(token_mint, buy_lamports)
            if not quote:
                print(f"  [SKIP] No route for W{w['index']+1}")
                continue

        expected_tokens = int(quote.get("outAmount", 0))
        token_amount = expected_tokens / (10 ** token_decimals)
        print(f"  [QUOTE] Expected ~{token_amount:.4f} tokens")

        # Build swap transaction
        unsigned_tx = jup_build_swap(quote, w["pubkey"], slippage_bps=500,
                                     priority_fee_micro_lamports=500_000)
        if not unsigned_tx:
            print(f"  [ERROR] Could not build swap for W{w['index']+1}")
            continue

        # Sign and send
        sig = sign_and_send(rpc, w["seed_b58"], unsigned_tx)
        if sig:
            print(f"  [BUY OK] TX: {sig[:32]}...")
            buy_results.append({
                "wallet": w["pubkey"][:16],
                "buy_sol": buy_amount,
                "tokens_received": token_amount,
                "signature": sig[:32],
            })
            w["tokens_held"] = token_amount
        else:
            print(f"  [BUY FAIL] Transaction failed for W{w['index']+1}")
            buy_results.append({
                "wallet": w["pubkey"][:16],
                "buy_sol": buy_amount,
                "error": "transaction_failed",
            })

        # Staggered delay (anti-detection)
        import random
        delay = random.uniform(1.0, 5.0)
        print(f"  [WAIT] {delay:.1f}s...")
        time.sleep(delay)

    state.save()
    return {
        "buys_completed": len(buy_results),
        "results": buy_results,
    }


def _pumpfun_direct_quote(token_mint: str, amount_lamports: int) -> Optional[dict]:
    """Direct Pump.fun bonding curve quote (fallback when Jupiter fails)."""
    # Pump.fun direct AMM: use Jupiter which includes pump pools
    # But filter for direct pump.fun route
    quote = jup_quote(WRAPPED_SOL_MINT, token_mint, amount_lamports, slippage_bps=1000)
    if quote and quote.get("route", {}).get("steps"):
        for step in quote["route"]["steps"]:
            if "pump" in str(step.get("dex", "")).lower():
                return quote
    return quote  # Return any quote if pump-specific not found


# ─── Phase 4: Active Trading ───

def run_active_trading(state: LifecycleState, token_mint: str,
                       duration_minutes: float = 10, dry_run: bool = False,
                       test_mode: bool = True,
                       sl_config: Optional[StopLossConfig] = None,
                       sl_state: Optional[StopLossState] = None,
                       auto: bool = False,
                       comment_enabled: bool = False,
                       comment_interval: float = 45.0) -> Dict[str, Any]:
    """
    Active trading loop with momentum and bubble detection.

    In test_mode, runs 0.01s cycles for fast simulation.
    In live mode, uses real timing.

    If sl_config and sl_state are provided, monitors stop-loss conditions
    and triggers emergency exit if thresholds are breached.

    If comment_enabled and comment_interval are set, posts comments from
    randomized wallet profiles at the specified interval (default: 45s).
    """
    global _LAST_COMMENT_TIME
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 4] Active trading for {duration_minutes} minutes...")

    # Import the TradingOrchestrator if available
    try:
        sys.path.insert(0, SCRIPT_DIR)
        from trading_orchestrator import TradingOrchestrator

        trader = TradingOrchestrator(
            budget_sol=state.budget_sol or 6.0,
            token_mint=token_mint or "",
            test_mode=test_mode,
        )
        trader.initialize(creator_seed=state.creator_seed_b58 or "")
        trader.is_running = True

        # Run with emergency check
        start_time = time.time()

        if test_mode and hasattr(trader, 'run_simulation'):
            # In test mode, cap duration to prevent runaway cycles.
            # The skill docs note: bubble detection at 0.80+ causes thousands of
            # no-op cycles. Use 0.03 minutes (1.8 seconds) max in test mode.
            # The 1.8-second budget gives enough time for initial buys + bubble buildup
            # while the 10-cycle no-trade break in the orchestrator ensures termination.
            test_duration = min(duration_minutes, 0.03) if duration_minutes > 0 else 0.03
            summary = trader.run_simulation(
                duration_minutes=test_duration,
                initial_buy_sol=0.50,
            )
            mc_mult = summary.get('mc_multiplier', 1.0)
            current_mc = 450.0 * mc_mult  # Starting MC from Pump.fun protocol
            print(f"  [SIM] Completed: MC ${current_mc:.0f} ({mc_mult:.2f}x)")
            # Post a comment after simulation completes
            if comment_enabled:
                _try_post_comment(token_mint, state, dry_run)
        elif not test_mode and hasattr(trader, 'launch_and_trade'):
            # Live mode: run launch_and_trade with comment integration
            while (time.time() - start_time) < (duration_minutes * 60):
                if check_emergency():
                    print("\n  [EMERGENCY] Trading interrupted — exiting loop")
                    break

                if comment_enabled and state.bot_wallets:
                    # Comment at intervals during live trading
                    _try_post_comment(token_mint, state, dry_run)

                try:
                    # Run one step — launch_and_trade handles the full lifecycle internally
                    trader.launch_and_trade(
                        budget_sol=state.budget_sol,
                        token_mint=token_mint,
                        initial_mc_usd=450.0,
                    )
                    break  # launch_and_trade runs the full lifecycle
                except Exception as e:
                    print(f"  [WARN] Cycle error: {e}")
                    time.sleep(1)
                    continue

            # Stop-loss monitoring
            if sl_config and sl_state and sl_config.enabled:
                should_trigger, reason = check_stop_loss(
                    state, token_mint, sl_config, sl_state, rpc
                )
                if should_trigger:
                    if "EMERGENCY" in reason:
                        print(f"\n  ⚠️  STOP-LOSS EMERGENCY: {reason}")
                        print("  Triggering emergency exit...")
                        raise EmergencyExit(f"Stop-loss: {reason}")
                    else:
                        print(f"\n  ⚠️  STOP-LOSS TRIGGERED: {reason}")
                        print("  Consider exiting or adjusting strategy.")
                        sl_state.trigger_count += 1
                        sl_state.last_trigger_time = time.time()
                        if not auto:
                            print("  Press Enter to continue or Ctrl+C to exit: ", end="", flush=True)
                            sys.stdin.readline()

            if test_mode:
                time.sleep(0.01)
            else:
                time.sleep(5)

            elapsed = time.time() - start_time
            if elapsed % 30 < 5:
                mc = _get_token_mc(token_mint, rpc)
                print(f"  [STATUS] {elapsed:.0f}s elapsed | MC: ${mc:.0f}")

            # Auto-comment from wallet profiles during trading
            if comment_enabled and state.bot_wallets:
                interval = comment_interval if not test_mode else min(comment_interval, 15.0)
                if _LAST_COMMENT_TIME == 0 or (elapsed - _LAST_COMMENT_TIME) >= interval:
                    _try_post_comment(token_mint, state, dry_run)
                    _LAST_COMMENT_TIME = time.time()

    except ImportError:
        print("  [WARN] TradingOrchestrator not available, using simplified loop")
        _simple_trading_loop(state, token_mint, duration_minutes, dry_run,
                             sl_config=sl_config, sl_state=sl_state, auto=auto)

    state.save()
    return {"status": "completed_or_interrupted"}


def _simple_trading_loop(state: LifecycleState, token_mint: str,
                         duration_minutes: float, dry_run: bool,
                         sl_config: Optional[StopLossConfig] = None,
                         sl_state: Optional[StopLossState] = None,
                         auto: bool = False):
    """Simplified trading loop when full orchestrator isn't available."""
    # In dry-run mode, do only 3 cycles
    max_cycles = 3 if dry_run else int(duration_minutes * 6)  # ~10s per cycle

    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    start_time = time.time()
    cycle = 0
    while cycle < max_cycles:
        if check_emergency():
            print("\n  [EMERGENCY] Trading interrupted")
            break

        cycle += 1
        if dry_run:
            mc = 100  # Mock MC
        else:
            mc = _get_token_mc(token_mint, state.network)
        print(f"  Cycle {cycle}/{max_cycles} | MC: ${mc:.0f} | Wallets: {len(state.bot_wallets)}")

        # Stop-loss check
        if sl_config and sl_state and sl_config.enabled and not dry_run:
            should_trigger, reason = check_stop_loss(
                state, token_mint, sl_config, sl_state, rpc
            )
            if should_trigger:
                if "EMERGENCY" in reason:
                    print(f"\n  ⚠️  STOP-LOSS EMERGENCY: {reason}")
                    print("  Triggering emergency exit...")
                    raise EmergencyExit(f"Stop-loss: {reason}")
                else:
                    print(f"\n  ⚠️  STOP-LOSS TRIGGERED: {reason}")
                    if not auto:
                        print("  Press Enter to continue or Ctrl+C to exit: ", end="", flush=True)
                        sys.stdin.readline()

        if dry_run:
            time.sleep(0.01)
        else:
            if (time.time() - start_time) >= (duration_minutes * 60):
                break
            time.sleep(10)


# ─── Phase 5: Take Profit ───

def take_profit(state: LifecycleState, token_mint: str,
                target_mc_x: float = 5.0, dry_run: bool = False) -> Dict[str, Any]:
    """
    Tiered profit-taking based on MC multiplier.

    Tiers: 2x→14%, 3x→14%, 5x→14%, 10x→14%, 15x→20%, 20x→30%, 100x→24%
    Only sells from wallets with sufficient token balance.
    Can be interrupted by user at any time.
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 5] Take-profit mode — target {target_mc_x}x MC")

    # Determine current MC and which tier we're at
    launch_mc = PUMP_GRADUATION_MC_USD / 5.0  # Approximate launch MC (~$13.8K as baseline)

    # Actually, let's compute based on bonding curve
    mc_usd = _get_token_mc_usd(token_mint, rpc)
    if mc_usd < 1:
        mc_usd = launch_mc  # Use estimate

    # Find applicable tier
    applicable_tier = None
    for tier in reversed(TAKE_PROFIT_TIERS):
        if mc_usd >= launch_mc * tier["mc_mult"]:
            applicable_tier = tier
            break

    if not applicable_tier:
        print(f"  Current MC ${mc_usd:.0f} below 2x threshold, no sell triggered")
        print("  [INFO] Use --target-mc to force a take-profit at specific multiplier")
        return {"status": "no_sell", "mc_usd": mc_usd}

    print(f"  [OK] Current MC: ${mc_usd:.0f} ({applicable_tier['mc_mult']}x)")
    print(f"  Tier: {applicable_tier['desc']} — selling {applicable_tier['sell_pct']*100:.0f}%")

    # Sell from each wallet
    if dry_run:
        token_decimals = 6
    else:
        token_decimals = get_token_decimals(rpc, token_mint)
    sell_results = []

    for w in state.bot_wallets:
        if check_emergency():
            print("\n  [EMERGENCY] Take-profit interrupted")
            break

        if dry_run:
            token_balance = w.get("tokens_held", 0.0)
        else:
            token_balance = get_token_balance(rpc, w["pubkey"], token_mint)
        if token_balance <= 0:
            print(f"  W{w['index']+1}: No tokens to sell")
            continue

        sell_pct = applicable_tier["sell_pct"]
        sell_amount = token_balance * sell_pct
        sell_raw = int(sell_amount * (10 ** token_decimals))

        if dry_run:
            print(f"  W{w['index']+1}: Would sell {sell_amount:.4f} tokens ({sell_pct*100:.0f}%)")
            sell_results.append({"wallet": w["pubkey"][:16], "tokens_sold": sell_amount, "dry_run": True})
            continue

        print(f"  W{w['index']+1}: Selling {sell_amount:.4f} tokens...")
        quote = jup_quote(token_mint, WRAPPED_SOL_MINT, sell_raw, slippage_bps=500)
        if quote:
            unsigned_tx = jup_build_swap(quote, w["pubkey"], slippage_bps=500,
                                         priority_fee_micro_lamports=500_000)
            if unsigned_tx:
                sig = sign_and_send(rpc, w["seed_b58"], unsigned_tx)
                if sig:
                    sol_received = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
                    print(f"  [SELL OK] Got {sol_received:.6f} SOL")
                    sell_results.append({
                        "wallet": w["pubkey"][:16],
                        "tokens_sold": sell_amount,
                        "sol_received": sol_received,
                        "signature": sig[:32],
                    })
                    w["tokens_held"] = max(0, w.get("tokens_held", 0) - sell_amount)
                else:
                    print(f"  [SELL FAIL] Transaction error")
            else:
                print(f"  [ERROR] Could not build sell tx")
        else:
            print(f"  [WARN] No sell route for W{w['index']+1}")

    state.save()
    total_sold = sum(r.get("tokens_sold", 0) for r in sell_results)
    total_sol = sum(r.get("sol_received", 0) for r in sell_results)
    print(f"\n  Take-profit summary: sold {total_sold:.4f} tokens, received {total_sol:.6f} SOL")

    # Push TP tier to dashboard
    _push_dashboard_state(tp_tier=applicable_tier["mc_mult"], mc_usd=mc_usd,
                          log_entry={"t": time.time(), "event": f"TP {applicable_tier['mc_mult']}x",
                                      "wallets": len(sell_results), "change": f"+{applicable_tier['sell_pct']*100:.0f}%"})

    return {
        "tokens_sold": total_sold,
        "sol_received": total_sol,
        "wallets_sold_from": len(sell_results),
        "tier_used": applicable_tier,
    }


# ─── Phase 6: Cash Out ───

def cash_out_all_tokens(state: LifecycleState, token_mint: str,
                        dry_run: bool = False) -> Dict[str, Any]:
    """
    Convert ALL remaining tokens to SOL via Jupiter multi-route.
    Tries multiple slippage levels if initial quote fails.
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 6] Cash-out: converting all tokens to SOL")
    print(f"  Token: {token_mint[:16]}...")

    if not state.bot_wallets:
        print("  [ERROR] No wallets configured")
        return {"error": "no_wallets"}

    if dry_run:
        token_decimals = 6
    else:
        token_decimals = get_token_decimals(rpc, token_mint)
    print(f"  Token decimals: {token_decimals}")

    cash_results = []
    total_sol_recovered = 0.0

    for w in state.bot_wallets:
        if check_emergency():
            print("\n  [EMERGENCY] Cash-out interrupted")
            break

        if dry_run:
            token_balance = w.get("tokens_held", 0.0)
        else:
            token_balance = get_token_balance(rpc, w["pubkey"], token_mint)
        if token_balance <= 0:
            print(f"  W{w['index']+1}: No tokens remaining")
            continue

        sell_raw = int(token_balance * (10 ** token_decimals))
        print(f"\n  W{w['index']+1}: Selling {token_balance:.4f} tokens...")

        if dry_run:
            print(f"  [DRY RUN] Would sell all {token_balance:.4f} tokens")
            cash_results.append({"wallet": w["pubkey"][:16], "dry_run": True, "tokens": token_balance})
            continue

        # Try with increasing slippage tolerance
        slippage_levels = [500, 1000, 2000, 3000]  # 5% to 30%
        quote = None
        used_slippage = None

        for sl in slippage_levels:
            quote = jup_quote(token_mint, WRAPPED_SOL_MINT, sell_raw, slippage_bps=sl)
            if quote:
                used_slippage = sl
                break

        if not quote:
            print(f"  [WARN] No sell route for W{w['index']+1} at any slippage")
            cash_results.append({"wallet": w["pubkey"][:16], "error": "no_route"})
            continue

        print(f"  [QUOTE] Got route (slippage {used_slippage} bps)")
        unsigned_tx = jup_build_swap(quote, w["pubkey"], slippage_bps=used_slippage,
                                     priority_fee_micro_lamports=500_000)
        if not unsigned_tx:
            print(f"  [ERROR] Could not build tx")
            cash_results.append({"wallet": w["pubkey"][:16], "error": "build_failed"})
            continue

        sig = sign_and_send(rpc, w["seed_b58"], unsigned_tx)
        if sig:
            sol_received = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
            print(f"  [CASH OUT OK] {sol_received:.6f} SOL received")
            total_sol_recovered += sol_received
            cash_results.append({
                "wallet": w["pubkey"][:16],
                "tokens_sold": token_balance,
                "sol_received": sol_received,
                "signature": sig[:32],
            })
            w["tokens_held"] = 0
        else:
            print(f"  [CASH OUT FAIL]")
            cash_results.append({"wallet": w["pubkey"][:16], "error": "tx_failed"})

    state.save()
    print(f"\n  Total SOL recovered: {total_sol_recovered:.6f}")
    return {
        "total_sol_recovered": total_sol_recovered,
        "wallets_processed": len(cash_results),
        "results": cash_results,
    }


# ─── Phase 7: Close Wallets ───

def close_wallets(state: LifecycleState, dry_run: bool = False) -> Dict[str, Any]:
    """
    Transfer ALL remaining SOL from bot wallets back to creator.
    This is the final cleanup step — recovers gas money and leftover funds.
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[PHASE 7] Closing wallets — sweeping SOL to creator")

    if not state.creator_seed_b58 or not state.creator_pubkey:
        print("  [ERROR] No creator wallet configured")
        return {"error": "no_creator"}

    print(f"  Creator: {state.creator_pubkey[:16]}...")

    close_results = []
    total_recovered = 0.0

    for w in state.bot_wallets:
        if check_emergency():
            print("\n  [EMERGENCY] Wallet close interrupted")
            break

        if dry_run:
            # In dry-run, skip RPC balance check — use stored/mock balance
            balance = w.get("sol_balance", 0.019)
            keep_min = 0.001
        else:
            balance = get_balance(rpc, w["pubkey"])
            # Keep minimum 0.001 SOL for gas (don't drain completely)
            keep_min = 0.001
            if balance <= keep_min:
                print(f"  W{w['index']+1}: Balance {balance:.6f} SOL (too low to sweep)")
                continue

        amount_to_send = balance - keep_min
        lamports = int(amount_to_send * LAMPORTS_PER_SOL)

        print(f"\n  W{w['index']+1}: {balance:.6f} SOL → sweep {amount_to_send:.6f} SOL")

        if dry_run:
            print(f"  [DRY RUN] Would send {amount_to_send:.6f} SOL to {state.creator_pubkey[:16]}...")
            close_results.append({"wallet": w["pubkey"][:16], "dry_run": True, "amount": amount_to_send})
            continue

        result = batch_transfer_sol(rpc, w["seed_b58"],
                                    [(state.creator_pubkey, lamports)])
        if result and "results" in result:
            for r in result["results"]:
                if "signature" in r:
                    print(f"  [CLOSE OK] {amount_to_send:.6f} SOL swept")
                    total_recovered += amount_to_send
                    close_results.append({
                        "wallet": w["pubkey"][:16],
                        "amount_sol": amount_to_send,
                        "signature": r["signature"][:32],
                    })
                else:
                    print(f"  [CLOSE FAIL] {r.get('error', 'unknown')}")
                    close_results.append({"wallet": w["pubkey"][:16], "error": r.get("error")})
        else:
            print(f"  [CLOSE FAIL] Unknown error")

    if not dry_run:
        final_balance = get_balance(rpc, state.creator_pubkey)
        print(f"\n  Creator final balance: {final_balance:.6f} SOL")
    else:
        final_balance = 99.0
        print(f"\n  Creator final balance: {final_balance:.6f} SOL [DRY RUN]")
    print(f"  Total recovered: {total_recovered:.6f} SOL")

    state.save()
    return {
        "total_recovered_sol": total_recovered,
        "creator_final_balance": final_balance,
        "results": close_results,
    }


# ─── Emergency Exit ───

def emergency_exit(state: LifecycleState, token_mint: str,
                   dry_run: bool = False) -> Dict[str, Any]:
    """
    Immediate full exit: sell ALL tokens + collect ALL SOL.
    Uses maximum slippage tolerance (1000 bps = 10%) for speed.
    Bypasses all profit-taking tiers.
    """
    print("\n" + "=" * 60)
    print("  *** EMERGENCY EXIT INITIATED ***")
    print("=" * 60)
    print("  This will:")
    print("  1. Sell ALL tokens from ALL wallets at any price")
    print("  2. Sweep ALL SOL from ALL wallets to creator")
    print("  3. Bypass all profit-taking tiers and safety checks")
    print("=" * 60)

    if not dry_run:
        print("  Type 'CONFIRM' to proceed: ", end="", flush=True)
        confirm = sys.stdin.readline().strip()
        if confirm != "CONFIRM":
            print("  Emergency exit cancelled.")
            return {"status": "cancelled"}

    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n  [1/2] Selling ALL tokens...")
    cash_result = cash_out_all_tokens(state, token_mint, dry_run=dry_run)

    print(f"\n  [2/2] Sweeping ALL SOL...")
    close_result = close_wallets(state, dry_run=dry_run)

    print("\n" + "=" * 60)
    print("  EMERGENCY EXIT COMPLETE")
    print(f"  SOL recovered: {close_result.get('total_recovered_sol', 0):.6f}")
    print(f"  Creator final: {close_result.get('creator_final_balance', 0):.6f} SOL")
    print("=" * 60)

    return {"cash_out": cash_result, "close": close_result}


# ─── Recovery: Stuck Wallet Detection ───

def detect_and_recover_stuck_wallets(state: LifecycleState,
                                     token_mint: str) -> Dict[str, Any]:
    """
    Detect wallets with stuck funds and attempt recovery.
    A wallet is 'stuck' if it has:
    - Tokens but no SOL (can't pay gas for swap)
    - SOL but can't trade (network issues, bad routes)
    - Funds in a closed/unknown state

    Recovery methods:
    - Free gas reserves: send 0.002 SOL from creator for gas
    - Force sell via direct pump.fun swap
    - Manual export of seed phrases for manual recovery
    """
    rpc = DEVNET_RPC if state.network == "devnet" else MAINNET_RPC
    print(f"\n[RECOVERY] Scanning for stuck wallets...")

    stuck_wallets = []
    recovered = []

    for w in state.bot_wallets:
        sol_bal = get_balance(rpc, w["pubkey"])
        token_bal = get_token_balance(rpc, w["pubkey"], token_mint)

        is_stuck = False
        issues = []

        # Check 1: Has tokens but no SOL for gas
        if token_bal > 0 and sol_bal < 0.002:
            is_stuck = True
            issues.append(f"has_tokens ({token_bal:.4f}) but no_gas ({sol_bal:.6f} SOL)")

        # Check 2: Has SOL but no tokens (should have been swept)
        if sol_bal > 0.005 and token_bal == 0:
            issues.append(f"has_sol ({sol_bal:.6f}) but no_tokens — needs sweep")

        # Check 3: Negative or anomalous state
        if w.get("allocated_sol", 0) > 0 and sol_bal == 0 and token_bal == 0:
            is_stuck = True
            issues.append("fully_drained — funds may be lost")

        if issues:
            print(f"  W{w['index']+1} ({w['pubkey'][:16]}...): {', '.join(issues)}")

        if is_stuck:
            stuck_wallets.append({"wallet": w, "issues": issues})

            # Attempt recovery: send gas
            if "has_tokens" in issues[0] and "no_gas" in issues[0]:
                print(f"  [RECOVERY] Sending 0.005 SOL for gas to W{w['index']+1}...")
                result = batch_transfer_sol(rpc, state.creator_seed_b58,
                                            [(w["pubkey"], int(0.005 * LAMPORTS_PER_SOL))])
                if result and "results" in result:
                    if "signature" in result["results"][0]:
                        print(f"  [RECOVERY OK] Gas sent to W{w['index']+1}")
                        recovered.append({"wallet": w["pubkey"][:16], "action": "sent_gas"})
                        # Now try to sell tokens
                        token_decimals = get_token_decimals(rpc, token_mint)
                        sell_raw = int(token_bal * (10 ** token_decimals))
                        quote = jup_quote(token_mint, WRAPPED_SOL_MINT, sell_raw, slippage_bps=1000)
                        if quote:
                            unsigned_tx = jup_build_swap(quote, w["pubkey"], slippage_bps=1000)
                            if unsigned_tx:
                                sig = sign_and_send(rpc, w["seed_b58"], unsigned_tx)
                                if sig:
                                    print(f"  [RECOVERY OK] Sold stuck tokens: {sig[:32]}")
                                    recovered.append({"wallet": w["pubkey"][:16], "action": "sold_tokens"})
                                    w["tokens_held"] = 0

    # Wallets with SOL but no tokens — sweep them
    for w in state.bot_wallets:
        sol_bal = get_balance(rpc, w["pubkey"])
        token_bal = get_token_balance(rpc, w["pubkey"], token_mint)
        if sol_bal > 0.005 and token_bal == 0:
            print(f"  [RECOVERY] Sweeping {sol_bal:.6f} SOL from W{w['index']+1}...")
            result = batch_transfer_sol(rpc, w["seed_b58"],
                                        [(state.creator_pubkey, int((sol_bal - 0.001) * LAMPORTS_PER_SOL))])
            if result and "results" in result:
                if "signature" in result["results"][0]:
                    print(f"  [RECOVERY OK] Swept {sol_bal - 0.001:.6f} SOL")
                    recovered.append({"wallet": w["pubkey"][:16], "action": "swept_sol"})

    # Export seed phrases for manual recovery
    if stuck_wallets:
        print(f"\n  [MANUAL RECOVERY] {len(stuck_wallets)} wallets need manual handling:")
        for s in stuck_wallets:
            w = s["wallet"]
            print(f"    W{w['index']+1}: {w['pubkey']}")
            print(f"      Seed: {w['seed_b58'][:30]}...{w['seed_b58'][-10:]}")
            print(f"      Issues: {', '.join(s['issues'])}")
            print(f"      Recovery: Import seed into Phantom and manually transfer")

        # Save recovery info to file
        recovery_file = os.path.join(SCRIPT_DIR, ".recovery_report.json")
        recovery_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "token_mint": token_mint,
            "stuck_wallets": [{"pubkey": w["pubkey"], "seed_b58": w["seed_b58"][:20] + "...",
                              "issues": s["issues"]} for s in stuck_wallets],
            "recovered": recovered,
        }
        with open(recovery_file, 'w') as f:
            json.dump(recovery_data, f, indent=2)
        print(f"\n  Recovery report saved to: {recovery_file}")

    state.save()
    return {"stuck_count": len(stuck_wallets), "recovered": recovered}


# ─── Devnet Faucet Handler ───

def request_devnet_sol(target_pubkey: str, preferred_method: str = "auto") -> bool:
    """
    Request devnet SOL from alternate faucets.

    Methods (tried in order):
    1. Solfaucet.com (browser-based, most reliable for headless)
    2. QuickNode faucet (browser-based, requires tweet)
    3. RPC airdrop (rate-limited, last resort)

    Args:
        target_pubkey: Wallet to fund
        preferred_method: 'auto', 'cli', 'solfaucet', 'quicknode'

    Returns:
        True if successful, False if all methods failed
    """
    print(f"\n[FAUCET] Requesting devnet SOL for {target_pubkey[:16]}...")

    if preferred_method in ("auto", "solfaucet"):
        # Try SOLfaucet via their API endpoint
        print("  Trying Solfaucet API...")
        try:
            url = "https://solfaucet.com/api/request"
            payload = json.dumps({"address": target_pubkey, "network": "devnet"}).encode()
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            }, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
                if result.get("success") or result.get("txid"):
                    print(f"  [OK] Solfaucet: {result.get('message', 'funded')}")
                    return True
        except Exception as e:
            print(f"  [WARN] Solfaucet: {e}")

    if preferred_method in ("auto", "quicknode"):
        # QuickNode faucet requires browser interaction, but we can try the API
        print("  Trying QuickNode faucet...")
        try:
            url = "https://faucet.quicknode.com/api/faucet/solana/devnet"
            payload = json.dumps({"address": target_pubkey}).encode()
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            }, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
                if result.get("success"):
                    print(f"  [OK] QuickNode faucet: funded")
                    return True
        except Exception as e:
            print(f"  [WARN] QuickNode: {e}")

    # Fallback: RPC airdrop (rate-limited)
    if preferred_method in ("auto", "cli"):
        print("  Trying RPC airdrop (may be rate-limited)...")
        result = rpc_request(DEVNET_RPC, "requestAirdrop", [target_pubkey, 1.0 * LAMPORTS_PER_SOL])
        if result:
            print(f"  [OK] RPC airdrop: tx = {result[:32]}...")
            return True
        else:
            print("  [WARN] RPC airdrop failed (likely rate-limited)")

    # All methods failed
    print("\n  [INFO] All faucets failed. Manual options:")
    print("    1. Visit https://faucet.quicknode.com/solana/devnet in browser")
    print("    2. Visit https://solfaucet.com in browser")
    print("    3. Use Solana CLI: solana airdrop 1 <pubkey> --url devnet")
    print("       (install: sh -c \"$(curl -sSfL https://release.solana.com/stable/install)\")")
    return False


# ─── Helper: Get token MC ───

def _get_token_mc_usd(token_mint: str, rpc: str) -> float:
    """Get current token market cap in USD via Jupiter price API."""
    url = f"https://api.jup.ag/price/v3?ids={token_mint}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if token_mint in data:
                price_data = data[token_mint]
                price = price_data.get("usdPrice", 0)
                vol = price_data.get("volume24h", 0)
                # Get token supply from mint
                supply = _get_token_supply(token_mint, rpc)
                if supply > 0 and price > 0:
                    return price * supply
    except Exception:
        pass
    return 0.0


def _get_token_supply(token_mint: str, rpc: str) -> float:
    """Get token supply from on-chain."""
    account = rpc_request(rpc, "getAccountInfo", [
        token_mint, {"encoding": "jsonParsed", "commitment": "confirmed"}
    ])
    if account and account.get("value"):
        try:
            info = account["value"]["data"]["parsed"]["info"]
            amount = int(info.get("supply", {}).get("amount", 0))
            decimals = int(info.get("decimals", 0))
            return amount / (10 ** decimals)
        except (KeyError, TypeError):
            pass
    return 1_000_000_000  # Default: 1B tokens


def _get_token_mc(token_mint: str, rpc: str) -> float:
    """Get token market cap in SOL-equivalent for status display."""
    return _get_token_mc_usd(token_mint, rpc) / 150.0  # Approximate at $150/SOL


# ─── CLI Argument Parser ───

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with comprehensive phase control."""
    parser = argparse.ArgumentParser(
        description="Enhanced Pump.fun Token Lifecycle CLI — from creation to profit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Phases:
  CREATE    Create token on Pump.fun (or generate test mint on devnet)
  FUND      Distribute SOL from creator to bot wallets
  BUY       Initial buy sequence with anti-detection timing
  TRADE     Active trading loop with momentum/bubble detection
  TAKE_PROFIT  Tiered profit-taking at MC multipliers
  CASH_OUT  Convert all remaining tokens to SOL
  CLOSE     Sweep all SOL from bot wallets to creator
  EMERGENCY Immediate full exit (sell everything + collect SOL)

Examples:
  Full lifecycle (devnet, auto, $6 budget):
    %(prog)s --devnet --auto --budget-usd 6 --full

  Create token only:
    %(prog)s --create --budget-usd 20 --name "TokenName" --symbol TKN

  Trade existing token for 5 minutes:
    %(prog)s --trade --mint <TOKEN> --trade-minutes 5

  Emergency exit (will ask for CONFIRM):
    %(prog)s --emergency --mint <TOKEN>

  Recover stuck wallets:
    %(prog)s --recover-stuck --mint <TOKEN>

  Request devnet SOL from faucets:
    %(prog)s --faucet-request <WALLET_ADDRESS>

  Resume from saved state:
    %(prog)s --resume
""")
    # Phase selection
    parser.add_argument("--create", action="store_true", help="Phase 1: Create token")
    parser.add_argument("--fund", action="store_true", help="Phase 2: Fund bot wallets")
    parser.add_argument("--buy", action="store_true", help="Phase 3: Initial buy sequence")
    parser.add_argument("--trade", action="store_true", help="Phase 4: Active trading")
    parser.add_argument("--take-profit", action="store_true", help="Phase 5: Take profit")
    parser.add_argument("--cashout", action="store_true", help="Phase 6: Cash out tokens to SOL")
    parser.add_argument("--close", action="store_true", help="Phase 7: Close wallets (sweep SOL)")
    parser.add_argument("--emergency", action="store_true", help="Emergency exit (sell everything + collect SOL)")
    parser.add_argument("--recover-stuck", action="store_true", help="Detect and recover stuck wallets")
    parser.add_argument("--warmup", action="store_true", help="Run wallet warmup (small test trades before launch)")
    parser.add_argument("--full", action="store_true", help="Run all phases in sequence")
    parser.add_argument("--resume", action="store_true", help="Resume from saved state file")

    # Token config
    parser.add_argument("--mint", type=str, help="Token mint address (skip creation)")
    parser.add_argument("--name", type=str, default="Token", help="Token name")
    parser.add_argument("--symbol", type=str, default="TKN", help="Token symbol")
    parser.add_argument("--description", type=str, default="A meme token on Solana", help="Token description")
    parser.add_argument("--image", type=str, help="Path to token image for metadata")

    # Wallet config
    parser.add_argument("--wallet", type=str, help="Creator wallet seed (base58). If omitted, generates new.")
    parser.add_argument("--wallets", type=int, default=3, help="Number of bot wallets (default: 3, devnet: 2-3)")

    # Budget
    parser.add_argument("--budget-usd", type=float, default=20.0, help="Total budget in USD (default: 20)")
    parser.add_argument("--buy-sol", type=float, help="Initial buy amount in SOL (default: auto = 50%% of budget)")

    # Trading
    parser.add_argument("--trade-minutes", type=float, default=10.0, help="Trading duration in minutes")
    parser.add_argument("--target-mc", type=float, help="Target MC multiplier for take-profit")

    # Network
    parser.add_argument("--devnet", action="store_true", help="Use devnet")
    parser.add_argument("--network", choices=["mainnet", "devnet"], default="mainnet")
    parser.add_argument("--rpc", type=str, help="Custom RPC endpoint")

    # Faucet
    parser.add_argument("--faucet-request", type=str, metavar="WALLET",
                        help="Request devnet SOL from alternate faucets for given wallet")

    # Safety
    parser.add_argument("--slippage", type=int, default=500, help="Slippage tolerance bps (default: 500)")
    parser.add_argument("--priority-fee", type=int, default=500000, help="Priority fee in microlamports")
    parser.add_argument("--stop-loss-pct", type=float, default=STOP_LOSS_PCT, help="Stop-loss percentage")
    parser.add_argument("--rugcheck", action="store_true", help="Run RugCheck safety scan before trading")
    parser.add_argument("--stop-loss-disable", action="store_true", help="Disable stop-loss monitoring")
    parser.add_argument("--dashboard", action="store_true", help="Start web dashboard alongside lifecycle (http://localhost:8765)")
    parser.add_argument("--comment", action="store_true", help="Enable auto-comment posting during trading (every 45s from random wallets)")
    parser.add_argument("--comment-interval", type=float, default=45.0, help="Seconds between auto-comments (default: 45s)")
    parser.add_argument("--status", action="store_true", help="Show current lifecycle status and exit")
    parser.add_argument("--inspect", action="store_true", help="Deep inspection: show all phase details + wallet balances + token info, then continue")
    parser.add_argument("--inspect-wallet", type=str, help="Inspect a specific wallet: balance, tokens, transaction count")
    parser.add_argument("--inspect-mint", type=str, help="Inspect a specific token mint: supply, holders, RugCheck score")

    # Execution
    parser.add_argument("--auto", action="store_true", help="No prompts (fully automated)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without real transactions")
    parser.add_argument("--test-mode", action="store_true", help="Fast test mode (0.01s cycles)")
    parser.add_argument("--gas-buffer", type=float, default=0.001, help="Min SOL to keep for gas per wallet")
    parser.add_argument("--stealth", action="store_true", help="Enable stealth mode (suppress non-critical alerts when bubble risk high)")
    parser.add_argument("--stealth-threshold", type=float, default=0.6, help="Bubble risk threshold to activate stealth mode (default: %%0.6f%%)")

    return parser


# ─── Inspection Functions ───

def _print_status():
    """Print current lifecycle state and exit."""
    if not os.path.exists(STATE_FILE):
        print("No saved state file found.")
        print(f"  Expected: {STATE_FILE}")
        return

    state = LifecycleState.load()
    if not state:
        print("ERROR: Could not load state file.")
        return

    print("=" * 60)
    print("  PUMP.FUN LIFECYCLE — CURRENT STATUS")
    print("=" * 60)
    print(f"  Token: {state.token_name} ({state.token_symbol})")
    print(f"  Mint:  {state.token_mint or '(not created)'}")
    print(f"  Network: {state.network}")
    print(f"  Current Phase: {state.current_phase or '(none)'}")
    print(f"  Created: {state.created_at}")
    print(f"  Wallets: {len(state.bot_wallets)}")
    print(f"  Budget: ${state.budget_usd}")
    print()
    print("  Phase Status:")
    for ph_name, ph_info in state.phases.items():
        if isinstance(ph_info, dict):
            status = ph_info.get("status", "pending")
        else:
            status = getattr(ph_info, "status", "pending")
        symbol = "✓" if status == "completed" else "○" if status == "pending" else "⚠"
        print(f"    {symbol} {ph_name:<15} {status}")
    print()
    if hasattr(state, 'recovery_info') and state.recovery_info:
        print(f"  Recovery: {len(state.recovery_info)} wallets flagged for recovery")
    print("=" * 60)


def _inspect_wallet(seed_b58: str, rpc: str, is_devnet: bool = False):
    """Inspect a specific wallet by seed phrase."""
    pubkey = get_pub_from_seed(seed_b58)
    if not pubkey:
        print(f"ERROR: Could not derive pubkey from seed")
        return

    print("=" * 60)
    print(f"  WALLET INSPECTION")
    print("=" * 60)
    print(f"  Pubkey: {pubkey}")
    print(f"  Network: {'devnet' if is_devnet else 'mainnet'}")

    balance = get_balance(rpc, pubkey)
    print(f"  SOL Balance: {balance:.6f} SOL (${balance * 150:.2f})")

    if balance < 0.001:
        print(f"  ⚠️  WARNING: Balance below gas floor (0.001 SOL). Cannot pay for transactions!")

    # Check for any token accounts
    token_accts = get_token_accounts(rpc, pubkey)
    if token_accts:
        print(f"\n  Token Accounts ({len(token_accts)}):")
        for acct in token_accts[:10]:  # Show first 10
            mint = acct.get("mint", "unknown")[:16] + "..."
            bal = acct.get("balance", 0)
            print(f"    {mint}  Balance: {bal}")
        if len(token_accts) > 10:
            print(f"    ... and {len(token_accts) - 10} more")
    else:
        print(f"\n  Token Accounts: none")

    # Check transaction count
    try:
        sigs = rpc_request(rpc, "getSignaturesForAddress", [pubkey, {"limit": 1}])
        if sigs and "result" in sigs:
            count = len(sigs["result"])
            print(f"\n  Recent Transactions: {count}")
            if count == 0:
                print(f"  ⚠️  WARNING: Fresh wallet (0 transactions). Consider warmup!")
    except Exception:
        pass

    print("=" * 60)


def _inspect_token(mint: str, rpc: str, is_devnet: bool = False):
    """Inspect a specific token mint."""
    print("=" * 60)
    print(f"  TOKEN INSPECTION")
    print("=" * 60)
    print(f"  Mint: {mint}")
    print(f"  Network: {'devnet' if is_devnet else 'mainnet'}")

    # Token supply
    supply = _get_token_supply(mint, rpc)
    print(f"  Total Supply: {supply:,}" if supply > 0 else f"  Total Supply: unknown")

    # Token account count (holder count)
    try:
        resp = rpc_request(rpc, "getTokenLargestAccounts", [mint])
        holder_count = 0
        if resp and "result" in resp and resp["result"].get("value"):
            holder_count = len(resp["result"]["value"])
        print(f"  Holders (largest accounts): {holder_count}")
    except Exception:
        print(f"  Holders: error fetching")

    # RugCheck scan
    print(f"\n  [RUGCHECK] Scanning...")
    report = rugcheck_token_report(mint)
    if report.get("ok"):
        print_rugcheck_report(report, verbose=True)
    else:
        print(f"  [RUGCHECK] Error: {report.get('error', 'unknown')}")
        print(f"  (RugCheck only covers tokens on Raydium/Pump.fun production)")

    # Current MC estimate
    mc = _get_token_mc_usd(mint, rpc)
    if mc > 0:
        print(f"\n  Current Market Cap: ${mc:,.2f}")
    else:
        print(f"\n  Current Market Cap: (not available — token may not be traded yet)")

    print("=" * 60)


# ─── Telegram Alert Helper ───
_TELEGRAM_BOT = None

# ─── Comment Bot State ───
_COMMENT_BOT = None
_LAST_COMMENT_TIME = 0.0


def _get_comment_bot():
    """Lazy-load the CommentBot for auto-commenting during trading."""
    global _COMMENT_BOT
    if _COMMENT_BOT is not None:
        return _COMMENT_BOT
    try:
        from comment_bot import CommentBot
        _COMMENT_BOT = CommentBot()
    except Exception:
        _COMMENT_BOT = False  # Cache the failure to avoid retrying
    return _COMMENT_BOT


def _try_post_comment(token_mint: str, state: LifecycleState, dry_run: bool):
    """Try to post an auto-comment from a random wallet profile."""
    global _LAST_COMMENT_TIME
    cb = _get_comment_bot()
    if not cb or not state.bot_wallets:
        return

    # Find wallets with auth tokens (or use dry-run placeholder)
    eligible = [w for w in state.bot_wallets if w.get("profile")]
    if not eligible:
        return

    wallet = eligible[int(time.time() * 1000) % len(eligible)]
    comment = cb.get_random_comment()

    if dry_run:
        print(f"  [COMMENT] W{wallet['index']+1}: '{comment}' [DRY RUN]")
        _LAST_COMMENT_TIME = time.time()
        return

    # Try to post using wallet's auth token (if configured)
    auth_token = wallet.get("auth_token", "")
    if auth_token:
        try:
            result = cb.post_comment(token_mint, wallet_index=wallet["index"], comment_text=comment)
            if result.get("success"):
                print(f"  [COMMENT] W{wallet['index']+1}: '{comment}'")
            else:
                print(f"  [COMMENT FAIL] W{wallet['index']+1}: {result.get('message', 'unknown')}")
        except Exception as e:
            print(f"  [COMMENT ERROR] {e}")
    else:
        print(f"  [COMMENT] W{wallet['index']+1}: '{comment}' (no auth token)")

def _get_telegram_bot():
    """Lazy-load the Telegram bot from environment variables."""
    global _TELEGRAM_BOT
    if _TELEGRAM_BOT is not None:
        return _TELEGRAM_BOT
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id_str = os.environ.get("TELEGRAM_CHAT_ID", "")
        if token and chat_id_str:
            from telegram_bot import TelegramBot
            _TELEGRAM_BOT = TelegramBot(token=token, chat_id=int(chat_id_str))
    except Exception:
        pass
    return _TELEGRAM_BOT

def send_telegram_alert(message: str, priority: str = "info"):
    """Send an alert via Telegram if configured."""
    bot = _get_telegram_bot()
    if bot:
        try:
            bot.send_alert(message, priority)
        except Exception:
            pass

# ─── Main Entry Point ───

def main():
    load_env_file()
    parser = build_parser()
    args = parser.parse_args()

    # Merge --devnet and --network flags
    network = "devnet" if args.devnet or args.network == "devnet" else "mainnet"
    args.network = network

    rpc = args.rpc or (DEVNET_RPC if network == "devnet" else MAINNET_RPC)

    # --- Status / Inspection (standalone) ---
    if args.status or args.inspect:
        _print_status()
        if not args.inspect:
            sys.exit(0)

    if args.inspect_wallet:
        _inspect_wallet(args.inspect_wallet, rpc, network == "devnet")
        sys.exit(0)

    if args.inspect_mint:
        _inspect_token(args.inspect_mint, rpc, network == "devnet")
        sys.exit(0)

    # --- Faucet request (standalone) ---
    if args.faucet_request:
        success = request_devnet_sol(args.faucet_request, preferred_method="auto")
        sys.exit(0 if success else 1)

    # --- Resume from saved state ---
    if args.resume:
        state = LifecycleState.load()
        if not state:
            print("No saved state found. Run a phase first.")
            sys.exit(1)
        print(f"Resuming from saved state: {STATE_FILE}")
        print(f"  Token: {state.token_mint or '(not created)'}")
        print(f"  Network: {state.network}")
        print(f"  Phase: {state.current_phase}")
        for ph_name, ph in state.phases.items():
            status_val = ph.get("status", "pending") if isinstance(ph, dict) else ph.status
            print(f"    {ph_name}: {status_val}")
    else:
        state = LifecycleState(
            token_name=args.name,
            token_symbol=args.symbol,
            token_image=args.image,
            network=network,
            created_at=datetime.now(timezone.utc).isoformat(),
            budget_usd=args.budget_usd,
        )

        # Load creator wallet
        if args.wallet:
            state.creator_seed_b58 = args.wallet
            state.creator_pubkey = get_pub_from_seed(args.wallet)
            if not state.creator_pubkey:
                print("[ERROR] Invalid creator wallet seed")
                sys.exit(1)
            print(f"Creator pubkey: {state.creator_pubkey}")
        elif not args.dry_run and not args.full:
            # Auto-generate in dry-run or full mode
            print("Generating creator wallet...")
            result = call_node([
                "node", os.path.join(SCRIPT_DIR, "wallet_utils.js"), "generate",
            ], timeout=10)
            if result:
                data = json.loads(result)
                state.creator_seed_b58 = data["seed_b58"]
                state.creator_pubkey = data["pubkey"]
                print(f"Creator: {state.creator_pubkey}")
                print(f"Seed: {state.creator_seed_b58[:20]}...")
            else:
                print("[ERROR] Wallet generation failed")
                sys.exit(1)
        else:
            # Use a test seed for dry-run
            state.creator_seed_b58 = "TEST"
            state.creator_pubkey = "111111111111111111111000000000000000000000"
            print("[DRY RUN] Using test creator wallet")

    # Install emergency handler
    set_emergency_handler()

    token_mint = args.mint or state.token_mint

    # Enable stealth mode if requested
    if args.stealth:
        bot = _get_telegram_bot()
        if bot:
            bot.set_stealth_mode(True, threshold=args.stealth_threshold)
            print(f"  [STEALTH] Auto-stealth enabled (threshold: {args.stealth_threshold})")
        else:
            print("  [WARN] Stealth mode requested but Telegram bot not configured")

    # Start web dashboard (if requested)
    dashboard_thread = None
    if args.dashboard:
        try:
            import threading
            # Try web_dashboard first, fall back to web_viz
            try:
                from web_dashboard import run_dashboard
                dashboard_thread = threading.Thread(target=run_dashboard, kwargs={"port": 8765}, daemon=True)
                dashboard_thread.start()
                print(f"\n  Dashboard: http://localhost:8765 (web_dashboard)")
            except (ImportError, AttributeError):
                from web_viz import start_dashboard_background
                dashboard_thread = start_dashboard_background(port=8765)
                print(f"\n  Dashboard: http://localhost:8765 (web_viz)")
        except ImportError as e:
            print(f"  [WARN] Could not start dashboard: {e}")

    # --- Run phases ---

    try:
        if args.full:
            # Run all phases in sequence
            _run_full_lifecycle(state, token_mint, args, rpc)
        else:
            # Run individual phases
            _run_individual_phases(state, token_mint, args, rpc)

    except EmergencyExit:
        print("\n\n*** EMERGENCY EXIT TRIGGERED ***")
        send_telegram_alert("🚨 EMERGENCY EXIT TRIGGERED — selling all positions", "emergency")
        if token_mint and state.bot_wallets:
            emergency_exit(state, token_mint, dry_run=args.dry_run)
        state.save()
        print("\nState saved. Run with --resume to recover.")
        sys.exit(130)

    except KeyboardInterrupt:
        clear_emergency()  # Clear the flag so we don't immediately exit on the next SIGINT
        print("\n[INTERRUPTED] Use --emergency to force exit if needed.")
        state.save()
        print("State saved. Use --resume to continue.")
        sys.exit(130)


def _run_full_lifecycle(state: LifecycleState, token_mint: Optional[str],
                        args, rpc: str):
    """Run all phases in sequence (full lifecycle)."""
    print("\n" + "=" * 60)
    print("  PUMP.FUN FULL LIFECYCLE")
    print("=" * 60)
    print(f"  Token: {args.name} ({args.symbol})")
    print(f"  Network: {args.network}")
    print(f"  Budget: ${args.budget_usd}")
    print(f"  Wallets: {args.wallets}")
    print(f"  Dry run: {args.dry_run}")
    print("=" * 60)

    # Phase 1: Create
    state.start_phase("create")
    if not token_mint:
        token_mint = create_pumpfun_token(
            args.name, args.symbol, args.description, args.image,
            devnet=(args.network == "devnet"),
            dry_run=args.dry_run,
        )
        if token_mint:
            state.token_mint = token_mint
            state.complete_phase("create", {"mint": token_mint})
            send_telegram_alert(f"🆕 Token created: {args.name} ({args.symbol})\nMint: {token_mint[:16]}...", "info")
        else:
            state.fail_phase("create", "Token creation failed")
            if not args.dry_run:
                print("\n[FATAL] Token creation failed. Aborting.")
                sys.exit(1)
    else:
        print(f"  Using existing mint: {token_mint}")
        state.complete_phase("create", {"mint": token_mint})
        send_telegram_alert(f"🔄 Starting lifecycle for: {args.name} ({args.symbol})", "info")

    # Phase 2: Fund
    state.start_phase("fund")
    fund_result = fund_wallets(state, args.budget_usd, args.wallets,
                               dry_run=args.dry_run, max_wallet_pct=1.0)
    state.complete_phase("fund", fund_result)
    if fund_result and fund_result.get("total_distributed", 0) > 0:
        send_telegram_alert(
            f"💰 Funded {len(state.bot_wallets)} wallets\n"
            f"Budget: ${args.budget_usd} | Distributed: {fund_result['total_distributed']:.4f} SOL",
            "info"
        )

    # Phase 1.5: Warmup (if requested)
    if args.warmup:
        state.start_phase("warmup")
        warmup_result = wallet_warmup(state, token_mint, dry_run=args.dry_run)
        state.complete_phase("warmup", warmup_result)

    # Phase 3: Buy
    state.start_phase("buy")
    buy_result = initial_buy_sequence(state, token_mint,
                                      buy_pct=0.5, dry_run=args.dry_run)
    state.complete_phase("buy", buy_result)

    # Optional: RugCheck safety scan
    if args.rugcheck and token_mint:
        if not args.dry_run:
            print("\n  [RUGCHECK] Scanning token for security risks...")
            report = rugcheck_token_report(token_mint)
            print_rugcheck_report(report, verbose=args.auto)
            if report.get("ok") and report["score"] < 0.3:
                print("  [HIGH RISK] Token safety score is very low. Consider aborting.")
                if not args.auto:
                    print("  Press Enter to continue anyway, or Ctrl+C to abort: ", end="", flush=True)
                    sys.stdin.readline()
        else:
            print("\n  [DRY RUN] Would run RugCheck safety scan")

    # Phase 4: Trade (with stop-loss monitoring)
    state.start_phase("trade")
    sl_config = StopLossConfig(
        enabled=not args.stop_loss_disable,
        max_drawdown_pct=args.stop_loss_pct,
        max_loss_pct=args.stop_loss_pct,
    )
    sl_state = StopLossState()
    trade_result = run_active_trading(state, token_mint,
                                      duration_minutes=args.trade_minutes,
                                      dry_run=args.dry_run,
                                      test_mode=args.test_mode or args.dry_run,
                                      sl_config=sl_config,
                                      sl_state=sl_state,
                                      auto=args.auto,
                                      comment_enabled=args.comment,
                                      comment_interval=args.comment_interval)
    state.complete_phase("trade", trade_result)

    # Phase 5: Take Profit
    state.start_phase("take_profit")
    tp_result = take_profit(state, token_mint, dry_run=args.dry_run)
    state.complete_phase("take_profit", tp_result)
    if tp_result and tp_result.get("profitable_count", 0) > 0:
        send_telegram_alert(
            f"✅ Take-profit triggered\n"
            f"Profitable exits: {tp_result['profitable_count']}\n"
            f"SOL recovered: {tp_result.get('total_sol_recovered', 0):.4f}",
            "profit"
        )

    # Phase 6: Cash Out
    state.start_phase("cash_out")
    co_result = cash_out_all_tokens(state, token_mint, dry_run=args.dry_run)
    state.complete_phase("cash_out", co_result)
    send_telegram_alert(f"💸 Cashed out all tokens → SOL", "profit")

    # Phase 7: Close
    state.start_phase("close")
    cl_result = close_wallets(state, dry_run=args.dry_run)
    state.complete_phase("close", cl_result)
    send_telegram_alert(f"🏁 Lifecycle complete. Wallets closed and swept.", "info")

    # Summary
    print("\n" + "=" * 60)
    print("  LIFECYCLE COMPLETE — TRADE SUMMARY")
    print("=" * 60)
    if args.dry_run:
        creator_final = 99.0  # Mock balance in dry-run
        print(f"  Creator final balance: {creator_final:.6f} SOL [DRY RUN]")
    else:
        creator_final = get_balance(rpc, state.creator_pubkey) if state.creator_pubkey else 0
        print(f"  Creator final balance: {creator_final:.6f} SOL")
    print(f"  Token: {args.name} ({args.symbol}) [{token_mint[:16] if token_mint else 'N/A'}]")
    print(f"  Network: {args.network}")
    print(f"  Wallets used: {len(state.bot_wallets)}")
    print(f"  Phases: {len([p for p in state.phases.values() if isinstance(p, dict) and p.get('status') == 'completed'])}/{len(PhaseNames) if hasattr(PhaseNames, '__len__') else 7} completed")
    print("=" * 60)


def _run_individual_phases(state: LifecycleState, token_mint: Optional[str],
                           args, rpc: str):
    """Run individual phases selected by the user."""
    phase_map = []
    if args.create:
        phase_map.append("create")
    if args.fund:
        phase_map.append("fund")
    if args.buy:
        phase_map.append("buy")
    if args.trade:
        phase_map.append("trade")
    if args.take_profit:
        phase_map.append("take_profit")
    if args.cashout:
        phase_map.append("cash_out")
    if args.close:
        phase_map.append("close")
    if args.emergency:
        phase_map.append("emergency")
    if args.recover_stuck:
        phase_map.append("recover_stuck")

    if not phase_map and not args.resume:
        parser = build_parser()
        parser.print_help()
        sys.exit(1)

    for phase in phase_map:
        if check_emergency():
            print(f"\n[ABORT] Phase {phase} skipped due to emergency signal")
            break

        state.start_phase(phase)

        if phase == "create":
            if not token_mint:
                token_mint = create_pumpfun_token(
                    args.name, args.symbol, args.description, args.image,
                    devnet=(args.network == "devnet"),
                    dry_run=args.dry_run
                )
                state.token_mint = token_mint
            state.complete_phase("create", {"mint": token_mint})

        elif phase == "fund":
            result = fund_wallets(state, args.budget_usd, args.wallets,
                                  dry_run=args.dry_run)
            state.complete_phase("fund", result)

        elif phase == "buy":
            result = initial_buy_sequence(state, token_mint,
                                          dry_run=args.dry_run)
            state.complete_phase("buy", result)

        elif phase == "trade":
            sl_config = StopLossConfig(
                enabled=not args.stop_loss_disable,
                max_drawdown_pct=args.stop_loss_pct,
                max_loss_pct=args.stop_loss_pct,
            )
            sl_state = StopLossState()
            result = run_active_trading(state, token_mint,
                                        duration_minutes=args.trade_minutes,
                                        dry_run=args.dry_run,
                                        test_mode=args.dry_run,
                                        sl_config=sl_config,
                                        sl_state=sl_state,
                                        auto=args.auto,
                                        comment_enabled=args.comment,
                                        comment_interval=args.comment_interval)
            state.complete_phase("trade", result)

        elif phase == "take_profit":
            result = take_profit(state, token_mint,
                                 target_mc_x=args.target_mc or 5.0,
                                 dry_run=args.dry_run)
            state.complete_phase("take_profit", result)

        elif phase == "cash_out":
            result = cash_out_all_tokens(state, token_mint, dry_run=args.dry_run)
            state.complete_phase("cash_out", result)

        elif phase == "close":
            result = close_wallets(state, dry_run=args.dry_run)
            state.complete_phase("close", result)

        elif phase == "emergency":
            result = emergency_exit(state, token_mint, dry_run=args.dry_run)
            state.complete_phase("emergency", result)

        elif phase == "recover_stuck":
            result = detect_and_recover_stuck_wallets(state, token_mint)
            state.complete_phase("recover_stuck", result)

        state.save()


# Names for phase reference in summary
PhaseNames = ["create", "fund", "buy", "trade", "take_profit", "cash_out", "close"]


if __name__ == "__main__":
    main()
