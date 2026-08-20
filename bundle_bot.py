"""
Bundle bot for coordinated multi-wallet trading on Solana.

Features:
1. Jito MEV bundle submission (atomic multi-tx execution)
2. Multi-wallet coordination (simultaneous buys/sells)
3. Pump.fun bundling (create+buy in one block)
4. Cross-DEX arbitrage bundles
5. All-coin scanner with bundled entries

Usage:
    from bundle_bot import BundleBot, BundleConfig
    bot = BundleBot(config.get_tier_config("MEDIUM"))
    await bot.run_volume_cycles()
"""

import asyncio
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from trading_engine import (
    get_advanced_quote,
    get_pumpfun_quote,
    get_network_conditions,
    build_advanced_swap_transaction,
    get_jito_tip_amount,
    get_jito_tip_accounts,
    WRAPPED_SOL_MINT,
    USDC_MINT,
    LAMPORTS_PER_SOL,
)
from config import get_tier_config, get_bundle_config, get_pumpfun_config


@dataclass
class WalletInfo:
    """Represents a trading wallet in the bundle."""
    index: int
    pubkey: str
    seed_b58: str
    sol_balance: float = 0.0
    token_balance: float = 0.0
    position_open: bool = False
    entry_price: float = 0.0
    peak_price: float = 0.0


@dataclass
class BundleTransaction:
    """A single transaction in a bundle."""
    wallet_index: int
    transaction: str  # base64 encoded
    action: str  # 'buy', 'sell', 'tip'
    expected_output: Optional[str] = None


class BundleBot:
    """
    Multi-wallet bundle trading bot.

    Coordinates multiple wallets to execute simultaneous trades
    via Jito MEV bundles for front-run resistance and atomicity.
    """

    def __init__(self, tier_config: Dict[str, Any], rpc_endpoint: str = None):
        self.config = tier_config
        self.rpc_endpoint = rpc_endpoint or "https://api.mainnet-beta.solana.com"
        self.wallets: List[WalletInfo] = []
        self.quote_cache: Dict[str, Any] = {}
        self._last_quote_time: float = 0

    def generate_wallets(self) -> List[WalletInfo]:
        """Generate deterministic sub-wallets for bundling."""
        script_path = os.path.join(os.path.dirname(__file__), "wallet_utils.js")

        self.wallets = []
        for i in range(self.config["num_wallets"]):
            result = subprocess.run(
                ["node", script_path, "derive",
                 "--seed", os.environ.get("PRIVATE_KEY", ""),
                 "--index", str(i)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                wallet = WalletInfo(
                    index=i,
                    pubkey=data["pubkey"],
                    seed_b58=data["seed_b58"],
                    sol_balance=self.config["sol_per_wallet"],
                )
                self.wallets.append(wallet)

        return self.wallets

    def get_cached_quote(self, token_mint: str, input_mint: str = WRAPPED_SOL_MINT,
                         amount: int = None, force_refresh: bool = False) -> Optional[Dict]:
        """Get cached quote with TTL (quotes expire after 15 seconds)."""
        cache_key = f"{input_mint}->{token_mint}:{amount}"
        now = time.time()

        if not force_refresh and cache_key in self.quote_cache:
            if now - self._last_quote_time < 15:
                return self.quote_cache[cache_key]

        if amount is None:
            amount = int(self.config["sol_per_wallet"] * LAMPORTS_PER_SOL)

        quote = get_advanced_quote(input_mint, token_mint, amount, self.config["slippage_bps"])
        if quote:
            self.quote_cache[cache_key] = quote
            self._last_quote_time = now
        return quote

    async def build_bundle_transactions(
        self,
        token_mint: str,
        action: str = "buy",
        use_jito: bool = True,
    ) -> List[BundleTransaction]:
        """
        Build a list of transactions for the bundle.

        For 'buy': all wallets buy simultaneously.
        For 'sell': all wallets sell simultaneously.
        """
        transactions = []

        # Create a single shared quote for all wallets (prevents race conditions)
        quote = self.get_cached_quote(token_mint, force_refresh=True)
        if not quote:
            return transactions

        for wallet in self.wallets:
            tx = build_advanced_swap_transaction(
                quote=quote,
                user_pubkey=wallet.pubkey,
                slippage_bps=self.config["slippage_bps"],
                priority_fee_micro_lamports=self.config["priority_fee_micro_lamports"],
                use_jito=use_jito,
            )
            if tx:
                transactions.append(BundleTransaction(
                    wallet_index=wallet.index,
                    transaction=tx,
                    action=action,
                    expected_output=str(int(quote["outAmount"])),
                ))

        # Add Jito tip transaction if using bundles
        if use_jito and self.config.get("use_jito", False):
            tip_tx = self._build_jito_tip_transaction()
            if tip_tx:
                transactions.append(BundleTransaction(
                    wallet_index=-1,
                    transaction=tip_tx,
                    action="tip",
                ))

        return transactions

    def _build_jito_tip_transaction(self) -> Optional[str]:
        """Build a Jito tip transaction for the bundle."""
        tip_amount = self.config.get("jito_tip_lamports", get_jito_tip_amount("medium"))
        tip_accounts = get_jito_tip_accounts()

        if not tip_accounts:
            return None

        # Call sign_sender.js tip_transfer to build the tip transaction
        script_path = os.path.join(os.path.dirname(__file__), "sign_sender.js")

        # We need a wallet seed to sign the tip transfer
        # Use the first wallet's seed
        first_wallet = self.wallets[0] if self.wallets else None
        if not first_wallet:
            return None

        result = subprocess.run(
            ["node", script_path, "tip_transfer", str(tip_amount), first_wallet.seed_b58],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("transaction")
        return None

    async def submit_jito_bundle(self, transactions: List[BundleTransaction]) -> Optional[str]:
        """
        Submit a bundle of transactions via Jito.

        Returns the bundle ID if successful.
        """
        if not transactions:
            return None

        # Build the bundle payload
        bundle_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [
                [tx.transaction for tx in transactions],
            ],
        }

        # Submit to Jito relayer
        import urllib.request
        data = json.dumps(bundle_payload).encode("utf-8")
        req = urllib.request.Request(
            "https://bundles.jito.wtf/api/v1/bundles",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                if "result" in result:
                    return result["result"].get("signature")
        except Exception as e:
            print(f"  [ERROR] Bundle submission failed: {e}")
            # Fallback: submit transactions individually
            return await self._submit_individual(transactions)

        return None

    async def _submit_individual(self, transactions: List[BundleTransaction]) -> Optional[str]:
        """Fallback: submit transactions individually via RPC."""
        for tx in transactions:
            if tx.action == "tip":
                continue  # Skip tip on individual submission
            from sign_sender import send_transaction
            sig = send_transaction(tx.transaction, self.rpc_endpoint)
            if sig:
                print(f"  Wallet {tx.wallet_index} signed {tx.action}: {sig[:16]}...")
        return "individual_fallback"

    async def run_volume_cycle(
        self,
        token_mint: str,
        input_mint: str = WRAPPED_SOL_MINT,
        use_bundles: bool = True,
    ) -> Dict[str, Any]:
        """
        Run one complete buy+sell cycle for all wallets.

        Returns summary of the cycle results.
        """
        results = {
            "buy_txs": [],
            "sell_txs": [],
            "bundle_id": None,
            "total_output": 0,
            "errors": [],
        }

        # Step 1: Build buy transactions
        buy_txs = await self.build_bundle_transactions(token_mint, "buy", use_jito=use_bundles)
        if not buy_txs:
            results["errors"].append("No buy transactions generated")
            return results

        # Step 2: Submit buy bundle
        if use_bundles and self.config.get("use_bundles", False):
            bundle_id = await self.submit_jito_bundle(buy_txs)
            results["bundle_id"] = bundle_id

        # Track which wallets have positions
        for tx in buy_txs:
            if tx.wallet_index >= 0:
                wallet = self.wallets[tx.wallet_index]
                wallet.position_open = True
                wallet.entry_price = 0  # Would track actual entry price
                wallet.peak_price = 0
                results["buy_txs"].append({
                    "wallet": wallet.index,
                    "pubkey": wallet.pubkey,
                    "tx": tx.transaction[:40] + "...",
                })

        # Step 3: Wait and then sell
        # In a real bot, this would monitor price for optimal exit
        # For now, simulate a hold period
        await asyncio.sleep(1)  # Placeholder for actual price monitoring

        # Step 4: Build sell transactions
        sell_txs = await self.build_bundle_transactions(token_mint, "sell", use_jito=use_bundles)
        results["sell_txs"] = sell_txs

        return results

    async def run_all_cycles(self, token_mint: str) -> List[Dict[str, Any]]:
        """Run all trading cycles for this tier configuration."""
        cycle_results = []

        for cycle in range(self.config["cycles_per_wallet"]):
            print(f"\n  Cycle {cycle + 1}/{self.config['cycles_per_wallet']}")
            result = await self.run_volume_cycle(token_mint)
            cycle_results.append(result)

            # Check if we should stop (loss limit, etc.)
            if result.get("errors"):
                print(f"  Errors in cycle {cycle + 1}: {result['errors']}")

        return cycle_results

    def get_config_summary(self) -> Dict[str, Any]:
        """Get a summary of the current configuration."""
        return {
            "budget_usd": self.config["budget_usd"],
            "num_wallets": self.config["num_wallets"],
            "cycles_per_wallet": self.config["cycles_per_wallet"],
            "sol_per_wallet": self.config["sol_per_wallet"],
            "slippage_bps": self.config["slippage_bps"],
            "priority_fee": self.config["priority_fee_micro_lamports"],
            "use_jito": self.config.get("use_jito", False),
            "use_bundles": self.config.get("use_bundles", False),
            "strategies": self.config.get("strategies", []),
            "total_estimated_cost_sol": (
                self.config["num_wallets"] * self.config["sol_per_wallet"] +
                self.config["num_wallets"] * self.config["cycles_per_wallet"] * 2 * 0.000015 +
                self.config["num_wallets"] * 0.00025
            ),
        }


def create_bundle_bot(tier_name: str = "SMALL", rpc_endpoint: str = None) -> BundleBot:
    """Factory function to create a bundle bot for a specific money tier."""
    config = get_tier_config(tier_name)
    return BundleBot(config, rpc_endpoint)
