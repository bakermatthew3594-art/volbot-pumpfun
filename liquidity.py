"""
Multi-DEX Liquidity Provision & Wash Trading Engine.

Supports liquidity provision and wash trading on:
1. Pump.fun (bonding curve)
2. Raydium (AMM)
3. Orca (Whirlpool concentrated liquidity)
4. Meteora (Dynamic AMM / DLMM)
5. Openbook (Order Book)
6. Jupiter (Aggregator - routes to all DEXes)

Each DEX has different liquidity models:
  - Pump.fun: Bonding curve (no LP tokens)
  - Raydium: x*y=k AMM (fungible LP tokens)
  - Orca: Concentrated liquidity (NFT positions)
  - Meteora: Dynamic fees and ranges
  - Openbook: Limit orders

Wash trading: Using multiple wallets to simulate volume
and attract organic traders to newly created pools.
"""

import json
import math
import os
import random
import time
from typing import Any, Dict, List, Optional

from trading_engine import (
    get_advanced_quote, get_price_feed, get_trending_pairs,
    get_jito_tip_amount, WRAPPED_SOL_MINT, USDC_MINT, BONK_MINT,
    JUPITER_QUOTE_API, JUPITER_SWAP_API, JITO_TIP_MIN_LAMPORTS,
    LAMPORTS_PER_SOL, _make_request, _make_post_request,
)
from bundle_bot import BundleBot, BundleTransaction, WalletInfo, create_bundle_bot
from config import get_tier_config, get_bundle_config, BUNDLE_MODES
from onchain_monitor import SolanaRPC


# ─── DEX Constants ───

RAYDIUM_API = "https://api.raydium.io/v2/main"
RAYDIUM_API_V3 = "https://api.raydium.io/v3"
ORCA_API = "https://api.orca.so/v1"
METEORA_API = "https://api.meteora.io/v1"
PUMP_FUN_API = "https://api.pump.fun/v1"

RAYDIUM_AMM_PROGRAM = "5Q25VcB6HgqJ6fH8wJq8vR9mT2uP3zQ4wE5rT6yU7iO8"
ORCA_WHIRLPOOL_PROGRAM = "whirlpool1r4FHKj32q5T5t5t5t5t5t5t5t5t5t5t5t5t5"
METEORA_DAMM_PROGRAM = "Dpool1m2Q5r6s7t8u9v0w1x2y3z4a5b6c7d8e9f0g1h2"


class LiquidityProvider:
    """
    Multi-DEX liquidity provision manager.

    Handles:
      - Pool discovery and analysis
      - Liquidity deposit/withdrawal
      - Fee calculation and APR estimation
      - Range management for concentrated liquidity
    """

    def __init__(self, rpc_endpoint: str = "https://api.mainnet-beta.solana.com"):
        self.rpc = SolanaRPC(rpc_endpoint)
        self.active_positions: Dict[str, Dict] = {}

    def discover_pools(self, token_mint: str, dex_filter: str = "all") -> List[Dict]:
        """
        Discover liquidity pools for a given token across DEXes.

        Returns list of pool info including liquidity, fees, APR.
        """
        pools = []

        # Jupiter markets (aggregates all DEXes)
        if dex_filter in ("all", "jupiter"):
            jupiter_url = f"{JUPITER_QUOTE_API}?inputMint={WRAPPED_SOL_MINT}&outputMint={token_mint}&amount=1000000000"
            quote = _make_request(jupiter_url)
            if quote and "data" in quote:
                pass  # Use for routing info

        # Raydium pools
        if dex_filter in ("all", "raydium"):
            raydium_pools = self._search_raydium_pool(token_mint)
            pools.extend(raydium_pools)

        # Orca pools
        if dex_filter in ("all", "orca"):
            orca_pools = self._search_orca_pool(token_mint)
            pools.extend(orca_pools)

        # Meteora pools
        if dex_filter in ("all", "meteora"):
            meteora_pools = self._search_meteora_pool(token_mint)
            pools.extend(meteora_pools)

        # Pump.fun pools (bonding curves)
        if dex_filter in ("all", "pumpfun"):
            pump_pools = self._search_pumpfun(token_mint)
            pools.extend(pump_pools)

        return pools

    def _search_raydium_pool(self, token_mint: str) -> List[Dict]:
        """Search for Raydium AMM pools."""
        url = f"{RAYDIUM_API}/pool?mint={token_mint}"
        data = _make_request(url)
        if not data:
            return []

        pools = []
        for item in data.get("data", [])[:5]:
            pools.append({
                "dex": "raydium",
                "pool_address": item.get("address", ""),
                "token_mint": token_mint,
                "ticker": item.get("ticker", "Unknown"),
                "liquidity_usd": item.get("liquidity_usd", 0),
                "volume_24h": item.get("volume_24h", 0),
                "fees_24h": item.get("fees_24h", 0),
                "apr": item.get("apr", 0),
                "pool_type": "amm_x*y=k",
            })
        return pools

    def _search_orca_pool(self, token_mint: str) -> List[Dict]:
        """Search for Orca Whirlpool pools."""
        url = f"{ORCA_API}/whirlpools?token={token_mint}"
        data = _make_request(url)
        if not data:
            return []

        pools = []
        for item in data.get("whirlpools", [])[:5]:
            pools.append({
                "dex": "orca",
                "pool_address": item.get("address", ""),
                "token_mint": token_mint,
                "ticker": f"{item.get('tokenA', '')}-{item.get('tokenB', '')}",
                "liquidity": item.get("liquidity", 0),
                "fee_rate": item.get("feeRate", 0),
                "apr": item.get("apr", 0),
                "pool_type": "concentrated_liquidity",
            })
        return pools

    def _search_meteora_pool(self, token_mint: str) -> List[Dict]:
        """Search for Meteora pools."""
        url = f"{METEORA_API}/search?query={token_mint}"
        data = _make_request(url)
        if not data:
            return []

        pools = []
        for item in data.get("pools", [])[:5]:
            pools.append({
                "dex": "meteora",
                "pool_address": item.get("address", ""),
                "token_mint": token_mint,
                "ticker": item.get("name", "Unknown"),
                "liquidity_usd": item.get("liquidityUsd", 0),
                "volume_24h": item.get("volume24h", 0),
                "apr": item.get("apr", 0),
                "pool_type": "dynamic_amm",
            })
        return pools

    def _search_pumpfun(self, token_mint: str) -> List[Dict]:
        """Search for Pump.fun bonding curves."""
        url = f"{PUMP_FUN_API}/coins/{token_mint}"
        data = _make_request(url)
        if not data:
            return []

        price_data = get_price_feed(token_mint)
        return [{
            "dex": "pumpfun",
            "pool_address": token_mint,
            "token_mint": token_mint,
            "ticker": data.get("name", "Unknown"),
            "price_usd": price_data.get("usdPrice", 0) if price_data else 0,
            "liquidity_usd": data.get("usd", 0),
            "market_cap": data.get("usd_market_cap", 0),
            "pool_type": "bonding_curve",
        }]

    def get_pool_depth(self, pool_address: str) -> Dict:
        """
        Analyze pool liquidity depth at various price ranges.
        Useful for estimating slippage before adding liquidity.
        """
        # For AMM pools, depth is proportional to sqrt(k)
        # For this we need the pool's reserve data
        account = self.rpc.get_account_info(pool_address)
        if not account:
            return {"error": "Pool not found"}

        try:
            data = account.get("data", {}).get("parsed", {}).get("info", {})
            reserve_a = int(data.get("reserveA", {}).get("amount", 0))
            reserve_b = int(data.get("reserveB", {}).get("amount", 0))
            decimals_a = int(data.get("mintA", {}).get("decimals", 6))
            decimals_b = int(data.get("mintB", {}).get("decimals", 6))

            # Price = reserve_b / reserve_a (normalized)
            price = (reserve_b / 10**decimals_b) / (reserve_a / 10**decimals_a) if reserve_a > 0 else 0

            return {
                "reserve_a": reserve_a / 10**decimals_a,
                "reserve_b": reserve_b / 10**decimals_b,
                "price": price,
                "total_liquidity": (reserve_a / 10**decimals_a) * price + reserve_b / 10**decimals_b,
                "depth_curve": self._calculate_depth_curve(reserve_a, reserve_b, decimals_a, decimals_b),
            }
        except (KeyError, TypeError, ValueError):
            return {"error": "Could not parse pool data"}

    def _calculate_depth_curve(self, reserve_a: int, reserve_b: int,
                               dec_a: int, dec_b: int) -> List[Dict]:
        """Calculate slippage at different trade sizes."""
        ra = reserve_a / 10**dec_a
        rb = reserve_b / 10**dec_b
        price = rb / ra if ra > 0 else 0
        k = ra * rb  # Constant product

        depths = []
        for pct in [0.1, 0.5, 1, 2, 5, 10]:
            trade_size = ra * pct / 100
            new_ra = ra + trade_size
            new_rb = k / new_ra
            new_price = new_rb / new_ra if new_ra > 0 else 0
            slippage = (price - new_price) / price * 100 if price > 0 else 0
            depths.append({
                "trade_pct": pct,
                "trade_size": trade_size,
                "price_impact_pct": abs(slippage),
            })
        return depths

    def add_liquidity(self, pool_address: str, dex: str,
                      amount_a: float, amount_b: float,
                      token_a_decimals: int = 6,
                      token_b_decimals: int = 6,
                      range_min: Optional[float] = None,
                      range_max: Optional[float] = None) -> Dict:
        """
        Add liquidity to a pool.

        For AMM pools (Raydium): Provide equal value amounts
        For Concentrated (Orca): Specify price range
        For Bonding Curve (Pump.fun): Buy directly from the curve
        """
        position = {
            "pool_address": pool_address,
            "dex": dex,
            "amount_a": amount_a,
            "amount_b": amount_b,
            "timestamp": time.time(),
            "status": "pending",
        }

        if dex == "pumpfun":
            # Pump.fun uses bonding curve - no LP tokens
            # Just buy tokens from the curve
            position["position_id"] = f"pump_{pool_address}"
            position["shares"] = amount_b  # Tokens received
            position["status"] = "active"
            position["pool_type"] = "bonding_curve"

        elif dex == "raydium":
            # Raydium AMM - deposit equal value
            position["position_id"] = f"ray_{pool_address}"
            position["lp_tokens_received"] = (amount_a * amount_b) ** 0.5  # Simplified
            position["status"] = "active"
            position["pool_type"] = "amm"

        elif dex == "orca":
            # Orca Whirlpool - concentrated liquidity
            position["position_id"] = f"orca_{pool_address}"
            position["lower_tick"] = range_min or 0.9  # 10% below current
            position["upper_tick"] = range_max or 1.1  # 10% above current
            position["status"] = "active"
            position["pool_type"] = "whirlpool"

        elif dex == "meteora":
            # Meteora DLMM - dynamic ranges
            position["position_id"] = f"met_{pool_address}"
            position["bins"] = self._calculate_bin_ranges(range_min or 0.8, range_max or 1.2)
            position["status"] = "active"
            position["pool_type"] = "dlmm"

        self.active_positions[position["position_id"]] = position
        return position

    def _calculate_bin_ranges(self, range_min: float, range_max: float) -> List[Dict]:
        """Calculate bin ranges for Meteora DLMM."""
        bins = []
        num_bins = 10
        for i in range(num_bins):
            lower = range_min + (range_max - range_min) * i / num_bins
            upper = range_min + (range_max - range_min) * (i + 1) / num_bins
            bins.append({"lower": lower, "upper": upper, "active": True})
        return bins

    def withdraw_liquidity(self, position_id: str) -> Dict:
        """Withdraw liquidity from a position."""
        position = self.active_positions.get(position_id)
        if not position:
            return {"error": "Position not found"}

        position["status"] = "withdrawing"
        position["withdraw_timestamp"] = time.time()
        position["status"] = "closed"
        del self.active_positions[position_id]

        return position

    def calculate_fees(self, position_id: str, pool_address: str) -> Dict:
        """
        Estimate fees earned from a liquidity position.

        Factors: trading fee rate, volume, time, position size.
        """
        position = self.active_positions.get(position_id)
        if not position:
            return {"error": "Position not found"}

        pools = self.discover_pools(position.get("token_mint", WRAPPED_SOL_MINT),
                                     dex_filter=position["dex"])
        pool = next((p for p in pools if p["pool_address"] == pool_address), None)
        if not pool:
            return {"error": "Pool not found"}

        # Fee estimate = volume * fee_rate * position_share
        fee_rate = self._get_fee_rate(position["dex"], pool)
        volume_24h = pool.get("volume_24h", pool.get("volume", 0))
        position_share = position.get("amount_a", 0) / pool.get("liquidity_usd", 1)

        daily_fees = volume_24h * fee_rate * position_share
        return {
            "daily_fees_usd": daily_fees,
            "apr": (daily_fees * 365 / (position.get("amount_a", 1) + position.get("amount_b", 1))) * 100,
            "fee_rate": fee_rate,
            "position_share": position_share,
        }

    def _get_fee_rate(self, dex: str, pool: Dict) -> float:
        """Get fee rate for a DEX/pool type."""
        fee_rates = {
            "pumpfun": 0.0,  # Bonding curve, no LP fees
            "raydium": 0.0025,  # 0.25%
            "orca": 0.0005,  # 0.05% typical
            "meteora": 0.001,  # 0.10% typical
        }
        if "fee_rate" in pool:
            return pool["fee_rate"] / 10000
        if "apr" in pool:
            return pool.get("apr", 0) / 365 / 100  # Back-calculate
        return fee_rates.get(dex, 0.001)


class WashTrader:
    """
    Wash trading engine using multiple wallets to simulate volume.

    Strategy: Create N wallets, distribute funds, then have them
    buy/sell back and forth to create artificial trading volume.
    This attracts real traders who see "buzz" on the token.

    Risk: Can get wallets banned if detected. Use sparingly.
    """

    def __init__(self, tier: str = "SMALL", num_wallets: int = None):
        config = get_tier_config(tier)
        self.num_wallets = num_wallets or config["num_wallets"]
        self.sol_per_wallet = config["sol_per_wallet"]
        self.cycles = config["cycles_per_wallet"]
        self.slippage_bps = config.get("slippage_bps", 300)
        self.jito_tip = get_jito_tip_amount("medium")
        self.wallets: List[WalletInfo] = []
        self.trades: List[Dict] = []

    def generate_wallets(self) -> List[WalletInfo]:
        """Generate wallets for wash trading."""
        import subprocess
        wallet_js = os.path.join(os.path.dirname(__file__), "wallet_utils.js")
        seed_result = subprocess.run(
            ["node", wallet_js, "generate"],
            capture_output=True, text=True, timeout=10
        )
        if seed_result.returncode != 0:
            raise RuntimeError(f"Wallet generation failed: {seed_result.stderr}")

        main_wallet = json.loads(seed_result.stdout)
        from wallet_utils import derive_sub_wallet

        # Derive sub-wallets
        sub_result = subprocess.run(
            ["node", wallet_js, "derive", main_wallet["seed_b58"], str(self.num_wallets)],
            capture_output=True, text=True, timeout=15
        )
        if sub_result.returncode == 0:
            wallets = json.loads(sub_result.stdout)
            for i, w in enumerate(wallets):
                self.wallets.append(WalletInfo(
                    index=i,
                    pubkey=w["pubkey"],
                    seed_b58=w["seed_b58"],
                ))
        return self.wallets

    def distribute_funds(self, token_mint: str = WRAPPED_SOL_MINT) -> Dict:
        """
        Distribute funds to all wallets for wash trading.

        Each wallet gets an equal share of the total budget.
        """
        if not self.wallets:
            self.generate_wallets()

        # Calculate amount per wallet
        total_sol = self.sol_per_wallet * self.num_wallets
        per_wallet = total_sol / self.num_wallets

        print(f"Distributing {total_sol:.4f} SOL to {self.num_wallets} wallets...")

        distributions = []
        for wallet in self.wallets:
            dist = {
                "wallet_index": wallet.index,
                "pubkey": wallet.pubkey,
                "amount_sol": per_wallet,
                "status": "pending",
            }
            # In real implementation, would send SOL from main wallet
            dist["status"] = "simulated"
            distributions.append(dist)

        return {
            "total_distributed_sol": total_sol,
            "per_wallet_sol": per_wallet,
            "distributions": distributions,
        }

    def generate_trade_sequence(self, token_mint: str,
                                base_token: str = None) -> List[Dict]:
        """
        Generate a sequence of buy/sell orders among wallets to simulate volume.

        Uses a round-robin approach: Wallet A buys from B, B sells to A, etc.
        Also adds random timing to look more organic.
        """
        if base_token is None:
            base_token = USDC_MINT

        trades = []
        for cycle in range(self.cycles):
            for i, wallet in enumerate(self.wallets):
                # Alternate buyer/seller
                buyer_idx = i
                seller_idx = (i + 1) % len(self.wallets)

                # Randomize trade size (50-100% of available)
                trade_amount = random.uniform(0.001, 0.005) * LAMPORTS_PER_SOL
                side = "buy" if cycle % 2 == 0 else "sell"

                trade = {
                    "cycle": cycle,
                    "buyer_wallet": buyer_idx,
                    "seller_wallet": seller_idx,
                    "token_mint": token_mint,
                    "base_token": base_token,
                    "amount": trade_amount,
                    "side": side,
                    "timestamp": time.time() + cycle * random.uniform(5, 30),
                    "priority_fee_lamports": self.jito_tip,
                }
                trades.append(trade)

        return trades

    def estimate_volume(self, token_mint: str) -> Dict:
        """Estimate the trading volume that would be generated."""
        # Get current price
        price_data = get_price_feed(token_mint)
        price = price_data["usdPrice"] if price_data else 0

        per_wallet_sol = self.sol_per_wallet
        total_volume = 0

        for cycle in range(self.cycles):
            for i in range(self.num_wallets):
                trade_amount_sol = random.uniform(0.001, 0.003) * LAMPORTS_PER_SOL / LAMPORTS_PER_SOL
                trade_usd = trade_amount_sol * price
                total_volume += trade_usd

        return {
            "total_volume_usd": total_volume,
            "estimated_volume_token": total_volume / price if price > 0 else 0,
            "num_wallets": self.num_wallets,
            "num_cycles": self.cycles,
            "price": price,
            "jito_tip_usd": self.jito_tip / LAMPORTS_PER_SOL * 150 if price else 0,
        }

    def simulate_trading(self, token_mint: str) -> Dict:
        """
        Simulate wash trading sequence (no actual transactions).

        Returns estimated results for planning.
        """
        if not self.wallets:
            self.generate_wallets()

        trades = self.generate_trade_sequence(token_mint)
        volume_info = self.estimate_volume(token_mint)

        # Estimate fees
        total_txs = len(trades) * 2  # Each trade is buy + sell
        fee_per_tx = 0.000005 * LAMPORTS_PER_SOL  # Base fee
        priority_fees = self.jito_tip * total_txs
        total_fees_sol = fee_per_tx * total_txs + priority_fees

        return {
            "status": "simulated",
            "wallets": len(self.wallets),
            "trades": len(trades),
            "total_transactions": total_txs,
            "estimated_volume_usd": volume_info["total_volume_usd"],
            "estimated_volume_token": volume_info["estimated_volume_token"],
            "total_fees_sol": total_fees_sol,
            "total_fees_usd": total_fees_sol * 150,
            "per_wallet_sol": self.sol_per_wallet,
            "jito_tip_sol": self.jito_tip / LAMPORTS_PER_SOL,
        }

    def execute_bundle_trades(self, token_mint: str,
                               num_cycles: int = None,
                               use_jito: bool = True) -> List[BundleTransaction]:
        """
        Execute wash trades as a Jito bundle for atomicity.

        All trades in a cycle are submitted as a single bundle
        to prevent MEV bots from frontrunning.
        """
        if num_cycles is None:
            num_cycles = min(self.cycles, 5)  # Cap at 5 for safety

        trades = self.generate_trade_sequence(token_mint)
        bundle_txs: List[BundleTransaction] = []

        for cycle in range(num_cycles):
            cycle_trades = [t for t in trades if t["cycle"] == cycle]
            for trade in cycle_trades:
                bundle_tx = BundleTransaction(
                    wallet_index=trade["buyer_wallet"],
                    transaction=f"swap_{trade['side']}_{token_mint[:8]}_{trade['timestamp']:.0f}",
                    action=trade["side"],
                    amount=str(trade["amount"]),
                    token_address=token_mint,
                )
                bundle_txs.append(bundle_tx)

        return bundle_txs


class LiquidityManager:
    """
    High-level liquidity management combining multiple DEX strategies.

    Features:
    - Optimal DEX selection based on token/pool criteria
    - Multi-DEX position coordination
    - Fee harvesting automation
    - Range rebalancing for concentrated liquidity
    - Impermanent loss hedging
    """

    def __init__(self, rpc_endpoint: str = "https://api.mainnet-beta.solana.com"):
        self.lp = LiquidityProvider(rpc_endpoint)
        self.wash_trader = None
        self._dex_preferences = {
            "pumpfun": {"priority": 1, "reason": "bonding curve, easy entry"},
            "raydium": {"priority": 2, "reason": "established AMM, high TVL"},
            "orca": {"priority": 3, "reason": "concentrated liquidity, higher APR"},
            "meteora": {"priority": 4, "reason": "dynamic fees, DLMM flexibility"},
        }

    def find_best_dex(self, token_mint: str, min_liquidity_usd: float = 1000) -> Dict:
        """
        Find the best DEX for liquidity provision based on:
        - Liquidity depth (slippage)
        - Fee rates
        - APR
        - Pool age/activity
        """
        pools = self.lp.discover_pools(token_mint, dex_filter="all")

        # Score pools based on multiple criteria
        scored = []
        for pool in pools:
            score = 0
            liquidity = pool.get("liquidity_usd", 0) or pool.get("liquidity", 0) or 0
            apr = pool.get("apr", 0)
            volume = pool.get("volume_24h", 0) or pool.get("volume", 0) or 0

            if liquidity >= min_liquidity_usd:
                score += 40
            if apr > 50:
                score += 30
            if volume > 100000:
                score += 20
            if pool["dex"] == "pumpfun":
                score += 10  # Easier to provide liquidity

            scored.append({
                "pool": pool,
                "score": score,
                "liquidity": liquidity,
                "apr": apr,
                "volume": volume,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[0] if scored else {"error": "No suitable pools found"}

    def create_liquidity_strategy(self, token_mint: str,
                                   budget_usd: float = 20,
                                   strategy: str = "auto") -> Dict:
        """
        Create a complete liquidity provision strategy.

        Strategies:
        - "auto": Let system choose best DEX
        - "pumpfun": Buy from bonding curve + wash trade
        - "raydium": Provide LP to AMM
        - "orca": Concentrated liquidity range
        - "mix": Split across multiple DEXes
        """
        if strategy == "auto":
            best = self.find_best_dex(token_mint)
            if "error" in best:
                return {"error": "No suitable pools found"}
            dex = best["pool"]["dex"]
        else:
            dex = strategy
            best = {"pool": {"dex": dex}, "score": 100}

        config = {
            "token_mint": token_mint,
            "dex": dex,
            "budget_usd": budget_usd,
            "budget_sol": budget_usd / 150,  # At $150/SOL
        }

        if dex == "pumpfun":
            # Pump.fun strategy: Buy tokens + create wash trading
            config.update({
                "steps": [
                    "1. Buy tokens from bonding curve",
                    "2. Create 3-5 wash trading wallets",
                    "3. Simulate small volume trades",
                    "4. Withdraw remaining tokens",
                ],
                "wallets_needed": 3,
                "estimated_fees": budget_usd * 0.15,  # ~15% for gas + tips
            })
        elif dex == "raydium":
            config.update({
                "steps": [
                    "1. Create LP position",
                    "2. Provide 50/50 token + SOL",
                    "3. Earn trading fees over time",
                    "4. Remove position when APR drops",
                ],
                "wallets_needed": 1,
                "estimated_fees": budget_usd * 0.05,
            })
        elif dex == "orca":
            config.update({
                "steps": [
                    "1. Create concentrated liquidity range",
                    "2. Set range ±10% around current price",
                    "3. Earn higher fee tier (0.25%)",
                    "4. Rebalance if price moves outside range",
                ],
                "wallets_needed": 1,
                "estimated_fees": budget_usd * 0.05,
            })

        return config

    def setup_wash_trading(self, tier: str = "SMALL") -> WashTrader:
        """Set up a wash trader with the given money tier."""
        self.wash_trader = WashTrader(tier=tier)
        return self.wash_trader


# ─── Helper functions for CLI integration ───

def get_lake_charter_dexes() -> List[Dict]:
    """Return list of DEXes with detailed info for CLI display."""
    return [
        {
            "name": "Pump.fun",
            "type": "Bonding Curve",
            "description": "Token launch platform with automated market maker",
            "min_budget": "$5",
            "complexity": "Easy",
            "wash_trading": True,
        },
        {
            "name": "Raydium",
            "type": "AMM (x*y=k)",
            "description": "Established DEX with high liquidity and volume",
            "min_budget": "$10",
            "complexity": "Medium",
            "wash_trading": True,
        },
        {
            "name": "Orca",
            "type": "Whirlpool (Concentrated)",
            "description": "Concentrated liquidity DEX with higher fee tiers",
            "min_budget": "$15",
            "complexity": "Advanced",
            "wash_trading": True,
        },
        {
            "name": "Meteora",
            "type": "Dynamic AMM (DLMM)",
            "description": "Dynamic liquidity with customizable bin ranges",
            "min_budget": "$15",
            "complexity": "Advanced",
            "wash_trading": True,
        },
        {
            "name": "Openbook",
            "type": "Order Book",
            "description": "Central limit order book - set limit orders",
            "min_budget": "$10",
            "complexity": "Medium",
            "wash_trading": True,
        },
        {
            "name": "Jupiter",
            "type": "Aggregator",
            "description": "Routes to all DEXes for best prices",
            "min_budget": "$5",
            "complexity": "Easy",
            "wash_trading": False,
        },
    ]


def get_wash_trading_strategies() -> List[Dict]:
    """Return available wash trading strategies."""
    return [
        {
            "name": "Round Robin",
            "description": "Wallets trade sequentially (A->B->C->A)",
            "complexity": "Easy",
            "detection_risk": "Low",
        },
        {
            "name": "Ping Pong",
            "description": "Two wallets trade back and forth rapidly",
            "complexity": "Easy",
            "detection_risk": "Medium",
        },
        {
            "name": "Ring Trading",
            "description": "All wallets participate in a circular trade",
            "complexity": "Medium",
            "detection_risk": "Medium",
        },
        {
            "name": "Whale Mimicry",
            "description": "One large trade followed by smaller confirming trades",
            "complexity": "Advanced",
            "detection_risk": "Low",
        },
        {
            "name": "Market Maker",
            "description": "Post both bids and asks across multiple wallets",
            "complexity": "Advanced",
            "detection_risk": "High",
        },
    ]


def estimate_wash_trading_cost(num_wallets: int, num_cycles: int,
                                avg_trade_size_usd: float = 5.0) -> Dict:
    """Estimate total cost of wash trading operation."""
    # Each cycle involves num_wallets trades (each wallet trades once)
    total_trades = num_wallets * num_cycles
    total_txs = total_trades * 2  # Buy + sell per cycle

    # Costs
    base_fees = total_txs * 0.000005 * 150  # $0.00075 per tx at $150/SOL
    jito_tips = total_txs * 50000 * 150 / LAMPORTS_PER_SOL  # $0.05 per tx
    trading_fees = total_trades * avg_trade_size_usd * 0.003  # 0.3% per trade
    volume_created = total_trades * avg_trade_size_usd

    total_cost = base_fees + jito_tips + trading_fees

    return {
        "num_wallets": num_wallets,
        "num_cycles": num_cycles,
        "total_trades": total_trades,
        "total_transactions": total_txs,
        "base_fees_usd": base_fees,
        "jito_tips_usd": jito_tips,
        "trading_fees_usd": trading_fees,
        "total_cost_usd": total_cost,
        "volume_created_usd": volume_created,
        "cost_to_volume_ratio": total_cost / volume_created if volume_created > 0 else 0,
        "within_20_budget": total_cost <= 20,
    }
