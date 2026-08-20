#!/usr/bin/env python3
"""
Enhanced CLI — Subcommand-based interface for Pump.fun Lifecycle CLI.

Provides granular control over every part of the token lifecycle cycle.

Usage:
  python3 cli.py create --name MyToken --symbol MTK --budget-usd 20
  python3 cli.py wallet list
  python3 cli.py wallet generate --output my_wallet.json
  python3 cli.py wallet inspect <seed_or_pubkey>
  python3 cli.py trade start --mint <TOKEN> --duration 10
  python3 cli.py trade stop          (sends SIGUSR1 for graceful stop)
  python3 cli.py profile create basic --budget 20 --wallets 5
  python3 cli.py profile list
  python3 cli.py status
  python3 cli.py rugcheck <mint>
  python3 cli.py emergency --mint <TOKEN>
  python3 cli.py fund --faucet --wallet <pubkey>
  python3 cli.py lut create --mint <TOKEN>      (generate LUT instructions)

Author: Matthew A. Baker
"""

import argparse
import json
import os
import sys
import time

# ─── Constants ───
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from pumpfun_lifecycle_cli import (
    create_pumpfun_token, fund_wallets, wallet_warmup,
    initial_buy_sequence, run_active_trading, take_profit,
    cash_out_all_tokens, close_wallets, emergency_exit,
    detect_and_recover_stuck_wallets, request_devnet_sol,
    rugcheck_token_report, print_rugcheck_report,
    get_balance, get_token_accounts, _get_token_supply,
    _get_token_mc_usd, rpc_request, StopLossConfig, StopLossState,
    DEVNET_RPC, MAINNET_RPC, LAMPORTS_PER_SOL, WRAPPED_SOL_MINT,
    TOKEN_PROGRAM_ID, LifecycleState, STATE_FILE,
    set_emergency_handler, clear_emergency,
    check_emergency, get_pub_from_seed, call_node,
    _inspect_wallet,
    detect_sniping_activity, verify_liquidity_lock, analyze_holders,
)
from smart_bundler import SmartBundler, WalletInfo, BundleResult
from money_flow import (
    calculate_allocations, AllocationStrategy,
    calculate_budget_analysis, estimate_fees,
    MONEY_TIERS, get_recommended_tier, get_tier_config,
    GAS_FLOOR_SOL, TX_FEE_SOL, JITO_TIP_DEFAULT_SOL,
    SOL_PRICE_USD, BudgetAnalysis, Allocation,
    PUMP_FEE_TIERS, TAKE_PROFIT_TIERS,
)


# ─── Profile System ───

def _get_profile_dir():
    """Get the profiles directory."""
    return os.path.expanduser("~/.hermes/pumpfun_profiles")


def _ensure_profile_dir():
    os.makedirs(_get_profile_dir(), exist_ok=True)


class BotProfile:
    """A trading profile — defines wallet roles, budget split, and trade parameters."""

    def __init__(self, name, budget_usd, num_wallets, strategy,
                 slippage_bps=500, stop_loss_pct=0.30, target_mc=5.0,
                 trade_minutes=10, buy_pct=0.5, warmup=True,
                 rugcheck=True, priority_fee=500000, gas_buffer=0.001):
        self.name = name
        self.budget_usd = budget_usd
        self.num_wallets = num_wallets
        self.strategy = strategy  # "aggressive", "balanced", "conservative"
        self.slippage_bps = slippage_bps
        self.stop_loss_pct = stop_loss_pct
        self.target_mc = target_mc
        self.trade_minutes = trade_minutes
        self.buy_pct = buy_pct
        self.warmup = warmup
        self.rugcheck = rugcheck
        self.priority_fee = priority_fee
        self.gas_buffer = gas_buffer

    def to_dict(self):
        return {
            "name": self.name,
            "budget_usd": self.budget_usd,
            "num_wallets": self.num_wallets,
            "strategy": self.strategy,
            "slippage_bps": self.slippage_bps,
            "stop_loss_pct": self.stop_loss_pct,
            "target_mc": self.target_mc,
            "trade_minutes": self.trade_minutes,
            "buy_pct": self.buy_pct,
            "warmup": self.warmup,
            "rugcheck": self.rugcheck,
            "priority_fee": self.priority_fee,
            "gas_buffer": self.gas_buffer,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    @classmethod
    def create(cls, name, strategy="balanced"):
        """Create a profile with sensible defaults based on strategy."""
        defaults = {
            "aggressive": {"budget_usd": 50, "num_wallets": 8, "slippage_bps": 1000,
                           "stop_loss_pct": 0.25, "target_mc": 3.0, "trade_minutes": 5,
                           "buy_pct": 0.7, "warmup": True, "rugcheck": True},
            "balanced": {"budget_usd": 20, "num_wallets": 5, "slippage_bps": 500,
                         "stop_loss_pct": 0.30, "target_mc": 5.0, "trade_minutes": 10,
                         "buy_pct": 0.5, "warmup": True, "rugcheck": True},
            "conservative": {"budget_usd": 10, "num_wallets": 3, "slippage_bps": 300,
                             "stop_loss_pct": 0.40, "target_mc": 10.0, "trade_minutes": 20,
                             "buy_pct": 0.3, "warmup": True, "rugcheck": True},
            "testing": {"budget_usd": 3, "num_wallets": 2, "slippage_bps": 1000,
                        "stop_loss_pct": 0.50, "target_mc": 2.0, "trade_minutes": 0.1,
                        "buy_pct": 0.5, "warmup": False, "rugcheck": False},
        }
        params = defaults.get(strategy, defaults["balanced"])
        return cls(name=name, strategy=strategy, **params)

    def save(self):
        """Save profile to disk."""
        _ensure_profile_dir()
        path = os.path.join(_get_profile_dir(), f"{self.name}.json")
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path

    @classmethod
    def load(cls, name):
        """Load a profile from disk."""
        path = os.path.join(_get_profile_dir(), f"{name}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def describe(self):
        """Return a human-readable description."""
        return (f"Profile '{self.name}' ({self.strategy}): "
                f"${self.budget_usd} budget, {self.num_wallets} wallets, "
                f"SL:{self.stop_loss_pct*100:.0f}% TP:{self.target_mc}x "
                f"slippage:{self.slippage_bps}bps "
                f"warmup:{'on' if self.warmup else 'off'} "
                f"rugcheck:{'on' if self.rugcheck else 'off'}")


# ─── Subcommand: create ───

def cmd_create(args):
    """Create a new token on Pump.fun."""
    print(f"Creating token: {args.name} ({args.symbol})")
    mint = create_pumpfun_token(
        args.name, args.symbol, args.description or "",
        image_path=args.image,
        devnet=args.devnet,
        dry_run=args.dry_run,
    )
    if mint:
        print(f"✓ Token created: {mint}")
        print(f"  Mint: {mint[:16]}...{mint[-8:]}")
    else:
        print("✗ Token creation failed")
        sys.exit(1)


# ─── Subcommand: wallet ───

def cmd_wallet(args):
    """Manage wallets."""
    if args.wallet_cmd == "generate":
        _wallet_generate(args)
    elif args.wallet_cmd == "list":
        _wallet_list(args)
    elif args.wallet_cmd == "inspect":
        _wallet_inspect(args)
    elif args.wallet_cmd == "balance":
        _wallet_balance(args)
    else:
        print(f"Unknown wallet command: {args.wallet_cmd}")


def _wallet_generate(args):
    """Generate a new wallet."""
    from pumpfun_lifecycle_cli import call_node
    result = call_node([
        "node", os.path.join(SCRIPT_DIR, "wallet_utils.js"), "generate",
    ], timeout=10)
    if result:
        data = json.loads(result)
        wallet = {
            "pubkey": data["pubkey"],
            "seed_b58": data["seed_b58"],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        print(f"✓ Wallet generated")
        print(f"  Pubkey: {wallet['pubkey']}")
        print(f"  Seed: {wallet['seed_b58'][:20]}...")
        if args.output:
            with open(args.output, "w") as f:
                json.dump(wallet, f, indent=2)
            print(f"  Saved to: {args.output}")
    else:
        # Fallback: generate using Python (Ed25519)
        try:
            from nacl.signing import SigningKey
            import base58
            seed = bytes.fromhex(__import__("secrets").token_hex(32))
            signing_key = SigningKey(seed)
            verify_key = signing_key.verify_key
            pubkey = base58.b58encode(bytes(verify_key)).decode()
            # Note: This is the ed25519 key, not the Solana pubkey (which is
            # derived differently). For production, use the actual Solana derivation.
            print(f"  [WARN] Using fallback key generation (Python)")
            print(f"  Pubkey (raw ed25519): {pubkey[:32]}...")
        except ImportError:
            # Generate a random base58 string as placeholder
            import secrets, base64
            raw = secrets.token_bytes(32)
            fake_pub = base64.b58encode(raw).decode()[:44] if hasattr(base64, 'b58encode') else \
                       base64.b64encode(raw).decode()[:44]
            print(f"  [WARN] No crypto libs available. Using placeholder.")
            print(f"  Placeholder pubkey: {fake_pub}")
            print(f"  [NOTE] Install PyNaCl or use wallet_utils.js for real keys")
        print("✗ Wallet generation failed (no Node.js or crypto libs)")
        sys.exit(1)


def _wallet_list(args):
    """List wallets from saved state."""
    from pumpfun_lifecycle_cli import LifecycleState, STATE_FILE

    if not os.path.exists(STATE_FILE):
        print("No saved state found. Run a lifecycle phase first.")
        return

    state = LifecycleState.load()
    if not state or not state.bot_wallets:
        print("No wallets in saved state.")
        return

    print(f"Wallets in saved state ({len(state.bot_wallets)}):")
    for w in state.bot_wallets:
        balance = w.get("current_sol", 0)
        tokens = w.get("tokens_held", 0)
        role = w.get("role", "bot")
        print(f"  W{w.get('index', 0)+1} [{role}] {w['pubkey'][:16]}...{w['pubkey'][-8:]}")
        print(f"    SOL: {balance:.6f} | Tokens: {tokens:.2f}")


def _wallet_inspect(args):
    """Inspect a specific wallet."""
    from pumpfun_lifecycle_cli import _inspect_wallet
    rpc = MAINNET_RPC if args.mainnet else DEVNET_RPC
    _inspect_wallet(args.wallet, rpc, is_devnet=not args.mainnet)


def _wallet_balance(args):
    """Check a wallet's balance."""
    from pumpfun_lifecycle_cli import get_pub_from_seed
    rpc = MAINNET_RPC if args.mainnet else DEVNET_RPC

    if args.wallet.startswith("[") or len(args.wallet) > 50:
        # Seed phrase
        pubkey = get_pub_from_seed(args.wallet)
        if not pubkey:
            print("ERROR: Could not derive pubkey from seed")
            sys.exit(1)
    else:
        pubkey = args.wallet

    balance = get_balance(rpc, pubkey)
    print(f"Wallet: {pubkey}")
    print(f"Balance: {balance:.6f} SOL (${balance * 150:.2f})")
    if balance < 0.001:
        print(f"  ⚠️  Below gas floor (0.001 SOL)")


# ─── Subcommand: trade ───

def cmd_trade(args):
    """Manage active trading."""
    if args.trade_cmd == "start":
        _trade_start(args)
    elif args.trade_cmd == "stop":
        _trade_stop(args)
    elif args.trade_cmd == "status":
        _trade_status(args)
    elif args.trade_cmd == "take-profit":
        _trade_take_profit(args)
    elif args.trade_cmd == "cash-out":
        _trade_cashout(args)
    else:
        print(f"Unknown trade command: {args.trade_cmd}")


def _trade_start(args):
    """Start active trading."""
    from pumpfun_lifecycle_cli import (
        LifecycleState, set_emergency_handler, run_active_trading,
        DEVNET_RPC, MAINNET_RPC, STATE_FILE,
    )

    rpc = args.rpc or (DEVNET_RPC if args.devnet else MAINNET_RPC)
    state = LifecycleState.load()
    if not state or not state.token_mint:
        print("ERROR: No saved state with token mint found.")
        print("  Run 'cli.py create' first, or use --mint to specify")
        sys.exit(1)

    set_emergency_handler()

    sl_config = StopLossConfig(
        enabled=not args.no_stop_loss,
        max_drawdown_pct=args.stop_loss_pct,
        max_loss_pct=args.stop_loss_pct,
    )
    sl_state = StopLossState()

    result = run_active_trading(
        state, state.token_mint,
        duration_minutes=args.duration,
        dry_run=args.dry_run,
        test_mode=args.test_mode,
        sl_config=sl_config,
        sl_state=sl_state,
        auto=args.auto,
    )
    print(f"Trading complete: {result}")


def _trade_stop(args):
    """Send emergency stop signal via a flag file."""
    stop_file = os.path.join(SCRIPT_DIR, ".stop_trading")
    with open(stop_file, "w") as f:
        f.write(json.dumps({"stop_requested": True, "timestamp": time.time()}))
    print("✓ Stop signal sent. Active trading will stop at next checkpoint.")
    print(f"  Stop file: {stop_file}")


def _trade_status(args):
    """Show trading status."""
    _print_status_short()


def _trade_take_profit(args):
    """Trigger take-profit at a specific MC multiplier."""
    from pumpfun_lifecycle_cli import (
        LifecycleState, take_profit, DEVNET_RPC, MAINNET_RPC
    )
    rpc = DEVNET_RPC if args.devnet else MAINNET_RPC
    state = LifecycleState.load()
    if not state or not state.token_mint:
        print("ERROR: No saved state with token mint found.")
        sys.exit(1)

    result = take_profit(state, state.token_mint,
                         target_mc_x=args.multiplier, dry_run=args.dry_run)
    print(f"Take-profit result: {result}")


def _trade_cashout(args):
    """Cash out all tokens to SOL."""
    from pumpfun_lifecycle_cli import (
        LifecycleState, cash_out_all_tokens
    )
    state = LifecycleState.load()
    if not state or not state.token_mint:
        print("ERROR: No saved state with token mint found.")
        sys.exit(1)

    result = cash_out_all_tokens(state, state.token_mint, dry_run=args.dry_run)
    print(f"Cash-out result: {result}")


# ─── Subcommand: profile ───

def cmd_profile(args):
    """Manage trading profiles."""
    if args.profile_cmd == "create":
        _profile_create(args)
    elif args.profile_cmd == "list":
        _profile_list(args)
    elif args.profile_cmd == "show":
        _profile_show(args)
    elif args.profile_cmd == "delete":
        _profile_delete(args)
    elif args.profile_cmd == "apply":
        _profile_apply(args)
    else:
        print(f"Unknown profile command: {args.profile_cmd}")


def _profile_create(args):
    """Create a new trading profile."""
    profile = BotProfile.create(name=args.name, strategy=args.strategy)
    if args.budget:
        profile.budget_usd = args.budget
    if args.wallets:
        profile.num_wallets = args.wallets
    if args.stop_loss:
        profile.stop_loss_pct = args.stop_loss
    if args.target_mc:
        profile.target_mc = args.target_mc
    if args.trade_minutes:
        profile.trade_minutes = args.trade_minutes

    path = profile.save()
    print(f"✓ Profile created: {args.name}")
    print(f"  {profile.describe()}")
    print(f"  Saved to: {path}")


def _profile_list(args):
    """List all profiles."""
    _ensure_profile_dir()
    profile_dir = _get_profile_dir()
    files = [f for f in os.listdir(profile_dir) if f.endswith(".json")]
    if not files:
        print("No profiles found. Create one with 'cli.py profile create <name>'")
        return

    print("Saved Profiles:")
    print(f"  {'Name':<20} {'Strategy':<15} {'Budget':<10} {'Wallets':<8} {'SL%':<6} {'TPx':<6}")
    print(f"  {'─'*20} {'─'*15} {'─'*10} {'─'*8} {'─'*6} {'─'*6}")
    for f in sorted(files):
        name = f[:-5]  # Remove .json
        try:
            profile = BotProfile.load(name)
            print(f"  {profile.name:<20} {profile.strategy:<15} ${profile.budget_usd:<9} {profile.num_wallets:<8} {profile.stop_loss_pct*100:<5.0f}% {profile.target_mc}x")
        except Exception as e:
            print(f"  {name:<20} [ERROR loading: {e}]")


def _profile_show(args):
    """Show a specific profile."""
    profile = BotProfile.load(args.name)
    if not profile:
        print(f"Profile '{args.name}' not found")
        sys.exit(1)
    print(json.dumps(profile.to_dict(), indent=2))


def _profile_delete(args):
    """Delete a profile."""
    path = os.path.join(_get_profile_dir(), f"{args.name}.json")
    if os.path.exists(path):
        os.remove(path)
        print(f"✓ Profile '{args.name}' deleted")
    else:
        print(f"Profile '{args.name}' not found")


def _profile_apply(args):
    """Apply a profile to the current state file."""
    profile = BotProfile.load(args.name)
    if not profile:
        print(f"Profile '{args.name}' not found")
        sys.exit(1)

    from pumpfun_lifecycle_cli import LifecycleState, STATE_FILE
    state = LifecycleState.load()
    if not state:
        state = LifecycleState(token_name="Token", token_symbol="TKN",
                               network="devnet", budget_usd=profile.budget_usd)

    state.budget_usd = profile.budget_usd
    state.num_wallets = profile.num_wallets
    state.stop_loss_pct = profile.stop_loss_pct
    state.target_mc_mult = profile.target_mc
    state.trade_minutes = profile.trade_minutes
    state.buy_pct = profile.buy_pct
    state.warmup_enabled = profile.warmup
    state.rugcheck_enabled = profile.rugcheck
    state.slippage_bps = profile.slippage_bps
    state.priority_fee = profile.priority_fee
    state.gas_buffer = profile.gas_buffer

    state.save()
    print(f"✓ Profile '{args.name}' applied to state")
    print(f"  {profile.describe()}")


def _profile_gen(args):
    """Generate human-like wallet profiles."""
    try:
        from profile_gen import generate_profiles_for_bundle, get_profile_summary
    except ImportError:
        print("ERROR: profile_gen.py not available")
        sys.exit(1)

    profiles = generate_profiles_for_bundle(num_wallets=args.count, seed=args.seed)
    print(f"✓ Generated {args.count} wallet profiles")
    print(get_profile_summary(profiles))
    print(f"\nWallet Profiles:")
    for i, p in enumerate(profiles["profiles"]):
        pattern = p.get("activity_pattern", {})
        pattern_str = pattern.get("pattern", "unknown") if isinstance(pattern, dict) else str(pattern)
        tz = pattern.get("timezone", "UTC") if isinstance(pattern, dict) else "UTC"
        print(f"  W{i+1}: @{p['username']} ({p['trading_style']}) — {pattern_str}, {tz}")
        print(f"       Buy prob: {p['buy_probability']}, Sell prob: {p['sell_probability']}, Avg trade: {p['avg_trade_size_sol']} SOL")


# ─── Subcommand: status ───

def _print_status_short():
    """Print concise status."""
    from pumpfun_lifecycle_cli import LifecycleState, STATE_FILE
    if not os.path.exists(STATE_FILE):
        print("No saved state.")
        return
    state = LifecycleState.load()
    if not state:
        print("Could not load state.")
        return

    print(f"Token: {state.token_name} ({state.token_symbol})")
    print(f"Mint: {state.token_mint or '(not created)'}")
    print(f"Network: {state.network}")
    print(f"Current Phase: {state.current_phase}")
    print(f"Wallets: {len(state.bot_wallets)}")
    print(f"Budget: ${state.budget_usd}")

    completed = sum(1 for p in state.phases.values()
                    if isinstance(p, dict) and p.get("status") == "completed")
    total = len(state.phases)
    print(f"Phases: {completed}/{total} completed")


def cmd_status(args):
    """Show current lifecycle status."""
    from pumpfun_lifecycle_cli import _print_status
    _print_status()


# ─── Subcommand: rugcheck ───

def cmd_rugcheck(args):
    """Run RugCheck safety scan on a token."""
    from pumpfun_lifecycle_cli import DEVNET_RPC, MAINNET_RPC
    rpc = args.rpc or (DEVNET_RPC if args.devnet else MAINNET_RPC)
    report = rugcheck_token_report(args.mint)
    print_rugcheck_report(report, verbose=True)


# ─── Subcommand: research ───

def _cmd_research(args):
    """Run comprehensive token research: sniping detection, liquidity lock, holder analysis."""
    dry_run = getattr(args, 'dry_run', False)

    print(f"🔍 Researching token: {args.mint}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("-" * 60)

    if args.sniping:
        print("\n[1/3] Sniper Activity Detection")
        whitelist = [w.strip() for w in args.whitelist.split(",") if w.strip()] if args.whitelist else []
        result = detect_sniping_activity(args.mint, lookback_secs=args.lookback,
                                          whitelisted_wallets=whitelist, dry_run=dry_run)
        if result.get("detected"):
            print(f"  ⚠️  SNIPING DETECTED — confidence: {result.get('confidence', 0):.1%}")
            for w in result.get("suspicious_wallets", []):
                print(f"    {w['wallet']}: {w['hold_duration']} hold (confidence: {w['confidence']:.1%})")
        else:
            print(f"  ✓ No sniping detected ({result.get('recent_tx_count', 0)} recent txs)")
            if result.get("note"):
                print(f"  Note: {result['note']}")

    if args.liquidity:
        print("\n[2/3] Liquidity Lock Verification")
        result = verify_liquidity_lock(args.mint, dry_run=dry_run)
        if result.get("locked"):
            exp = result.get("lock_expiry", 0)
            exp_str = f" (expires {exp})" if exp else " (permanent)"
            print(f"  ✓ Liquidity is LOCKED via {result.get('provider', 'unknown')}{exp_str}")
            print(f"  LP Token: {result.get('lp_token', 'unknown')}")
            print(f"  Total LP: ${result.get('total_lp_usd', 0):,.0f}")
        else:
            print(f"  ✗ Liquidity NOT locked!")

    if args.holders:
        print(f"\n[3/3] Holder Analysis (top {args.top_n})")
        result = analyze_holders(args.mint, top_n=args.top_n, dry_run=dry_run)
        if "error" in result:
            print(f"  ⚠️  {result['error']}")
        else:
            conc = result.get("concentration", {})
            print(f"  Top 10 holders: {conc.get('top_10_pct', 0):.1f}% of supply")
            print(f"  Top 50 holders: {conc.get('top_50_pct', 0):.1f}% of supply")
            print(f"  Gini coefficient: {conc.get('gini', 0):.3f} (0=perfect equality, 1=maximum inequality)")

            bundlers = result.get("bundlers", [])
            if bundlers:
                print(f"\n  ⚠️  Potential bundlers detected ({len(bundlers)}):")
                for b in bundlers:
                    print(f"    {b['wallet_count']} wallets, {b['total_pct']:.1f}% supply{', created ' + str(b.get('timestamp', '')) if b.get('timestamp') else ''}")
            else:
                print(f"\n  ✓ No potential bundlers detected")

            snipers = result.get("sniper_holders", [])
            if snipers:
                print(f"\n  ⚠️  Sniper holders ({len(snipers)}):")
                for s in snipers:
                    print(f"    {s['wallet']}: {s['pct']:.1f}%")

            top = result.get("top_holders", [])
            if top:
                print(f"\n  Top 5 holders:")
                for h in top[:5]:
                    print(f"    #{h['rank']}: {h['pct']:.1f}% ({h.get('label', '')})")

            print(f"\n  Total holders: {result.get('total_holders', 'N/A')}")
            print(f"  Total supply: {result.get('total_supply', 'N/A')}")

    print("-" * 60)
    print("✅ Research complete")


# ─── Subcommand: emergency ───

def cmd_emergency(args):
    """Emergency exit — sell everything, sweep SOL."""
    from pumpfun_lifecycle_cli import LifecycleState, emergency_exit, DEVNET_RPC, MAINNET_RPC
    rpc = args.rpc or (DEVNET_RPC if args.devnet else MAINNET_RPC)
    state = LifecycleState.load()
    if not state or not state.token_mint:
        print("ERROR: No saved state with token mint found.")
        sys.exit(1)

    print("=" * 60)
    print("  EMERGENCY EXIT")
    print("  This will sell ALL tokens and sweep ALL SOL.")
    print("=" * 60)
    if not args.yes:
        confirm = input("Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            print("Aborted.")
            sys.exit(0)

    result = emergency_exit(state, state.token_mint, dry_run=args.dry_run)
    print(f"\nEmergency exit result: {result}")


# ─── Subcommand: fund ───

def cmd_fund(args):
    """Fund wallets — request devnet SOL or distribute from creator."""
    from pumpfun_lifecycle_cli import (
        LifecycleState, fund_wallets, request_devnet_sol,
        DEVNET_RPC, MAINNET_RPC
    )

    rpc = DEVNET_RPC if args.devnet else MAINNET_RPC

    if args.faucet:
        if not args.wallet:
            print("ERROR: --wallet required with --faucet")
            sys.exit(1)
        success = request_devnet_sol(args.wallet, preferred_method=args.faucet_method)
        if success:
            print(f"✓ SOL requested for {args.wallet[:16]}...")
        else:
            print(f"✗ Failed to request SOL for {args.wallet[:16]}...")
        return

    state = LifecycleState.load()
    if not state:
        state = LifecycleState(token_name=args.name or "Token", token_symbol=args.symbol or "TKN",
                               network="devnet", budget_usd=args.budget_usd)

    # Set test seed for dry-run
    if args.dry_run and not state.creator_seed_b58:
        state.creator_seed_b58 = "TEST"
        state.creator_pubkey = "111111111111111111111000000000000000000000"
        print("[DRY RUN] Using test creator wallet")

    result = fund_wallets(state, args.budget_usd, args.wallets,
                          dry_run=args.dry_run, max_wallet_pct=1.0)
    print(f"Funding complete: {len(state.bot_wallets)} wallets")
    for w in state.bot_wallets:
        print(f"  W{w['index']+1}: {w['allocated_sol']:.6f} SOL (role: {w.get('role', 'bot')})")
    state.save()


# ─── Subcommand: lut ───

def cmd_lut(args):
    """Manage Address Lookup Tables (LUTs) for compressed transactions."""
    if args.lut_cmd == "create":
        _lut_create(args)
    elif args.lut_cmd == "list":
        _lut_list(args)
    elif args.lut_cmd == "inspect":
        _lut_inspect(args)
    else:
        print(f"Unknown LUT command: {args.lut_cmd}")


def _lut_create(args):
    """
    Generate LUT creation instructions.

    LUTs allow storing addresses off-chain, referenced by a 32-byte account key.
    This reduces transaction size and enables larger transactions.
    """
    print("=" * 60)
    print("  ADDRESS LOOKUP TABLE (LUT) CREATION")
    print("=" * 60)
    print()
    print("  LUTs store frequently used addresses off-chain to reduce tx size.")
    print("  For Pump.fun lifecycle, create LUTs with:")
    print("    - Token program ID")
    print("    - Token mint")
    print("    - Associated token program")
    print("    - System program")
    print("    - All bot wallet pubkeys")
    print("    - Pump.fun program ID")
    print()
    print("  LUT Address (placeholder for {mint}):")
    print(f"    LUT will store: {args.mint}")
    print(f"    Bot wallets to include: {args.wallets or 5}")
    print()
    print("  Instructions:")
    print("  1. Use Solana CLI: spl-token address-lookup-table create")
    print("     solana address-lookup-table create --authority <wallet> --payer <wallet>")
    print("  2. Extend LUT with addresses:")
    print("     spl-address-lookup-table extend --address <mint> --table-account-key <lut>")
    print("  3. Use LUT in transactions by including the lookup table account")
    print()
    print("  NOTE: LUT creation requires Solana CLI or web3.js")
    print("  Estimated cost: ~0.001-0.003 SOL for creation + extension")


def _lut_list(args):
    """List available LUTs."""
    from pumpfun_lifecycle_cli import LifecycleState
    state = LifecycleState.load()
    if state and state.lut_address:
        print(f"LUT Address: {state.lut_address}")
        print(f"  Mint: {state.token_mint}")
    else:
        print("No LUT configured. Use 'cli.py lut create --mint <TOKEN>'")


def _lut_inspect(args):
    """Inspect a LUT."""
    from pumpfun_lifecycle_cli import rpc_request, DEVNET_RPC, MAINNET_RPC
    rpc = args.rpc or (DEVNET_RPC if args.devnet else MAINNET_RPC)

    result = rpc_request(rpc, "getAddressLookupTable", [args.lut_address])
    if result and result.get("result") and result["result"].get("value"):
        lut = result["result"]["value"]
        print(f"LUT: {args.lut_address}")
        print(f"  Authority: {lut.get('authority', 'N/A')}")
        print(f"  Lookup info: {len(lut.get('addresses', []))} addresses stored")
    else:
        print(f"  LUT not found or inactive")


# ─── Token Utilities ───
def _cmd_token_resolve(args):
    """Resolve a ticker symbol to a mint address."""
    from config import resolve_token_mint, TOKEN_MINT_LOOKUP
    mint = resolve_token_mint(args.symbol)
    print(f"Symbol: {args.symbol.upper()}")
    print(f"Mint:   {mint}")
    if args.symbol.upper() in TOKEN_MINT_LOOKUP:
        print(f"(Known token: mapped to mint address)")
    else:
        print(f"(Not in lookup — treated as direct mint address)")
    if args.output:
        with open(args.output, 'w') as f:
            f.write(mint)
        print(f"Written to: {args.output}")


# ─── Preset Commands ───
def _cmd_preset_list(args):
    """List all available Three Commas preset profiles."""
    from config import list_presets, THREE_COMMAS_PRESETS
    print("Three Commas Preset Profiles:")
    print("=" * 70)
    for name, cfg in THREE_COMMAS_PRESETS.items():
        print(f"  {name}")
        print(f"    Wallets: {cfg['num_wallets']} | Strategy: {cfg['strategy']}")
        print(f"    Take-profit: {cfg['take_profit_x']}x ({cfg['take_profit_pct']}%)")
        print(f"    Stop-loss: {cfg['stop_loss_pct']}%")
        print()


def _cmd_preset_show(args):
    """Show details of a specific preset."""
    from config import get_preset_config
    cfg = get_preset_config(args.name)
    print(f"Preset: {args.name}")
    print(f"  Description: {cfg.get('description', 'N/A')}")
    print(f"  Wallets: {cfg['num_wallets']}")
    print(f"  Strategy: {cfg['strategy']}")
    print(f"  Take-profit: {cfg['take_profit_x']}x ({cfg['take_profit_pct']}%)")
    print(f"  Stop-loss: {cfg['stop_loss_pct']}%")
    print(f"  Trade size: ${cfg['trade_size_usd']}")


def _cmd_preset_recommend(args):
    """Recommend a preset based on budget."""
    from config import THREE_COMMAS_PRESETS
    budget = args.budget
    best_preset = None
    best_wallets = 0
    for name, cfg in THREE_COMMAS_PRESETS.items():
        est_sol = cfg['num_wallets'] * cfg['trade_size_usd'] / 150  # rough estimate at $150/SOL
        if est_sol <= budget / 150 and cfg['num_wallets'] > best_wallets:
            best_preset = name
            best_wallets = cfg['num_wallets']
    if best_preset:
        print(f"Recommended for ${budget} budget: {best_preset}")
        _cmd_preset_show(argparse.Namespace(name=best_preset))
    else:
        print(f"No preset fits ${budget} budget. Minimum: Conservative DCA (${THREE_COMMAS_PRESETS['Conservative DCA']['num_wallets'] * THREE_COMMAS_PRESETS['Conservative DCA']['trade_size_usd']:.2f})")


# ─── Comment Bot Commands ───
def _cmd_comment_cost(args):
    """Estimate the cost of a comment campaign."""
    from comment_bot import estimate_comment_cost
    cost = estimate_comment_cost(args.comments, args.wallets)
    print(f"Comment Campaign Cost Estimate:")
    print(f"  Comments: {cost['num_comments']}")
    print(f"  Wallets: {cost['num_wallets']}")
    print(f"  Cost: ${cost['total_cost_usd']:.2f} ({cost['total_cost_sol']:.4f} SOL)")
    print(f"  Auth cost: ${cost['auth_cost_usd']:.2f}")
    print(f"  TX cost: ${cost['tx_cost_usd']:.2f} (API-based = FREE)")
    print(f"  Within $20 budget: {cost['within_20_budget']}")


# ─── Web Dashboard Command ───
def _cmd_dashboard(args):
    """Start the web dashboard server."""
    from web_viz import start_server
    port = args.port
    print(f"Starting dashboard on http://localhost:{port}")
    print(f"Press Ctrl+C to stop")
    try:
        start_server(port=port, background=False)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


# ─── Budget Tier Commands ───
def _cmd_tier_list(args):
    """List all budget tiers with costs."""
    from config import print_tier_summary, MONEY_TIERS, calculate_tier_cost
    print_tier_summary()
    print()
    for name, cfg in MONEY_TIERS.items():
        cost = calculate_tier_cost(name)
        print(f"{name}: ${cfg['budget_usd']} → {cfg['num_wallets']} wallets, cost=${cost['total_usd']:.2f}")


def _cmd_tier_calc(args):
    """Calculate the cost for a specific tier."""
    from config import calculate_tier_cost, MONEY_TIERS
    tier = args.name.upper()
    if tier not in MONEY_TIERS:
        print(f"Unknown tier: {args.name}")
        print(f"Available: {', '.join(MONEY_TIERS.keys())}")
        return
    cost = calculate_tier_cost(tier)
    print(f"Tier: {tier}")
    print(f"  Budget: ${MONEY_TIERS[tier]['budget_usd']}")
    print(f"  Wallets: {MONEY_TIERS[tier]['num_wallets']}")
    print(f"  Wallet creation: {cost['wallet_creation_sol']:.4f} SOL")
    print(f"  TX fees: {cost['tx_fees_sol']:.4f} SOL")
    print(f"  Buy capital: {cost['buy_capital_sol']:.4f} SOL")
    print(f"  Total: {cost['total_sol']:.4f} SOL (${cost['total_usd']:.2f})")
    print(f"  Within $25 budget: {cost['within_budget']}")


def _cmd_tier_recommend(args):
    """Recommend a tier based on budget."""
    from config import get_recommended_tier, MONEY_TIERS, calculate_tier_cost
    tier = get_recommended_tier(args.budget)
    print(f"Budget: ${args.budget}")
    print(f"Recommended tier: {tier}")
    cost = calculate_tier_cost(tier)
    print(f"  Wallets: {MONEY_TIERS[tier]['num_wallets']}")
    print(f"  Total cost: {cost['total_sol']:.4f} SOL (${cost['total_usd']:.2f})")


# ─── Parser ───

def build_parser() -> argparse.ArgumentParser:
    """Build the subcommand-based CLI parser."""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Enhanced Pump.fun Lifecycle CLI — subcommand interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cli.py create --name MyToken --symbol MTK
  python3 cli.py wallet generate --output wallet.json
  python3 cli.py wallet list
  python3 cli.py profile create my_bot --strategy aggressive
  python3 cli.py profile list
  python3 cli.py trade start --mint <TOKEN_MINT> --duration 10
  python3 cli.py trade take-profit --multiplier 5
  python3 cli.py status
  python3 cli.py rugcheck <TOKEN_MINT>
  python3 cli.py emergency --mint <TOKEN_MINT>
  python3 cli.py fund --faucet --wallet <PUBKEY>
  python3 cli.py lut create --mint <TOKEN_MINT>
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # create
    p_create = subparsers.add_parser("create", help="Create a new token on Pump.fun")
    p_create.add_argument("--name", required=True, help="Token name")
    p_create.add_argument("--symbol", required=True, help="Token symbol")
    p_create.add_argument("--description", default="")
    p_create.add_argument("--image", default=None)
    p_create.add_argument("--devnet", action="store_true")
    p_create.add_argument("--dry-run", action="store_true", default=False)
    p_create.set_defaults(func=cmd_create)

    # wallet
    p_wallet = subparsers.add_parser("wallet", help="Manage wallets")
    wp = p_wallet.add_subparsers(dest="wallet_cmd")
    p_gen = wp.add_parser("generate", help="Generate a new wallet")
    p_gen.add_argument("--output", "-o", default=None, help="Output file for wallet JSON")
    p_gen.set_defaults(func=lambda a: _wallet_generate(a))
    p_list = wp.add_parser("list", help="List wallets from saved state")
    p_list.set_defaults(func=lambda a: _wallet_list(a))
    p_inspect = wp.add_parser("inspect", help="Inspect a wallet")
    p_inspect.add_argument("wallet", help="Wallet pubkey or seed phrase")
    p_inspect.add_argument("--mainnet", action="store_true")
    p_inspect.set_defaults(func=lambda a: _wallet_inspect(a))
    p_bal = wp.add_parser("balance", help="Check wallet balance")
    p_bal.add_argument("wallet", help="Wallet pubkey or seed phrase")
    p_bal.add_argument("--mainnet", action="store_true")
    p_bal.set_defaults(func=lambda a: _wallet_balance(a))

    # profile
    p_profile = subparsers.add_parser("profile", help="Manage trading profiles")
    pp = p_profile.add_subparsers(dest="profile_cmd")
    p_pc = pp.add_parser("create", help="Create a new profile")
    p_pc.add_argument("name", help="Profile name")
    p_pc.add_argument("--strategy", choices=["aggressive", "balanced", "conservative", "testing"],
                      default="balanced", help="Strategy preset")
    p_pc.add_argument("--budget", type=float, help="Budget in USD")
    p_pc.add_argument("--wallets", type=int, help="Number of bot wallets")
    p_pc.add_argument("--stop-loss", type=float, help="Stop-loss percentage (e.g. 0.3)")
    p_pc.add_argument("--target-mc", type=float, help="Take-profit MC multiplier")
    p_pc.add_argument("--trade-minutes", type=float, help="Trading duration in minutes")
    p_pc.set_defaults(func=lambda a: _profile_create(a))
    p_pl = pp.add_parser("list", help="List all profiles")
    p_pl.set_defaults(func=lambda a: _profile_list(a))
    p_ps = pp.add_parser("show", help="Show profile details")
    p_ps.add_argument("name", help="Profile name")
    p_ps.set_defaults(func=lambda a: _profile_show(a))
    p_pd = pp.add_parser("delete", help="Delete a profile")
    p_pd.add_argument("name", help="Profile name")
    p_pd.set_defaults(func=lambda a: _profile_delete(a))
    p_pa = pp.add_parser("apply", help="Apply profile to current state")
    p_pa.add_argument("name", help="Profile name")
    p_pa.set_defaults(func=lambda a: _profile_apply(a))
    p_pgen = pp.add_parser("gen", aliases=["generate"], help="Generate human-like wallet profiles")
    p_pgen.add_argument("--count", type=int, default=5, help="Number of profiles to generate")
    p_pgen.add_argument("--seed", type=int, default=None, help="Random seed for reproducible profiles")
    p_pgen.set_defaults(func=lambda a: _profile_gen(a))

    # trade
    p_trade = subparsers.add_parser("trade", help="Manage active trading")
    tp = p_trade.add_subparsers(dest="trade_cmd")
    p_ts = tp.add_parser("start", help="Start active trading")
    p_ts.add_argument("--mint", required=True, help="Token mint to trade")
    p_ts.add_argument("--duration", type=float, default=10, help="Duration in minutes")
    p_ts.add_argument("--devnet", action="store_true", default=True)
    p_ts.add_argument("--dry-run", action="store_true", default=False)
    p_ts.add_argument("--test-mode", action="store_true", default=False)
    p_ts.add_argument("--stop-loss-pct", type=float, default=0.30)
    p_ts.add_argument("--no-stop-loss", action="store_true", default=False)
    p_ts.add_argument("--auto", action="store_true", default=True)
    p_ts.set_defaults(func=lambda a: _trade_start(a))
    p_sp = tp.add_parser("stop", help="Send emergency stop signal")
    p_sp.set_defaults(func=lambda a: _trade_stop(a))
    p_st = tp.add_parser("status", help="Show trading status")
    p_st.set_defaults(func=lambda a: _trade_status(a))
    p_tp = tp.add_parser("take-profit", help="Trigger take-profit")
    p_tp.add_argument("--multiplier", type=float, default=5.0)
    p_tp.add_argument("--dry-run", action="store_true", default=False)
    p_tp.set_defaults(func=lambda a: _trade_take_profit(a))
    p_co = tp.add_parser("cash-out", help="Cash out all tokens to SOL")
    p_co.add_argument("--dry-run", action="store_true", default=False)
    p_co.set_defaults(func=lambda a: _trade_cashout(a))

    # status
    p_st2 = subparsers.add_parser("status", help="Show current lifecycle status")
    p_st2.set_defaults(func=lambda a: cmd_status(a))

    # rugcheck
    p_rc = subparsers.add_parser("rugcheck", help="Run RugCheck safety scan")
    p_rc.add_argument("mint", help="Token mint to scan")
    p_rc.add_argument("--devnet", action="store_true", default=True)
    p_rc.add_argument("--rpc", default=None)
    p_rc.set_defaults(func=lambda a: cmd_rugcheck(a))

    # token (resolve symbol to mint)
    p_tok = subparsers.add_parser("token", help="Token utilities (symbol lookup, presets)")
    tp2 = p_tok.add_subparsers(dest="token_cmd")
    p_resolve = tp2.add_parser("resolve", help="Resolve ticker to mint address")
    p_resolve.add_argument("symbol", help="Ticker symbol (e.g., BONK, SOL)")
    p_resolve.add_argument("--output", "-o", default=None, help="Write resolved to file")
    p_resolve.set_defaults(func=lambda a: _cmd_token_resolve(a))

    # preset (Three Commas profiles)
    p_preset = subparsers.add_parser("preset", help="Three Commas preset profiles")
    pp2 = p_preset.add_subparsers(dest="preset_cmd")
    p_plist = pp2.add_parser("list", help="List all available presets")
    p_plist.set_defaults(func=lambda a: _cmd_preset_list(a))
    p_pshow = pp2.add_parser("show", help="Show a preset configuration")
    p_pshow.add_argument("name", help="Preset name (e.g., Aggressive Pump)")
    p_pshow.set_defaults(func=lambda a: _cmd_preset_show(a))
    p_precon = pp2.add_parser("recommend", help="Recommend a preset based on budget")
    p_precon.add_argument("--budget", type=float, default=20.0, help="Budget in USD")
    p_precon.set_defaults(func=lambda a: _cmd_preset_recommend(a))

    # comment (comment bot)
    p_comment = subparsers.add_parser("comment", help="Comment bot utilities")
    p_cs = p_comment.add_subparsers(dest="comment_cmd")
    p_cost = p_cs.add_parser("cost", help="Estimate comment campaign cost")
    p_cost.add_argument("--comments", type=int, default=50)
    p_cost.add_argument("--wallets", type=int, default=5)
    p_cost.set_defaults(func=lambda a: _cmd_comment_cost(a))

    # web dashboard
    p_dash = subparsers.add_parser("dashboard", help="Start web dashboard")
    p_dash.add_argument("--port", type=int, default=8765)
    p_dash.set_defaults(func=lambda a: _cmd_dashboard(a))

    # tier (budget tiers)
    p_tier = subparsers.add_parser("tier", help="Budget tier analysis")
    p_ts2 = p_tier.add_subparsers(dest="tier_cmd")
    p_tlist = p_ts2.add_parser("list", help="List all budget tiers")
    p_tlist.set_defaults(func=lambda a: _cmd_tier_list(a))
    p_tcalc = p_ts2.add_parser("calc", help="Calculate cost for a tier")
    p_tcalc.add_argument("name", help="Tier name (e.g., SMALL, LARGE)")
    p_tcalc.set_defaults(func=lambda a: _cmd_tier_calc(a))
    p_trr = p_ts2.add_parser("recommend", help="Recommend tier for budget")
    p_trr.add_argument("--budget", type=float, default=20.0)
    p_trr.set_defaults(func=lambda a: _cmd_tier_recommend(a))

    # emergency
    p_em = subparsers.add_parser("emergency", help="Emergency exit (sell all + sweep SOL)")
    p_em.add_argument("--mint", required=True, help="Token mint")
    p_em.add_argument("--dry-run", action="store_true", default=False)
    p_em.add_argument("--yes", action="store_true", default=False)
    p_em.add_argument("--devnet", action="store_true", default=True)
    p_em.set_defaults(func=lambda a: cmd_emergency(a))

    # fund
    p_fund = subparsers.add_parser("fund", help="Fund wallets")
    p_fund.add_argument("--budget-usd", type=float, default=20.0)
    p_fund.add_argument("--wallets", type=int, default=5)
    p_fund.add_argument("--faucet", action="store_true", help="Request devnet SOL from faucet")
    p_fund.add_argument("--wallet", type=str, help="Wallet pubkey for faucet request")
    p_fund.add_argument("--faucet-method", choices=["auto", "solfaucet", "quicknode", "rpc"], default="auto")
    p_fund.add_argument("--dry-run", action="store_true", default=False)
    p_fund.add_argument("--name", default="Token", help="Token name (for state)")
    p_fund.add_argument("--symbol", default="TKN", help="Token symbol (for state)")
    p_fund.add_argument("--devnet", action="store_true", default=True)
    p_fund.set_defaults(func=lambda a: cmd_fund(a))

    # lut
    p_lut = subparsers.add_parser("lut", help="Manage Address Lookup Tables")
    lp = p_lut.add_subparsers(dest="lut_cmd")
    p_lc = lp.add_parser("create", help="Generate LUT creation instructions")
    p_lc.add_argument("--mint", required=True, help="Token mint")
    p_lc.add_argument("--wallets", type=int, default=5)
    p_lc.set_defaults(func=lambda a: _lut_create(a))
    p_ll = lp.add_parser("list", help="List configured LUTs")
    p_ll.set_defaults(func=lambda a: _lut_list(a))
    p_li = lp.add_parser("inspect", help="Inspect a LUT")
    p_li.add_argument("lut_address", help="LUT account address")
    p_li.add_argument("--rpc", default=None)
    p_li.add_argument("--devnet", action="store_true", default=True)
    p_li.set_defaults(func=lambda a: _lut_inspect(a))
    p_lut.set_defaults(func=lambda a: cmd_lut(a))

    # research
    p_res = subparsers.add_parser("research", help="Token research: sniping, liquidity lock, holder analysis")
    p_res.add_argument("mint", help="Token mint to research")
    p_res.add_argument("--devnet", action="store_true", default=True)
    p_res.add_argument("--dry-run", action="store_true", default=False, help="Dry-run mode (mock results)")
    p_res.add_argument("--sniping", action="store_true", default=True, help="Check for sniper activity (default: on)")
    p_res.add_argument("--no-sniping", dest="sniping", action="store_false")
    p_res.add_argument("--liquidity", action="store_true", default=True, help="Verify liquidity lock (default: on)")
    p_res.add_argument("--no-liquidity", dest="liquidity", action="store_false")
    p_res.add_argument("--holders", action="store_true", default=True, help="Analyze holders (default: on)")
    p_res.add_argument("--no-holders", dest="holders", action="store_false")
    p_res.add_argument("--top-n", type=int, default=50, help="Top N holders to analyze")
    p_res.add_argument("--lookback", type=int, default=30, help="Sniping lookback window in seconds")
    p_res.add_argument("--whitelist", type=str, default="", help="Comma-separated whitelisted wallet pubkeys")
    p_res.set_defaults(func=lambda a: _cmd_research(a))

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Set convenience defaults
    if not hasattr(args, 'devnet'):
        args.devnet = True
    if not hasattr(args, 'dry_run'):
        args.dry_run = False
    if not hasattr(args, 'auto'):
        args.auto = False

    # Dispatch via func
    func = getattr(args, 'func', None)
    if func:
        func(args)
    else:
        # Fallback to command-based dispatch
        cmd_map = {
            "create": cmd_create,
            "wallet": cmd_wallet,
            "profile": cmd_profile,
            "trade": cmd_trade,
            "status": cmd_status,
            "rugcheck": cmd_rugcheck,
            "emergency": cmd_emergency,
            "fund": cmd_fund,
            "lut": cmd_lut,
        }
        handler = cmd_map.get(args.command)
        if handler:
            handler(args)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
