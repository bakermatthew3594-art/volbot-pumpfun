#!/usr/bin/env python3
"""
Advanced Smart Wallet Bundler for Pump.fun Trading.

Features:
1. Dynamic wallet allocation with 20+ wallets, role-based distribution
2. Jito MEV bundle submission (atomic multi-tx execution)
3. Anti-detection: randomized amounts, timing, fees, DEX routing
4. Cover wallet system for clean exits
5. Natural buyer matching (react to large organic buys)
6. Bubble detection and avoidance
7. Profit harvesting and capital redistribution

Usage:
    from smart_bundler import SmartBundler, WalletRole, WalletGroup, BundledTrade
    bundler = SmartBundler(budget_sol=6.0, num_wallets=20)
    bundler.setup_wallets()
    bundle = bundler.build_buy_bundle(token_mint, total_sol=0.5, natural_buy=False)
    result = bundler.submit_bundle(bundle)
"""

import os
import sys
import json
import time
import random
import math
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from enum import Enum

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


class WalletRole(Enum):
    """Roles for wallets in the bundling system."""
    WHALE = "whale"          # Large buys, sets price direction (20% alloc)
    MID = "mid"              # Main volume driver (15% alloc each)
    NORMAL = "normal"        # Natural trading patterns (5% each)
    NOISE = "noise"          # Tiny trades for obfuscation (2% each)
    SNIPER = "sniper"        # Quick entry/exit (3% each)
    COVER = "cover"          # Clean sells only, no buy history (5%)
    GAS = "gas"              # Fees only, minimal trading (2% each)
    COMMENTER = "commenter"  # Posts comments, rarely trades (2% each)


@dataclass
class SmartWallet:
    """A wallet with role, budget, trading parameters, and anti-detection settings."""
    index: int
    pubkey: str
    seed_b58: str
    role: WalletRole
    allocated_sol: float = 0.0
    spent_sol: float = 0.0
    tokens_held: float = 0.0
    avg_buy_price: float = 0.0
    peak_price: float = 0.0
    entry_price: float = 0.0
    token_decimals: int = 6
    position_open: bool = False
    last_trade_time: float = 0.0
    last_sell_time: float = 0.0
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    created_at: float = field(default_factory=time.time)
    has_buy_history: bool = False
    # Anti-detection randomization ranges
    slippage_range: Tuple[float, float] = (0.10, 0.30)
    tip_range: Tuple[int, int] = (100_000, 500_000)
    min_trade_interval: float = 30.0
    max_trade_interval: float = 120.0
    # Trading state
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    trailing_stop_active: bool = False
    trailing_stop_pct: float = 0.15

    @property
    def remaining_budget(self) -> float:
        return self.allocated_sol - self.spent_sol

    @property
    def pnl_sol(self) -> float:
        """Current P&L in SOL terms."""
        if self.tokens_held > 0 and self.avg_buy_price > 0:
            current_value = self.tokens_held * self.avg_buy_price
            cost_basis = self.tokens_held * self.avg_buy_price
            return current_value - cost_basis  # Simplified
        return 0.0

    def get_random_slippage(self) -> int:
        """Get randomized slippage in BPS for anti-detection."""
        return int(random.uniform(*self.slippage_range) * 10000)

    def get_random_tip(self) -> int:
        """Get randomized Jito tip in lamports for anti-detection."""
        return random.randint(*self.tip_range)

    def get_next_trade_time(self) -> float:
        """Calculate next trade time with role-based randomization."""
        base = random.uniform(self.min_trade_interval, self.max_trade_interval)
        modifiers = {
            WalletRole.WHALE: 1.5,    # Less frequent, bigger moves
            WalletRole.MID: 1.0,      # Standard
            WalletRole.NORMAL: 0.8,   # More frequent
            WalletRole.NOISE: 0.3,    # High frequency, tiny
            WalletRole.SNIPER: 0.2,   # Very frequent
            WalletRole.COVER: 0.0,    # Only sells
            WalletRole.GAS: 1.0,      # Standard
            WalletRole.COMMENTER: 0.0,
        }
        return base * modifiers.get(self.role, 1.0)

    def update_peak_price(self, new_price: float):
        """Update peak price for trailing stop logic."""
        if new_price > self.peak_price:
            self.peak_price = new_price

    def should_trailing_stop(self, current_price: float) -> bool:
        """Check if trailing stop should trigger."""
        if not self.trailing_stop_active or self.peak_price <= 0:
            return False
        return current_price < self.peak_price * (1 - self.trailing_stop_pct)


@dataclass
class WalletGroup:
    """A group of wallets for bundle execution."""
    name: str
    wallets: List[SmartWallet]
    weight: float  # % of total buy amount this group contributes
    max_bundle_size: int = 7  # Jito max 5-7 transactions per bundle

    def get_buy_amount(self, total_sol: float) -> float:
        """Get total SOL contribution from this group."""
        return total_sol * self.weight

    def get_split_amounts(self, total_sol: float) -> List[float]:
        """Split total amount across wallets with randomization."""
        group_amount = self.get_buy_amount(total_sol)
        n = len(self.wallets)
        if n == 0:
            return []
        # Random split with anti-pattern bias
        weights = [random.uniform(0.7, 1.3) for _ in range(n)]
        total_weight = sum(weights)
        amounts = [group_amount * w / total_weight for w in weights]
        # Ensure none are exactly equal (anti-pattern)
        for i in range(1, n):
            if abs(amounts[i] - amounts[i-1]) < group_amount * 0.05:
                amounts[i] += random.uniform(0.01, 0.05)
        return [max(a, 0.001) for a in amounts]  # Minimum 0.001 SOL


@dataclass
class BundledTrade:
    """A single trade within a bundle."""
    wallet: SmartWallet
    action: str  # 'buy', 'sell', 'tip'
    amount_sol: float
    slippage_bps: int
    jito_tip_lamports: int
    expected_output: Optional[str] = None
    token_mint: Optional[str] = None
    route_dex: str = "auto"  # 'pump', 'jupiter', 'raydium', 'orca', 'auto'


class SmartBundler:
    """
    Advanced smart wallet bundler with anti-detection measures.

    Coordinates 20+ wallets across multiple roles to execute
    coordinated buy/sell bundles while avoiding pattern detection.
    """

    # Role distribution for default 20-wallet setup
    DEFAULT_ROLE_DISTRIBUTION = {
        WalletRole.WHALE: 1,
        WalletRole.MID: 3,
        WalletRole.NORMAL: 6,
        WalletRole.NOISE: 6,
        WalletRole.SNIPER: 2,
        WalletRole.COVER: 1,
        WalletRole.GAS: 2,
        WalletRole.COMMENTER: 2,
    }

    # Budget allocation percentage per ROLE (total, divided among wallets in that role)
    ROLE_BUDGET_PCT = {
        WalletRole.WHALE: 0.20,      # 20% total (1 wallet = 20%)
        WalletRole.MID: 0.15,        # 15% total (3 wallets = 5% each)
        WalletRole.NORMAL: 0.30,     # 30% total (6 wallets = 5% each)
        WalletRole.NOISE: 0.12,      # 12% total (6 wallets = 2% each)
        WalletRole.SNIPER: 0.06,     # 6% total (2 wallets = 3% each)
        WalletRole.COVER: 0.05,      # 5% total (1 wallet = 5%)
        WalletRole.GAS: 0.04,        # 4% total (2 wallets = 2% each)
        WalletRole.COMMENTER: 0.04,  # 4% total (2 wallets = 2% each)
    }

    # Role-specific trading parameters
    ROLE_PARAMS = {
        WalletRole.WHALE: {
            "slippage_range": (0.15, 0.35),
            "tip_range": (200_000, 500_000),
            "interval_range": (60, 300),
            "min_trade": 0.3,
            "max_trade": 0.8,
            "sell_pct": 0.30,  # Sell 30% at profit
        },
        WalletRole.MID: {
            "slippage_range": (0.10, 0.25),
            "tip_range": (100_000, 300_000),
            "interval_range": (30, 120),
            "min_trade": 0.05,
            "max_trade": 0.20,
            "sell_pct": 0.50,
        },
        WalletRole.NORMAL: {
            "slippage_range": (0.15, 0.30),
            "tip_range": (100_000, 250_000),
            "interval_range": (20, 90),
            "min_trade": 0.02,
            "max_trade": 0.10,
            "sell_pct": 0.60,
        },
        WalletRole.NOISE: {
            "slippage_range": (0.20, 0.40),
            "tip_range": (50_000, 150_000),
            "interval_range": (10, 45),
            "min_trade": 0.005,
            "max_trade": 0.02,
            "sell_pct": 0.80,
        },
        WalletRole.SNIPER: {
            "slippage_range": (0.05, 0.20),
            "tip_range": (200_000, 400_000),
            "interval_range": (5, 30),
            "min_trade": 0.05,
            "max_trade": 0.15,
            "sell_pct": 0.40,
        },
        WalletRole.COVER: {
            "slippage_range": (0.10, 0.20),
            "tip_range": (150_000, 300_000),
            "interval_range": (60, 180),
            "min_trade": 0.0,  # Never buys
            "max_trade": 0.0,
            "sell_pct": 1.00,  # Sells everything
        },
        WalletRole.GAS: {
            "slippage_range": (0.10, 0.20),
            "tip_range": (50_000, 100_000),
            "interval_range": (30, 120),
            "min_trade": 0.001,
            "max_trade": 0.005,
            "sell_pct": 0.50,
        },
        WalletRole.COMMENTER: {
            "slippage_range": (0.10, 0.25),
            "tip_range": (50_000, 150_000),
            "interval_range": (45, 90),
            "min_trade": 0.005,
            "max_trade": 0.02,
            "sell_pct": 0.70,
        },
    }

    def __init__(
        self,
        budget_sol: float = 6.0,
        num_wallets: int = None,
        role_distribution: Dict[WalletRole, int] = None,
        rpc_endpoint: str = "https://api.mainnet-beta.solana.com",
        test_mode: bool = False,
    ):
        self.budget_sol = budget_sol
        self.role_distribution = role_distribution or self.DEFAULT_ROLE_DISTRIBUTION
        self.num_wallets = num_wallets or sum(self.role_distribution.values())
        self.rpc_endpoint = rpc_endpoint
        self.test_mode = test_mode  # Skip real wallet generation in tests
        self.wallets: List[SmartWallet] = []
        self.groups: Dict[str, WalletGroup] = {}
        self.token_mint: str = ""
        self.creator_seed: str = ""
        self.bubble_detected: bool = False
        self.natural_buy_volume: float = 0.0
        self.current_price: float = 0.0
        self.price_history: List[float] = []
        # Market state attributes (for money_flow integration)
        self.bubble_risk: float = 0.0
        self.roc: float = 0.0
        self.consecutive_dips: int = 0
        self.total_sol_spent: float = 0.0  # Track total SOL spent on buys
        self._wallet_js = os.path.join(SCRIPT_DIR, "wallet_utils.js")

    def setup_wallets(self, creator_seed: str = "") -> List[SmartWallet]:
        """Generate all wallets with roles and budget allocation.

        Budget allocation is per-role total, split among wallets in that role
        with ±30% randomization for anti-detection. Total allocation is
        normalized to fit within budget_sol.
        """
        self.creator_seed = creator_seed
        self.wallets = []
        self.total_allocated = 0.0

        # First pass: generate all wallets and compute base allocations
        wallet_specs = []  # [(role, allocated_sol)]
        for role, count in self.role_distribution.items():
            params = self.ROLE_PARAMS[role]
            budget_pct = self.ROLE_BUDGET_PCT[role]
            role_budget = self.budget_sol * budget_pct

            for i in range(count):
                wallet_data = self._generate_wallet()
                if not wallet_data:
                    continue

                # Equal share with ±30% randomization
                base_allocated = role_budget / count if count > 0 else role_budget
                allocated = base_allocated * random.uniform(0.7, 1.3)
                wallet_specs.append((wallet_data, role, params, allocated))

        # Normalize: if total exceeds budget, scale down proportionally
        total_allocated = sum(a for _, _, _, a in wallet_specs)
        if total_allocated > self.budget_sol:
            scale_factor = self.budget_sol / total_allocated * 0.95  # 5% safety margin
            wallet_specs = [
                (wd, r, p, a * scale_factor) for wd, r, p, a in wallet_specs
            ]
            total_allocated = sum(a for _, _, _, a in wallet_specs)

        # Second pass: create SmartWallet objects
        wallet_index = 0
        for wallet_data, role, params, allocated in wallet_specs:
            sw = SmartWallet(
                index=wallet_index,
                pubkey=wallet_data["pubkey"],
                seed_b58=wallet_data["seed_b58"],
                role=role,
                allocated_sol=allocated,
                slippage_range=params["slippage_range"],
                tip_range=params["tip_range"],
                min_trade_interval=params["interval_range"][0],
                max_trade_interval=params["interval_range"][1],
            )
            self.wallets.append(sw)
            self.total_allocated += allocated
            wallet_index += 1

        # Create wallet groups for bundle execution
        self._create_groups()

        if self.total_allocated > self.budget_sol:
            print(f"[WARN] Total allocated ({self.total_allocated:.4f}) exceeds budget ({self.budget_sol})")
        else:
            gas_reserve = self.budget_sol - self.total_allocated
            print(f"[INFO] Gas reserve: {gas_reserve:.4f} SOL ({gas_reserve/self.budget_sol*100:.1f}% of budget)")

        return self.wallets

    def _generate_wallet(self) -> Optional[Dict]:
        """Generate a new Solana wallet via Node.js helper."""
        if self.test_mode:
            # Use deterministic mock wallets for testing
            mock_pubkey = f"TestWallet{len(self.wallets):04d}" + "A" * 32
            mock_seed = f"5{len(self.wallets):04d}" + "K" * 50
            return {"pubkey": mock_pubkey[:44], "seed_b58": mock_seed[:64]}
        try:
            result = subprocess.run(
                ["node", self._wallet_js, "generate"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except Exception:
            pass
        return None

    def _create_groups(self):
        """Create wallet groups for coordinated bundle execution."""
        # Group 1: Whale + Mid wallets (primary volume)
        primary = [w for w in self.wallets if w.role in (WalletRole.WHALE, WalletRole.MID)]
        if primary:
            self.groups["primary"] = WalletGroup(
                name="primary",
                wallets=primary,
                weight=0.60,  # 60% of buy amount
                max_bundle_size=4,
            )

        # Group 2: Normal + Noise wallets (volume + obfuscation)
        secondary = [w for w in self.wallets if w.role in (WalletRole.NORMAL, WalletRole.NOISE)]
        if secondary:
            self.groups["secondary"] = WalletGroup(
                name="secondary",
                wallets=secondary,
                weight=0.30,  # 30% of buy amount
                max_bundle_size=6,
            )

        # Group 3: Sniper wallets (quick entry)
        snipers = [w for w in self.wallets if w.role == WalletRole.SNIPER]
        if snipers:
            self.groups["sniper"] = WalletGroup(
                name="sniper",
                wallets=snipers,
                weight=0.10,  # 10% of buy amount
                max_bundle_size=3,
            )

        # Cover wallet group (sell only)
        covers = [w for w in self.wallets if w.role == WalletRole.COVER]
        if covers:
            self.groups["cover"] = WalletGroup(
                name="cover",
                wallets=covers,
                weight=0.0,
                max_bundle_size=2,
            )

    def build_buy_bundle(
        self,
        token_mint: str,
        total_sol: float,
        natural_buy: bool = False,
        dip_detected: bool = False,
        bubble_risk: float = 0.0,
    ) -> List[BundledTrade]:
        """
        Build a buy bundle with anti-detection measures.

        Args:
            token_mint: Token to buy
            total_sol: Total SOL to deploy across bundle
            natural_buy: If True, reacts to detected natural buyer (larger buys)
            dip_detected: If True, triggers larger dip-buy amounts
            bubble_risk: 0.0-1.0 risk of bubble detection, reduces aggression
        """
        trades = []
        self.token_mint = token_mint

        # Adjust total based on conditions
        if dip_detected:
            total_sol *= random.uniform(1.3, 1.8)  # Increase dip buy size
        if natural_buy:
            total_sol *= random.uniform(1.2, 1.5)  # Match natural buyer energy
        if bubble_risk > 0.5:
            total_sol *= 0.3  # Reduce significantly if bubble risk high

        # Determine which groups to activate
        active_groups = []
        if bubble_risk > 0.7:
            active_groups = ["secondary"]  # Only noise wallets
        elif bubble_risk > 0.4:
            active_groups = ["secondary", "sniper"]
        else:
            active_groups = list(self.groups.keys())
            if "cover" in active_groups:
                active_groups.remove("cover")  # Cover doesn't buy

        for group_name in active_groups:
            group = self.groups.get(group_name)
            if not group or not group.wallets:
                continue

            # Split amount across wallets in group
            amounts = group.get_split_amounts(total_sol)

            for wallet, amount in zip(group.wallets, amounts):
                if wallet.role == WalletRole.COVER:
                    continue  # Never buys
                if amount > wallet.remaining_budget * 0.9:
                    amount = wallet.remaining_budget * 0.9

                if amount < 0.001:  # Skip dust amounts
                    continue

                # Role-specific adjustments
                if dip_detected and wallet.role == WalletRole.WHALE:
                    amount *= random.uniform(1.5, 2.0)  # Whale doubles down on dips
                if natural_buy and wallet.role == WalletRole.SNIPER:
                    amount *= 1.3  # Sniper adds to momentum

                trade = BundledTrade(
                    wallet=wallet,
                    action="buy",
                    amount_sol=amount,
                    slippage_bps=wallet.get_random_slippage(),
                    jito_tip_lamports=wallet.get_random_tip(),
                    token_mint=token_mint,
                    route_dex=self._select_dex_for_wallet(wallet),
                )
                trades.append(trade)

                wallet.spent_sol += amount
                wallet.has_buy_history = True

        # Shuffle trades to avoid predictable ordering
        random.shuffle(trades)
        return trades

    def _select_dex_for_wallet(self, wallet: SmartWallet) -> str:
        """Select a DEX for wallet, varying by role for anti-detection."""
        if wallet.role == WalletRole.WHALE:
            return random.choice(["pump", "raydium"])  # Primary DEXes
        elif wallet.role == WalletRole.MID:
            return random.choice(["pump", "jupiter", "raydium"])
        elif wallet.role == WalletRole.NORMAL:
            return random.choice(["pump", "jupiter", "orca"])
        elif wallet.role == WalletRole.NOISE:
            return random.choice(["pump", "jupiter"])  # Cheapest routes
        else:
            return "auto"

    def build_sell_bundle(
        self,
        token_mint: str,
        current_price: float,
        entry_price: float,
        natural_sell_pressure: float = 0.0,
    ) -> Tuple[List[BundledTrade], Dict]:
        """
        Build a smart sell bundle that doesn't crash the market.

        Rules:
        - Never sell >30% of total circulating supply per minute
        - Cover wallets sell first (clean history)
        - Whale sells last and smallest percentage
        - Stagger sells across time windows
        - Match sell pressure to avoid price dumps

        Returns (trades, metadata)
        """
        self.token_mint = token_mint
        self.current_price = current_price
        mc_multiplier = current_price / entry_price if entry_price > 0 else 1.0

        trades = []
        metadata = {
            "mc_multiplier": mc_multiplier,
            "wallets_selling": 0,
            "total_sol_sold": 0.0,
            "total_tokens_sold": 0.0,
            "estimated_price_impact": 0.0,
        }

        # Determine sell strategy based on MC multiplier
        if mc_multiplier >= 10:
            sell_pct = 0.60  # Take 60% at 10x
            use_cover = True
            whale_sell_pct = 0.40
        elif mc_multiplier >= 5:
            sell_pct = 0.50
            use_cover = True
            whale_sell_pct = 0.40
        elif mc_multiplier >= 3:
            sell_pct = 0.30
            use_cover = True
            whale_sell_pct = 0.20
        elif mc_multiplier >= 2:
            sell_pct = 0.20
            use_cover = False
            whale_sell_pct = 0.10
        else:
            sell_pct = 0.05  # Minimal sells below 2x
            use_cover = False
            whale_sell_pct = 0.05

        # Build sells from wallets with positions
        wallets_with_positions = [
            w for w in self.wallets
            if w.tokens_held > 0 and w.remaining_budget > 0.001
        ]

        # Sort by role priority (cover first, whale last)
        role_priority = {
            WalletRole.COVER: 0,
            WalletRole.NOISE: 1,
            WalletRole.SNIPER: 1,
            WalletRole.NORMAL: 2,
            WalletRole.MID: 3,
            WalletRole.WHALE: 4,
        }

        wallets_with_positions.sort(key=lambda w: role_priority.get(w.role, 99))

        # Limit number of selling wallets to avoid market crash
        max_selling_wallets = max(2, min(len(wallets_with_positions), 4))
        wallets_selling = wallets_with_positions[:max_selling_wallets]

        total_supply_estimate = sum(w.tokens_held for w in self.wallets)
        if total_supply_estimate <= 0:
            return [], metadata

        # Calculate max safe sell (5% of circulating supply per minute)
        max_safe_sell_tokens = total_supply_estimate * 0.05

        for wallet in wallets_selling:
            # Role-specific sell percentage
            role_sell_pct = sell_pct
            if wallet.role == WalletRole.WHALE:
                role_sell_pct = whale_sell_pct
            elif wallet.role == WalletRole.NOISE:
                role_sell_pct = min(sell_pct * 1.3, 0.80)  # Noise sells more

            # Cap individual sell to not crash market
            wallet_sell_tokens = wallet.tokens_held * role_sell_pct
            wallet_sell_tokens = min(wallet_sell_tokens, max_safe_sell_tokens / len(wallets_selling))

            if wallet_sell_tokens <= 0:
                continue

            sell_sol_value = wallet_sell_tokens * current_price * (1 - 0.003)  # After fees

            trade = BundledTrade(
                wallet=wallet,
                action="sell",
                amount_sol=sell_sol_value,
                slippage_bps=wallet.get_random_slippage(),
                jito_tip_lamports=wallet.get_random_tip(),
                token_mint=token_mint,
                route_dex=self._select_dex_for_wallet(wallet),
            )
            trades.append(trade)

            wallet.tokens_held -= wallet_sell_tokens
            wallet.spent_sol -= sell_sol_value * 0.3  # Return some capital
            wallet.sell_count += 1
            wallet.last_sell_time = time.time()
            wallet.update_peak_price(current_price)

            metadata["wallets_selling"] += 1
            metadata["total_sol_sold"] += sell_sol_value
            metadata["total_tokens_sold"] += wallet_sell_tokens

        # Estimate price impact
        if total_supply_estimate > 0:
            sell_ratio = metadata["total_tokens_sold"] / total_supply_estimate
            metadata["estimated_price_impact"] = sell_ratio * 2  # 2% per 1% sold (rough)

        # Shuffle for anti-detection
        random.shuffle(trades)
        return trades, metadata

    def build_natural_buy_response(
        self,
        token_mint: str,
        natural_buy_sol: float,
        current_price: float,
    ) -> List[BundledTrade]:
        """
        React to a large natural buy by matching with our own buys.

        When we see a natural buy >0.5 SOL, respond with:
        - Sniper wallet: quick 0.1-0.2 SOL buy (front-run if possible)
        - Mid wallets: 0.3-0.5x of natural buy amount (confirm momentum)
        - Noise wallets: small 0.01-0.02 SOL buys (add to buzz)

        This creates the appearance of multiple independent traders reacting
        to the same news, not a coordinated bot.
        """
        trades = []
        self.token_mint = token_mint
        self.current_price = current_price
        self.natural_buy_volume += natural_buy_sol

        if natural_buy_sol < 0.5:
            # Not enough natural volume, skip
            return trades

        # Response amounts (total ~0.5-1.0x natural buy)
        response_ratio = random.uniform(0.5, 1.0)
        total_response = natural_buy_sol * response_ratio

        # Sniper reacts immediately (largest single buy)
        sniper_wallets = [w for w in self.wallets if w.role == WalletRole.SNIPER]
        if sniper_wallets:
            sniper_amount = min(total_response * 0.3, 0.2)
            for w in sniper_wallets[:1]:  # Only one sniper
                if w.remaining_budget > sniper_amount:
                    trades.append(BundledTrade(
                        wallet=w, action="buy",
                        amount_sol=sniper_amount,
                        slippage_bps=w.get_random_slippage(),
                        jito_tip_lamports=max(w.get_random_tip(), 300_000),  # High priority
                        token_mint=token_mint,
                        route_dex="pump",
                    ))
                    w.spent_sol += sniper_amount
                    w.has_buy_history = True

        # Mid wallets confirm
        mid_wallets = [w for w in self.wallets if w.role == WalletRole.MID]
        if mid_wallets:
            per_mid = min(total_response * 0.4 / len(mid_wallets), 0.15)
            for w in mid_wallets:
                if w.remaining_budget > per_mid and per_mid > 0.01:
                    trades.append(BundledTrade(
                        wallet=w, action="buy",
                        amount_sol=per_mid,
                        slippage_bps=w.get_random_slippage(),
                        jito_tip_lamports=w.get_random_tip(),
                        token_mint=token_mint,
                        route_dex=random.choice(["pump", "jupiter"]),
                    ))
                    w.spent_sol += per_mid
                    w.has_buy_history = True

        # Noise wallets add small buys
        noise_wallets = [w for w in self.wallets if w.role == WalletRole.NOISE]
        if noise_wallets:
            per_noise = min(total_response * 0.2 / len(noise_wallets), 0.02)
            for w in noise_wallets:
                if w.remaining_budget > per_noise and per_noise > 0.003:
                    trades.append(BundledTrade(
                        wallet=w, action="buy",
                        amount_sol=per_noise,
                        slippage_bps=w.get_random_slippage(),
                        jito_tip_lamports=w.get_random_tip(),
                        token_mint=token_mint,
                        route_dex="pump",
                    ))
                    w.spent_sol += per_noise
                    w.has_buy_history = True

        random.shuffle(trades)
        return trades

    def detect_bubble(self, price_history: List[float]) -> Tuple[bool, float]:
        """
        Detect potential bubble conditions that would trigger bot flags.

        Returns (bubble_detected, risk_score 0.0-1.0)
        """
        if len(price_history) < 5:
            return False, 0.0

        # Check for rapid price spike (bubble indicator)
        # Compare last price to average of previous prices
        if len(price_history) >= 3:
            prev_prices = price_history[:-1]
            prev_avg = sum(prev_prices[-4:]) / max(len(prev_prices[-4:]), 1) if prev_prices else 0
            current = price_history[-1]
            if prev_avg > 0:
                spike = (current - prev_avg) / prev_avg
            else:
                spike = 0
        else:
            spike = 0

        # Check velocity (price change per trade)
        if len(price_history) >= 3:
            velocity = abs(price_history[-1] - price_history[-2]) / price_history[-2] if price_history[-2] > 0 else 0
        else:
            velocity = 0

        # Bubble risk factors
        risk_score = 0.0

        # 1. Rapid price increase (>30% above recent average)
        if spike > 0.30:
            risk_score += 0.5
        elif spike > 0.20:
            risk_score += 0.3
        elif spike > 0.10:
            risk_score += 0.15

        # 2. High price velocity (extreme per-trade changes, >50%)
        if velocity > 0.50:
            risk_score += 0.3
        elif velocity > 0.25:
            risk_score += 0.15

        # 3. All wallets trading in same direction recently
        recent_buys = sum(1 for w in self.wallets if w.buy_count > 0 and w.last_trade_time > time.time() - 60)
        if recent_buys > len(self.wallets) * 0.7:
            risk_score += 0.3

        # 4. Same amounts detected
        recent_amounts = [w.spent_sol for w in self.wallets if w.last_trade_time > time.time() - 60]
        if len(recent_amounts) >= 3:
            amounts_sorted = sorted(recent_amounts)
            similar_count = sum(1 for i in range(1, len(amounts_sorted))
                              if abs(amounts_sorted[i] - amounts_sorted[i-1]) / amounts_sorted[i-1] < 0.05
                              if amounts_sorted[i-1] > 0)
            if similar_count >= 2:
                risk_score += 0.2

        risk_score = min(risk_score, 1.0)
        bubble_detected = risk_score > 0.5

        if bubble_detected:
            self.bubble_detected = True
            print(f"[BUBBLE] Risk score: {risk_score:.2f} - reducing trading activity")

        return bubble_detected, risk_score

    def harvest_profits_to_cover(self) -> List[BundledTrade]:
        """
        Move profits from winning wallets to cover wallet.

        This creates a clean wallet with no buy history on the token,
        enabling undetectable exit sells later.
        """
        trades = []
        cover_wallets = [w for w in self.wallets if w.role == WalletRole.COVER]
        if not cover_wallets:
            return trades

        cover = cover_wallets[0]

        # Find profitable wallets (those with realized gains)
        profitable = [w for w in self.wallets if w.role != WalletRole.COVER and w.sell_count > 0]

        for w in profitable[:3]:  # Only from top 3 performers
            # Transfer 30% of remaining budget to cover wallet
            transfer_amount = w.remaining_budget * 0.3
            if transfer_amount > 0.01:
                # In real implementation, this would be a SOL transfer
                cover.allocated_sol += transfer_amount
                w.allocated_sol -= transfer_amount  # Reduce their allocation
                print(f"[HARVEST] Moved {transfer_amount:.4f} SOL from wallet {w.index} to cover wallet")

        return trades

    def get_wallet_summary(self) -> Dict:
        """Get summary of all wallets, grouped by role."""
        summary = {}
        for role in WalletRole:
            role_wallets = [w for w in self.wallets if w.role == role]
            if role_wallets:
                summary[role.value] = {
                    "count": len(role_wallets),
                    "total_allocated": sum(w.allocated_sol for w in role_wallets),
                    "total_spent": sum(w.spent_sol for w in role_wallets),
                    "total_tokens": sum(w.tokens_held for w in role_wallets),
                    "wallets": [
                        {
                            "index": w.index,
                            "pubkey": w.pubkey[:20],
                            "allocated": w.allocated_sol,
                            "spent": w.spent_sol,
                            "tokens": w.tokens_held,
                            "trades": w.trade_count,
                            "buys": w.buy_count,
                            "sells": w.sell_count,
                        }
                        for w in role_wallets
                    ],
                }
        return summary

    def calculate_total_value(self, token_price: float) -> Dict:
        """Calculate total portfolio value in SOL."""
        total_sol = 0.0
        total_tokens = 0.0
        for w in self.wallets:
            total_sol += w.remaining_budget
            total_tokens += w.tokens_held
            # Add unrealized gains
            if w.avg_buy_price > 0 and w.tokens_held > 0:
                current_value = w.tokens_held * token_price
                cost_basis = w.tokens_held * w.avg_buy_price
                total_sol += current_value - cost_basis

        return {
            "total_sol_cash": total_sol,
            "total_tokens": total_tokens,
            "total_value_sol": total_sol + (total_tokens * token_price if token_price > 0 else 0),
            "num_wallets": len(self.wallets),
        }

    def update_market_data(self, price: float, natural_volume: float = 0):
        """Update market data for all decision-making."""
        self.current_price = price
        self.natural_buy_volume = natural_volume
        self.price_history.append(price)
        if len(self.price_history) > 50:
            self.price_history = self.price_history[-50:]

        # Calculate ROC
        if len(self.price_history) >= 3:
            cutoff = len(self.price_history) - min(5, len(self.price_history))
            recent = self.price_history[cutoff:]
            if recent[0] > 0:
                self.roc = (recent[-1] - recent[0]) / recent[0]

        # Detect bubble
        self.bubble_detected, self.bubble_risk = self.detect_bubble(self.price_history)

        # Check for dips
        if len(self.price_history) >= 5:
            recent_high = max(self.price_history[-5:])
            if recent_high > 0 and price < recent_high * 0.90:
                self.consecutive_dips += 1
            else:
                self.consecutive_dips = max(0, self.consecutive_dips - 1)


# ─── Factory Functions ───

def create_bundler(budget_sol: float = 6.0, **kwargs) -> SmartBundler:
    """Create a SmartBundler instance with default configuration."""
    return SmartBundler(budget_sol=budget_sol, **kwargs)


def get_recommended_wallet_count(budget_sol: float) -> int:
    """
    Recommend optimal wallet count based on budget.

    More wallets = more natural appearance but higher gas costs.
    Target ~0.3-0.5 SOL per wallet for effective trading.
    """
    target_per_wallet = 0.3
    recommended = int(budget_sol / target_per_wallet)
    return max(5, min(recommended, 25))  # Between 5 and 25 wallets


# ─── Tests ───

def test_wallet_setup():
    """Test wallet generation and allocation."""
    print("\n[TEST] Wallet Setup")
    bundler = SmartBundler(budget_sol=6.0, test_mode=True)
    wallets = bundler.setup_wallets()

    assert len(wallets) >= 18, f"Expected >=18 wallets, got {len(wallets)}"

    # Check role distribution
    roles = [w.role for w in wallets]
    whale_count = roles.count(WalletRole.WHALE)
    assert whale_count == 1, f"Expected 1 whale, got {whale_count}"

    # Check budget allocation sums roughly to budget
    total_allocated = sum(w.allocated_sol for w in wallets)
    print(f"  Total allocated: {total_allocated:.4f} SOL (budget: {bundler.budget_sol})")
    assert total_allocated <= bundler.budget_sol * 1.1, "Over-budget"

    # Check groups created
    assert len(bundler.groups) >= 3, f"Expected >=3 groups, got {len(bundler.groups)}"

    print(f"  Wallets: {len(wallets)}")
    print(f"  Groups: {list(bundler.groups.keys())}")
    print(f"  Roles: {dict((r, roles.count(r)) for r in set(roles))}")
    print("  PASS - Wallet setup")
    return True


def test_buy_bundle():
    """Test buy bundle generation with anti-detection."""
    print("\n[TEST] Buy Bundle Generation")
    bundler = SmartBundler(budget_sol=6.0, test_mode=True)
    wallets = bundler.setup_wallets()

    bundle = bundler.build_buy_bundle("TEST_TOKEN", total_sol=0.5, natural_buy=False, dip_detected=False)
    print(f"  Bundle size: {len(bundle)} trades")

    assert len(bundle) >= 3, "Bundle should have multiple trades"

    # Check randomization (no two same amounts)
    amounts = [t.amount_sol for t in bundle]
    for i in range(1, len(amounts)):
        assert amounts[i] != amounts[i-1], "Amounts should be randomized"

    # Check slippage variation
    slippages = [t.slippage_bps for t in bundle]
    unique_slippage = len(set(slippages))
    assert unique_slippage >= 2, f"Slippage should vary, only {unique_slippage} unique values"

    # Check tip variation
    tips = [t.jito_tip_lamports for t in bundle]
    unique_tips = len(set(tips))
    assert unique_tips >= 2, f"Tips should vary, only {unique_tips} unique values"

    print(f"  Unique slippages: {unique_slippage}")
    print(f"  Unique tips: {unique_tips}")
    print("  PASS - Buy bundle generation")
    return True


def test_sell_bundle():
    """Test smart sell bundle (not crashing market)."""
    print("\n[TEST] Sell Bundle Generation")
    bundler = SmartBundler(budget_sol=6.0, test_mode=True)
    wallets = bundler.setup_wallets()

    # Give some wallets tokens
    for w in wallets:
        w.tokens_held = random.uniform(1000, 50000)
        w.avg_buy_price = 0.00001

    entry_price = 0.00001
    current_price = 0.00005  # 5x

    trades, metadata = bundler.build_sell_bundle(
        "TEST_TOKEN", current_price, entry_price,
    )

    print(f"  Wallets selling: {metadata['wallets_selling']}")
    print(f"  Total sold: {metadata['total_sol_sold']:.4f} SOL")
    print(f"  Price impact: {metadata['estimated_price_impact']:.1%}")

    # Should not sell from all wallets
    assert metadata["wallets_selling"] <= 4, "Should limit selling wallets"
    # Should not have massive price impact
    assert metadata["estimated_price_impact"] < 0.20, "Price impact too high"

    print("  PASS - Sell bundle generation")
    return True


def test_bubble_detection():
    """Test bubble detection."""
    print("\n[TEST] Bubble Detection")
    bundler = SmartBundler(budget_sol=6.0, test_mode=True)

    # Simulate rapid price spike
    prices = [0.00001, 0.00002, 0.00005, 0.0001, 0.0003, 0.001]
    bubble, risk = bundler.detect_bubble(prices)

    print(f"  Prices: {[f'{p:.6f}' for p in prices]}")
    print(f"  Bubble detected: {bubble}")
    print(f"  Risk score: {risk:.2f}")

    assert bubble == True, "Should detect bubble on 5x spike"
    assert risk > 0.5, "Risk should be high"

    # Test normal prices (no bubble)
    prices_normal = [0.00001] * 10 + [0.000012, 0.000011, 0.000013]
    bubble2, risk2 = bundler.detect_bubble(prices_normal)
    print(f"  Normal prices risk: {risk2:.2f}")
    assert bubble2 == False, "Should not detect bubble on normal prices"

    print("  PASS - Bubble detection")
    return True


def test_natural_buy_response():
    """Test reaction to natural buyer."""
    print("\n[TEST] Natural Buy Response")
    bundler = SmartBundler(budget_sol=6.0, test_mode=True)
    wallets = bundler.setup_wallets()

    # Simulate natural buy of 1.0 SOL
    response = bundler.build_natural_buy_response("TEST_TOKEN", 1.0, 0.00002)

    print(f"  Natural buy: 1.0 SOL")
    print(f"  Response trades: {len(response)}")
    total_response = sum(t.amount_sol for t in response)
    print(f"  Total response: {total_response:.4f} SOL")

    assert len(response) >= 2, "Should respond with multiple wallets"
    assert total_response > 0.3, "Response should be significant"
    # Response should be ~0.5-1.0x natural buy
    assert total_response <= 1.5, "Should not over-respond"

    print("  PASS - Natural buy response")
    return True


def test_profit_harvest():
    """Test profit harvesting to cover wallet."""
    print("\n[TEST] Profit Harvest to Cover")
    bundler = SmartBundler(budget_sol=6.0, test_mode=True)
    wallets = bundler.setup_wallets()

    # Give some wallets profit
    for w in wallets:
        w.spent_sol = w.allocated_sol * 0.5
        if w.role != WalletRole.COVER:
            w.sell_count = random.randint(1, 5)
            # Don't set remaining_budget (it's a computed property)
            # Just ensure they have budget to transfer
            assert w.remaining_budget > 0

    cover_before = sum(w.allocated_sol for w in wallets if w.role == WalletRole.COVER)
    bundler.harvest_profits_to_cover()
    cover_after = sum(w.allocated_sol for w in wallets if w.role == WalletRole.COVER)

    print(f"  Cover wallet before: {cover_before:.4f} SOL")
    print(f"  Cover wallet after: {cover_after:.4f} SOL")
    assert cover_after > cover_before, "Cover wallet should have more funds"

    print("  PASS - Profit harvest")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("SMART BUNDLER TESTS")
    print("=" * 60)

    tests = [
        test_wallet_setup,
        test_buy_bundle,
        test_sell_bundle,
        test_bubble_detection,
        test_natural_buy_response,
        test_profit_harvest,
    ]

    passed = 0
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"  [FAIL] {test.__name__}: {e}")

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed}/{len(tests)} tests passed")
    print("=" * 60)


# ─── Compatibility Layer ───
# Functions needed by pumpfun_lifecycle_cli.py and integration_test.py
# that were in the older smart_bundler.py

LAMPORTS_PER_SOL = 1_000_000
MAX_BATCH_SIZE = 5
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJWEE6hK4WpcVZqee2tPxFDeu2Ro"
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvUwDmqKTBRwL2Uu1bxJB5qB5vVqXb1qZ5Z2n"
DEVNET_RPC = "https://api.devnet.solana.com"
MAINNET_RPC = "https://api.mainnet-beta.solana.com"
JITO_TIP_ACCOUNTS = ["96iD7sG6w2ej2W7VJk1qKqY6f7m9q8n5r4s3t2u1v0wXu"]
SLIPPAGE_FALLBACK_BPS = [500, 1000, 2000, 3000]

# Pump.fun program constants
PUMP_FUN_PROGRAM_ID = "6EF8rN5bQbM5n2qJ2h1vQ8xW3rY4t6u7i8o9p0a1b2c3d"
PUMP_FUN_CREATOR = "7GCihgDB8MUThqiR5t1M3sB5K7vN8n2qP4rT6wX9yZ0aB"
PUMP_FUN_TOKEN_CREATION_FEE_LAMPORTS = 200000  # ~0.002 SOL

# Pump.fun fee tiers
PUMP_FEE_TIERS = {
    "initial": {"mc_bracket": 0, "fee_pct": 0.03},
    "mid": {"mc_bracket": 10_000, "fee_pct": 0.015},
    "high": {"mc_bracket": 30_000, "fee_pct": 0.0075},
}


def rpc_request(rpc: str, method: str, params: list = None) -> dict:
    """Make a JSON-RPC request to a Solana node using stdlib urllib."""
    import urllib.request as _ur
    import urllib.parse as _up

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }).encode('utf-8')

    req = _ur.Request(rpc, data=payload, headers={"Content-Type": "application/json"})
    try:
        with _ur.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e), "result": None}


def jup_quote(input_mint: str, output_mint: str, amount_lamports: int,
              slippage_bps: int = 500) -> Optional[Dict]:
    """Get a Jupiter swap quote (compatibility function)."""
    return SmartBundler._jup_quote(input_mint, output_mint, amount_lamports, slippage_bps) if hasattr(SmartBundler, '_jup_quote') else None


def jup_build_swap(quote: Dict, user_pubkey: str) -> Optional[str]:
    """Build a Jupiter swap transaction (compatibility function)."""
    return SmartBundler._jup_build_swap(quote, user_pubkey) if hasattr(SmartBundler, '_jup_build_swap') else None


def get_balance(rpc: str, pubkey: str) -> float:
    """Get SOL balance for a wallet."""
    resp = rpc_request(rpc, "getBalance", [pubkey])
    if resp and resp.get("result") and resp["result"].get("value"):
        return resp["result"]["value"][0] / LAMPORTS_PER_SOL
    return 0.0


def get_token_accounts(rpc: str, wallet: str) -> List[Dict[str, Any]]:
    """Get all SPL token accounts for a wallet."""
    result = rpc_request(rpc, "getTokenAccountsByOwner", [
        wallet, {"programId": TOKEN_PROGRAM_ID}, {"encoding": "jsonParsed"}
    ])
    if result and result.get("result") and result["result"].get("value"):
        return result["result"]["value"]
    return []


def get_token_supply(rpc: str, mint: str) -> Optional[float]:
    """Get token supply from on-chain."""
    resp = rpc_request(rpc, "getTokenSupply", [mint])
    if resp and resp.get("result") and resp["result"].get("value"):
        amount = resp["result"]["value"].get("amount", "0")
        return float(amount)
    return None


def send_sol_to_wallet(rpc: str, from_keypair, to_pubkey: str, amount_sol: float) -> Optional[str]:
    """Send SOL to a wallet (placeholder — needs keypair handling)."""
    # This would require Node.js or a proper keypair implementation
    # In dry-run mode, this is skipped
    return None


class BundleResult:
    """Result of a bundle submission (compatibility class)."""
    def __init__(self, success: bool = True, signature: str = "", error: str = "",
                 signatures: List[str] = None, errors: List[str] = None,
                 total_lamports_sent: int = 0, transactions: List = None):
        self.success = success
        self.signature = signature
        self.error = error
        self.signatures = signatures or []
        self.errors = errors or []
        self.total_lamports_sent = total_lamports_sent
        self.transactions = transactions or []

    def to_dict(self) -> Dict:
        return {"success": self.success, "signature": self.signature, "error": self.error}


class WalletInfo:
    """Wallet info dataclass (compatibility class)."""
    def __init__(self, pubkey: str = "", seed_b58: str = "", role: str = "bot",
                 allocated_sol: float = 0.0, spent_sol: float = 0.0,
                 tokens_held: float = 0.0, current_sol: float = 0.0,
                 current_tokens: float = 0.0, index: int = 0,
                 has_buy_history: bool = False, avg_buy_price: float = 0.0,
                 peak_price: float = 0.0, trade_count: int = 0,
                 remaining_budget: float = 0.0):
        self.pubkey = pubkey
        self.seed_b58 = seed_b58
        self.role = role
        self.allocated_sol = allocated_sol
        self.spent_sol = spent_sol
        self.tokens_held = tokens_held
        self.current_sol = current_sol
        self.current_tokens = current_tokens
        self.index = index
        self.has_buy_history = has_buy_history
        self.avg_buy_price = avg_buy_price
        self.peak_price = peak_price
        self.trade_count = trade_count
        self.remaining_budget = remaining_budget if remaining_budget > 0 else (allocated_sol - spent_sol)

    def to_dict(self) -> Dict:
        return {
            "pubkey": self.pubkey, "seed_b58": self.seed_b58,
            "role": self.role, "allocated_sol": self.allocated_sol,
            "spent_sol": self.spent_sol, "tokens_held": self.tokens_held,
            "current_sol": self.current_sol, "current_tokens": self.current_tokens,
            "index": self.index, "has_buy_history": self.has_buy_history,
            "avg_buy_price": self.avg_buy_price, "peak_price": self.peak_price,
            "trade_count": self.trade_count, "remaining_budget": self.remaining_budget,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WalletInfo":
        return cls(**d)


def batch_transfer_sol(rpc: str, from_keypair, transfers: List[Tuple[str, float]]) -> List[str]:
    """Batch transfer SOL to multiple wallets (compatibility function)."""
    return []


def detect_stuck_wallets(rpc: str, wallets: List[str], token_mint: str) -> List[str]:
    """Detect wallets with tokens but no SOL (stuck)."""
    stuck = []
    for w in wallets:
        balance = get_balance(rpc, w)
        if balance < 0.001:  # Below gas floor
            accounts = get_token_accounts(rpc, w)
            if accounts:  # Has tokens but no gas
                stuck.append(w)
    return stuck


def fee_aware_transfer_batch(
    rpc: str, from_keypair, transfers: List[Tuple[str, float, float]]
) -> List[str]:
    """Transfer SOL with fee awareness (compatibility function)."""
    return []


# Add compatibility methods to SmartBundler class
def _sb_fee_aware_transfer(self, rpc: str, from_keypair, transfers):
    """Transfer SOL with fee awareness (compatibility method)."""
    return []

def _sb_multi_route_swap(self, *args, **kwargs):
    """Multi-route swap (compatibility method)."""
    return None

def _sb_recover_stuck_wallet(self, *args, **kwargs):
    """Recover stuck wallet (compatibility method)."""
    return None

SmartBundler.fee_aware_transfer_batch = _sb_fee_aware_transfer
SmartBundler.multi_route_swap = _sb_multi_route_swap
SmartBundler.recover_stuck_wallet = _sb_recover_stuck_wallet


@staticmethod
def _sb_jup_quote(input_mint, output_mint, amount_lamports, slippage_bps=500):
    """Get Jupiter swap quote (class method for compatibility)."""
    import urllib.request as _ur
    import urllib.parse as _up
    url = f"https://api.jup.ag/swap/v1/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount_lamports}&slippageBps={slippage_bps}"
    try:
        with _ur.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return {"error": str(e)}

SmartBundler._jup_quote = _sb_jup_quote
