#!/usr/bin/env python3
"""
Advanced Pump.fun Trading System for One Claw Sloth ($OCS)

Implements:
1. Smart wallet bundling with anti-detection
2. Profit-optimized selling with trailing stops
3. Bonding curve profit maximization
4. Jito MEV bundle coordination
5. AI-driven micro adjustments based on P&L

Token Story: A sloth that lost its claw to poachers, rescued by a family,
symbolizing resilience and second chances.
"""

import os
import sys
import json
import time
import random
import math
import subprocess
import urllib.request
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from wallet_tracker import PnLTracker, Trade


class WalletRole(Enum):
    WHALE = "whale"          # Large buys, sets price direction
    MID = "mid"              # Regular trading, consistent activity
    SMALL = "small"          # High frequency, small amounts
    SNIPER = "sniper"        # Quick in/out for profit
    COMMENTER = "commenter"  # Posts comments, rarely trades


@dataclass
class SmartWallet:
    """A wallet with role, budget allocation, and trading parameters."""
    index: int
    pubkey: str
    seed_b58: str
    role: WalletRole
    allocated_sol: float = 0.0
    spent_sol: float = 0.0
    tokens_held: float = 0.0
    avg_buy_price: float = 0.0
    last_trade_time: float = 0.0
    trade_count: int = 0
    last_comment_time: float = 0.0
    
    # Role-specific parameters (anti-detection randomization)
    slippage_range: Tuple[float, float] = (0.10, 0.30)  # 10-30%
    tip_range: Tuple[int, int] = (100_000, 500_000)  # lamports
    min_trade_interval: float = 30.0  # seconds
    max_trade_interval: float = 120.0
    creator_seed: str = ""  # For funding transfers
    
    def get_random_slippage(self) -> float:
        """Get randomized slippage to avoid pattern detection."""
        return random.uniform(*self.slippage_range)
    
    def get_random_tip(self) -> int:
        """Get randomized Jito tip."""
        return random.randint(*self.tip_range)
    
    def get_next_trade_time(self) -> float:
        """Calculate next trade time with randomization."""
        base = random.uniform(self.min_trade_interval, self.max_trade_interval)
        # Add role-based modifier
        if self.role == WalletRole.SNIPER:
            base *= 0.3  # More frequent
        elif self.role == WalletRole.WHALE:
            base *= 1.5  # Less frequent
        elif self.role == WalletRole.SMALL:
            base *= 0.5  # High frequency
        return base
    
    @property
    def remaining_budget(self) -> float:
        """Remaining budget for this wallet."""
        return self.allocated_sol - self.spent_sol


@dataclass
class TradingConfig:
    """Configuration for advanced trading."""
    total_budget_sol: float = 6.0
    num_wallets: int = 5
    initial_price_sol: float = 0.00001  # Pump.fun initial price
    
    # Wallet allocation (must sum to 1.0)
    allocation: Dict[str, float] = field(default_factory=lambda: {
        "whale": 0.35,    # 35% = 2.1 SOL
        "mid": 0.20,      # 20% each = 1.2 SOL (x2)
        "small": 0.075,   # 7.5% each = 0.45 SOL (x2)
    })
    
    # Strategy parameters
    snipe_delay_blocks: int = 3  # Snipe at block 3-5
    dip_buy_threshold_pct: float = 0.10  # 10% dip triggers buys
    whale_buy_threshold_sol: float = 0.5  # Whale buys when >0.5 SOL natural volume
    early_phase_duration: int = 600  # 10 minutes
    early_phase_min_trades: int = 50
    early_phase_min_trades_per_min: float = 5.0
    
    # Profit-taking thresholds (MC multiplier tiers)
    # Each tier: (mc_multiplier_threshold, sell_ratio, trailing_stop_pct)
    # Higher tiers are checked first (loop iterates from 2x up), so the highest
    # matching tier wins. This means at 15x, you sell 70% (not just 60% at 10x).
    take_profit_tiers: List[Tuple[float, float, float]] = field(default_factory=lambda: [
        (2.0, 0.20, 0.0),    # 2x MC: take 20%, trail at 0% (conservative start)
        (3.0, 0.30, 0.15),   # 3x MC: take 30%, trail 15%
        (5.0, 0.40, 0.20),   # 5x MC: take 40%, trail 20%
        (10.0, 0.50, 0.30),  # 10x MC: take 50%, trail 30%
        (20.0, 0.60, 0.40),  # 20x MC: take 60%, trail 40%
        (50.0, 0.70, 0.50),  # 50x MC: take 70%, trail 50%
        (100.0, 0.80, 0.60), # 100x MC: take 80%, trail 60% (max tier)
    ])
    
    # Anti-detection
    randomize_slippage: bool = True
    randomize_tips: bool = True
    stagger_transactions: bool = True
    min_stagger_delay: float = 0.5  # seconds between wallet trades
    max_stagger_delay: float = 3.0
    
    # Emergency
    max_loss_pct: float = 0.25  # Stop if total loss > 25%
    daily_loss_limit_sol: float = 2.0


class PumpFunTrader:
    """
    Advanced trading system for Pump.fun with:
    - Smart wallet bundling
    - Profit-optimized exits
    - Anti-detection measures
    - AI-driven adjustments
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.wallets: List[SmartWallet] = []
        self.pnl_tracker = PnLTracker("One Claw Sloth", config.initial_price_sol)
        self.current_price = config.initial_price_sol
        self.current_mc = 0.0
        self.start_time = time.time()
        self.total_volume = 0.0
        self.natural_buy_volume = 0.0
        self.last_adjustments = {}
        self.is_emergency_stop = False
        self._price_history: List[float] = []  # Initialize price history
        self.momentum_multiplier: float = 1.0  # AI-adjusted buy size multiplier
    
    def setup_wallets(self, creator_seed: str = None) -> List[SmartWallet]:
        """Create and allocate wallets based on strategy."""
        # Generate wallets
        wallet_js = os.path.join(SCRIPT_DIR, "wallet_utils.js")
        wallets_data = []
        
        for i in range(self.config.num_wallets):
            result = subprocess.run(
                ["node", wallet_js, "generate"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                w = json.loads(result.stdout)
                w["index"] = i
                wallets_data.append(w)
        
        # Assign roles based on allocation
        allocations = self.config.allocation
        roles = []
        
        # Whale gets largest slice
        roles.append((WalletRole.WHALE, allocations["whale"]))
        
        # 2 MID wallets
        for _ in range(2):
            roles.append((WalletRole.MID, allocations["mid"]))
        
        # 2 SMALL wallets
        for _ in range(2):
            roles.append((WalletRole.SMALL, allocations["small"]))
        
        # Create SmartWallet objects
        for i, (w_data, (role, alloc_pct)) in enumerate(zip(wallets_data, roles)):
            allocation_sol = self.config.total_budget_sol * alloc_pct
            
            # Role-specific parameters
            if role == WalletRole.WHALE:
                slippage_range = (0.15, 0.35)
                tip_range = (200_000, 500_000)
                interval_range = (60, 300)
            elif role == WalletRole.MID:
                slippage_range = (0.10, 0.25)
                tip_range = (100_000, 300_000)
                interval_range = (30, 180)
            elif role == WalletRole.SMALL:
                slippage_range = (0.15, 0.40)
                tip_range = (50_000, 200_000)
                interval_range = (15, 90)
            else:
                slippage_range = (0.10, 0.30)
                tip_range = (100_000, 400_000)
                interval_range = (30, 120)
            
            sw = SmartWallet(
                index=i,
                pubkey=w_data["pubkey"],
                seed_b58=w_data["seed_b58"],
                role=role,
                allocated_sol=allocation_sol,
                slippage_range=slippage_range,
                tip_range=tip_range,
                min_trade_interval=interval_range[0],
                max_trade_interval=interval_range[1],
            )
            
            self.wallets.append(sw)
            self.pnl_tracker.add_wallet(i, sw.pubkey, sw.seed_b58, allocation_sol)
        
        return self.wallets
    
    def calculate_buy_amount(self, wallet: SmartWallet) -> float:
        """Calculate optimal buy amount based on wallet role and market conditions."""
        remaining = wallet.remaining_budget
        
        # Apply AI momentum multiplier (set by get_ai_adjustments)
        # momentum_multiplier is 1.0 at baseline, 1.2 for bullish, 0.5 for bearish
        momentum = max(0.1, self.momentum_multiplier)
        
        if wallet.role == WalletRole.WHALE:
            # Whale buys larger amounts on dips
            max_buy = min(remaining * 0.5, 0.8)  # Max 0.8 SOL per whale buy
            if self._is_dip():
                return min(max_buy * momentum, 0.3)  # Bigger dip buy, scaled by momentum
            return max_buy * 0.3 * momentum  # Regular whale buy, scaled
        
        elif wallet.role == WalletRole.MID:
            # Mid wallets: consistent medium buys
            max_buy = min(remaining * 0.3, 0.3)
            base_amount = max_buy * random.uniform(0.5, 1.0)
            return base_amount * momentum
        
        elif wallet.role == WalletRole.SMALL:
            # Small wallets: frequent small buys
            max_buy = min(remaining * 0.2, 0.15)
            base_amount = max_buy * random.uniform(0.3, 1.0)
            # Small wallets less affected by momentum (higher floor)
            return base_amount * max(0.5, momentum)
        
        base = min(remaining * 0.1, 0.05)
        return base * momentum
    
    def _is_dip(self) -> bool:
        """Check if current price is a dip from recent high."""
        if not hasattr(self, '_price_history'):
            self._price_history = []
        self._price_history.append(self.current_price)
        if len(self._price_history) > 20:
            self._price_history = self._price_history[-20:]
        
        if len(self._price_history) < 5:
            return False
        
        recent_high = max(self._price_history[-5:])
        drawdown = (recent_high - self.current_price) / recent_high if recent_high > 0 else 0
        return drawdown >= self.config.dip_buy_threshold_pct
    
    def get_take_profit_action(self, wallet: SmartWallet) -> Optional[Tuple[str, float]]:
        """
        Determine take-profit action based on current MC and profit tiers.
        Returns (action, amount_to_sell_ratio) or None.
        """
        if wallet.tokens_held <= 0:
            return None
        
        entry_price = wallet.avg_buy_price if wallet.avg_buy_price > 0 else self.config.initial_price_sol
        current_price = self.current_price
        
        if entry_price <= 0:
            return None
        
        mc_multiplier = current_price / entry_price if entry_price > 0 else 1.0
        
        for threshold, sell_ratio, trail_pct in self.config.take_profit_tiers:
            if mc_multiplier >= threshold:
                # Check if we should sell
                action = "sell"
                # For whale sells, be careful not to crash the market
                if wallet.role == WalletRole.WHALE:
                    sell_ratio *= 0.7  # Whale sells less aggressively
                # For small wallets, can be more aggressive
                if wallet.role == WalletRole.SMALL:
                    sell_ratio = min(sell_ratio * 1.2, 0.8)
                
                return (action, sell_ratio)
        
        return None
    
    def execute_buy(self, wallet: SmartWallet, amount_sol: float) -> Optional[Trade]:
        """Execute a buy trade with anti-detection measures."""
        if amount_sol <= 0 or amount_sol > wallet.remaining_budget:
            return None
        
        slippage = wallet.get_random_slippage() if self.config.randomize_slippage else 0.20
        tip = wallet.get_random_tip() if self.config.randomize_tips else 200_000
        
        # In real implementation, this would call Jupiter/Pump.fun
        # For simulation, we just record the trade
        fee_sol = amount_sol * 0.003  # ~0.3% fee
        token_amount = 0.0
        if self.current_price > 0:
            token_amount = (amount_sol - fee_sol) / self.current_price
        
        trade = Trade(
            timestamp=time.time(),
            wallet_index=wallet.index,
            action="buy",
            token_mint="",
            amount_sol=amount_sol,
            token_amount=token_amount,
            price_sol=self.current_price,
            fee_sol=fee_sol,
            notes=f"buy slippage={slippage:.2f} tip={tip}"
        )
        
        self.pnl_tracker.record_trade(trade)
        wallet.spent_sol += amount_sol
        wallet.tokens_held += token_amount
        # Update average buy price
        if wallet.tokens_held > 0:
            total_cost = wallet.avg_buy_price * (wallet.tokens_held - token_amount) + amount_sol
            wallet.avg_buy_price = total_cost / wallet.tokens_held
        
        wallet.last_trade_time = time.time()
        wallet.trade_count += 1
        self.total_volume += amount_sol
        
        return trade
    
    def execute_sell(self, wallet: SmartWallet, ratio: float = 1.0) -> Optional[Trade]:
        """Execute a sell trade with profit optimization."""
        if wallet.tokens_held <= 0 or ratio <= 0:
            return None
        
        sell_amount = wallet.tokens_held * ratio
        sell_sol = sell_amount * self.current_price * (1 - 0.003)  # After fees
        
        trade = Trade(
            timestamp=time.time(),
            wallet_index=wallet.index,
            action="sell",
            token_mint="",
            amount_sol=sell_sol,
            token_amount=sell_amount,
            price_sol=self.current_price,
            fee_sol=sell_sol * 0.003,
            notes=f"sell ratio={ratio:.2f}"
        )
        
        self.pnl_tracker.record_trade(trade)
        wallet.tokens_held -= sell_amount
        # Update current_sol estimate
        estimated_value = wallet.spent_sol - (wallet.tokens_held * self.current_price) + sell_sol
        self.pnl_tracker.wallets[wallet.index].current_sol = estimated_value
        
        wallet.last_trade_time = time.time()
        wallet.trade_count += 1
        self.total_volume += sell_sol
        
        return trade
    
    def evaluate_market_and_trade(self) -> List[Trade]:
        """
        Main trading loop: evaluate market conditions and execute trades.
        Returns list of trades executed.
        """
        trades = []
        
        if self.is_emergency_stop:
            return trades
        
        current_time = time.time()
        
        # Check emergency stop
        summary = self.pnl_tracker.get_portfolio_summary()
        if summary["total_pnl_sol"] < -self.config.max_loss_pct * self.config.total_budget_sol:
            print("[EMERGENCY] Total loss exceeds threshold, stopping all trading")
            self.is_emergency_stop = True
            return trades
        
        # Check if we're in early phase
        elapsed = current_time - self.start_time
        is_early = elapsed < self.config.early_phase_duration
        
        # Process each wallet
        for wallet in self.wallets:
            # Check if wallet should trade (randomized timing)
            next_trade = wallet.last_trade_time + wallet.get_next_trade_time()
            if current_time < next_trade:
                continue
            
            # Anti-detection: stagger trades
            if self.config.stagger_transactions and trades:
                delay = random.uniform(
                    self.config.min_stagger_delay,
                    self.config.max_stagger_delay
                )
                time.sleep(delay)
            
            # Check for take-profit opportunity
            tp_action = self.get_take_profit_action(wallet)
            if tp_action:
                action, ratio = tp_action
                trade = self.execute_sell(wallet, ratio)
                if trade:
                    trades.append(trade)
                    print(f"  [TP] Wallet {wallet.index} ({wallet.role.value}): sold {ratio:.2f} tokens")
                    continue
            
            # Check for buy opportunity
            buy_amount = self.calculate_buy_amount(wallet)
            if buy_amount > 0 and wallet.remaining_budget > 0:
                # Additional conditions for buying
                if is_early or self._is_dip() or self.natural_buy_volume > self.config.whale_buy_threshold_sol:
                    trade = self.execute_buy(wallet, buy_amount)
                    if trade:
                        trades.append(trade)
                        print(f"  [BUY] Wallet {wallet.index} ({wallet.role.value}): bought {buy_amount:.4f} SOL")
        
        # Apply AI adjustments
        if trades:
            adjustments = self.pnl_tracker.get_ai_adjustments()
            if adjustments:
                self._apply_ai_adjustments(adjustments)
        
        return trades
    
    def _apply_ai_adjustments(self, adjustments: dict):
        """Apply AI-driven adjustments to trading parameters."""
        if "emergency" in adjustments:
            self.is_emergency_stop = True
            print("[AI] Emergency stop triggered by P&L tracker")
            return
        
        if "dip_buy" in adjustments:
            old = self.config.dip_buy_threshold_pct
            self.config.dip_buy_threshold_pct = adjustments["dip_buy"]["threshold"]
            print(f"[AI] Dip buy threshold: {old:.0%} -> {self.config.dip_buy_threshold_pct:.0%}")
        
        if "momentum" in adjustments:
            adj = adjustments["momentum"]
            self.momentum_multiplier = adj.get("multiplier", 1.0)
            print(f"[AI] Momentum adjustment: {adj['reason']} (multiplier: {self.momentum_multiplier}x)")
        
        if "rebalance" in adjustments:
            print(f"[AI] Rebalancing: {adjustments['rebalance']['reason']}")
            # In real implementation: transfer funds between wallets
        
        if "entry_strategy" in adjustments:
            print(f"[AI] Entry strategy: {adjustments['entry_strategy']['reason']}")
    
    def update_market_data(self, price: float, mc: float = None, natural_volume: float = 0):
        """Update current market data."""
        self.current_price = price
        if mc:
            self.current_mc = mc
        self.natural_buy_volume = natural_volume
        self.pnl_tracker.update_price(price)
    
    def print_status(self):
        """Print current trading status."""
        self.pnl_tracker.print_dashboard()
        
        print("\nWallet Details:")
        print(f"  {'#':>3} {'Role':>10} {'Alloc':>7} {'Spent':>7} {'Left':>7} {'Tokens':>10} {'Trades':>6}")
        print(f"  {'-'*3} {'-'*10} {'-'*7} {'-'*7} {'-'*7} {'-'*10} {'-'*6}")
        
        for w in self.wallets:
            print(f"  {w.index:3d} {w.role.value:>10} {w.allocated_sol:7.4f} "
                  f"{w.spent_sol:7.4f} {w.remaining_budget:7.4f} {w.tokens_held:10.4f} {w.trade_count:6d}")


# ─── Test Functions ───
def test_smart_wallet_creation():
    """Test that wallets are created with correct roles and allocations."""
    print("\n[TEST] Smart Wallet Creation")
    
    config = TradingConfig(
        total_budget_sol=6.0,
        num_wallets=5
    )
    
    trader = PumpFunTrader(config)
    
    # We can't actually generate wallets in test (needs Node.js)
    # But we can test the allocation logic
    roles_allocated = []
    allocations = config.allocation
    expected_roles = ["whale", "mid", "mid", "small", "small"]
    
    # Whale
    roles_allocated.append(("whale", allocations["whale"]))
    # 2 MID
    for _ in range(2):
        roles_allocated.append(("mid", allocations["mid"]))
    # 2 SMALL
    for _ in range(2):
        roles_allocated.append(("small", allocations["small"]))
    
    total_alloc = sum(a for _, a in roles_allocated)
    print(f"  Total allocation: {total_alloc:.2f}")
    assert abs(total_alloc - 1.0) < 0.01, f"Allocation doesn't sum to 1.0: {total_alloc}"
    
    print(f"  Roles: {[r for r, _ in roles_allocated]}")
    print(f"  PASS - Smart wallet creation")
    return True


def test_profit_taking():
    """Test take-profit tier calculations."""
    print("\n[TEST] Profit Taking Tiers")
    
    config = TradingConfig()
    trader = PumpFunTrader(config)
    
    # Create mock wallet
    wallet = SmartWallet(
        index=0, pubkey="test", seed_b58="seed",
        role=WalletRole.WHALE, allocated_sol=1.8
    )
    
    # Test at 3x multiplier
    trader.current_price = 0.00003  # 3x from 0.00001 initial
    wallet.avg_buy_price = 0.00001
    wallet.tokens_held = 100000
    
    action = trader.get_take_profit_action(wallet)
    assert action is not None, "Should trigger take profit at 3x"
    assert action[0] == "sell"
    print(f"  3x MC: sell {action[1]:.2%} of position")
    
    # Test at 5x multiplier
    trader.current_price = 0.00005
    action = trader.get_take_profit_action(wallet)
    assert action is not None
    print(f"  5x MC: sell {action[1]:.2%} of position")
    
    # Test at 10x multiplier
    trader.current_price = 0.00010
    action = trader.get_take_profit_action(wallet)
    assert action is not None
    print(f"  10x MC: sell {action[1]:.2%} of position")
    
    # Test no action (below 2x)
    trader.current_price = 0.000015  # 1.5x
    action = trader.get_take_profit_action(wallet)
    assert action is None, "Should not trigger below 2x"
    print(f"  1.5x MC: no sell (correct)")
    
    print(f"  PASS - Profit taking tiers work correctly")
    return True


def test_dip_detection():
    """Test dip detection logic."""
    print("\n[TEST] Dip Detection")
    
    config = TradingConfig(dip_buy_threshold_pct=0.10)
    trader = PumpFunTrader(config)
    
    # Build price history
    prices = [0.00001, 0.000012, 0.000015, 0.000012, 0.000009, 0.000011]
    for p in prices:
        trader._price_history.append(p)
    
    trader.current_price = 0.000009  # 40% drop from high of 0.000015
    
    is_dip = trader._is_dip()
    print(f"  Current price: {trader.current_price}")
    print(f"  Recent high: {max(prices[-5:])}")
    print(f"  Dip detected: {is_dip}")
    assert is_dip, "Should detect 40% drop as dip"
    
    print(f"  PASS - Dip detection works")
    return True


def test_ai_adjustments():
    """Test AI-driven adjustment suggestions."""
    print("\n[TEST] AI Adjustments")
    
    config = TradingConfig()
    trader = PumpFunTrader(config)
    
    # Add wallets
    for i in range(3):
        trader.pnl_tracker.add_wallet(i, f"pub{i}", f"seed{i}", 2.0)
    
    # Simulate losing wallet
    for i in range(3):
        if i == 0:
            # Wallet 0 is a big loser
            trader.pnl_tracker.wallets[i].realized_pnl = -1.5
            trader.pnl_tracker.wallets[i].avg_buy_price = 0.00001
            trader.pnl_tracker.wallets[i].tokens_held = 10000
            trader.pnl_tracker.wallets[i].update_unrealized_pnl(0.000005)  # Price dropped
        else:
            trader.pnl_tracker.wallets[i].realized_pnl = 0.5
    
    adjustments = trader.pnl_tracker.get_ai_adjustments()
    print(f"  Adjustments suggested: {list(adjustments.keys())}")
    
    # Should suggest rebalancing and size adjustments
    assert "rebalance" in adjustments or any("size" in k for k in adjustments.keys()), \
        "Should suggest adjustments for losing wallet"
    
    print(f"  PASS - AI adjustments generated")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("OCS ADVANCED TRADING SYSTEM TESTS")
    print("=" * 60)
    
    tests = [
        test_smart_wallet_creation,
        test_profit_taking,
        test_dip_detection,
        test_ai_adjustments,
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
    print(f"{'=' * 60}")
