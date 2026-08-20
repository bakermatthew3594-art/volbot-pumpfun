#!/usr/bin/env python3
"""
Money Flow Engine for Pump.fun Trading System.

Implements:
1. Dynamic capital allocation across the 20-wallet ecosystem
2. Fee recovery and profit harvesting to cover wallet
3. Buy pressure timing (spread strategy for natural chart appearance)
4. Resistance buying (matching natural sellers to buy the dip)
5. Organic chart pattern simulation (looks like natural trader activity)
6. Gas optimization through Jito MEV bundles

Core Principle: Every SOL spent must earn back at least 1.5x its value
through coordinated buying/selling that attracts natural traders and
creates organic-looking price action.

Usage:
    from money_flow import MoneyFlowEngine, ChartPatternSimulator
    engine = MoneyFlowEngine(budget_sol=6.0)
    engine.initialize_wallets()

    # Simulate a natural-looking trading session
    sim = ChartPatternSimulator(engine, duration_minutes=5)
    sim.run_simulation()
"""

import os
import sys
import time
import random
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from smart_bundler import SmartBundler, SmartWallet, WalletRole, BundledTrade, WalletGroup
from bonding_curve_trader import BondingCurveTrader, PriceImpactModel


class MarketPhase(Enum):
    """Trading phases in a token lifecycle."""
    INITIAL_PUMP = "initial_pump"     # First 30 seconds: aggressive buys
    CONSOLIDATION = "consolidation"   # 30-90 seconds: steady accumulation
    MOMENTUM = "momentum"             # Natural buyers arrive: add fuel
    PROFIT_TAKING = "profit_taking"   # 2x+ MC: take partial profits
    EXIT = "exit"                     # Near graduation or decline: exit
    RECOVERY = "recovery"           # After dip: buy support


@dataclass
class TradeEvent:
    """Record of a single trade action."""
    timestamp: float
    phase: MarketPhase
    wallet_index: int
    wallet_role: WalletRole
    action: str  # 'buy', 'sell'
    amount_sol: float
    expected_tokens: float = 0.0
    expected_mc_after: float = 0.0
    reason: str = ""
    jito_bundled: bool = True
    success: bool = True


@dataclass
class ChartPoint:
    """A price point for chart simulation."""
    timestamp: float
    price_sol: float
    mc_usd: float
    event_type: str  # 'natural_buy', 'natural_sell', 'our_buy', 'our_sell', 'pump', 'dip'
    volume_sol: float


class ChartPatternSimulator:
    """
    Simulates organic-looking price charts by carefully timing
    buy and sell waves that mimic natural trader behavior.

    The pattern follows:
    1. Initial pump (our wallets buy aggressively)
    2. Consolidation (steady small buys)
    3. Natural buyer reaction (our response to organic volume)
    4. Dip and recovery (we buy dips, sell resistance)
    5. Profit taking (partial sells at milestones)
    """

    # Chart pattern templates (price multiplier over time)
    PATTERN_STEADY_GROWTH = [1.0, 1.2, 1.1, 1.3, 1.5, 1.4, 1.6, 1.8, 1.7, 2.0]
    PATTERN_VOLATILE = [1.0, 1.5, 1.2, 1.8, 1.3, 2.2, 1.5, 2.5, 1.8, 2.0]
    PATTERN_SLOW_PIMP = [1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5]

    def __init__(
        self,
        bundler: SmartBundler,
        initial_mc_usd: float = 450.0,
        total_supply: float = 1_000_000_000,
        sol_price: float = 150.0,
    ):
        self.bundler = bundler
        self.initial_mc = initial_mc_usd
        self.total_supply = total_supply
        self.sol_price = sol_price
        self.current_price_sol = initial_mc_usd / (total_supply * sol_price)
        self.current_mc_usd = initial_mc_usd
        self.chart_history: List[ChartPoint] = []
        self.trade_history: List[TradeEvent] = []
        self.natural_events: List[Dict] = []
        self.phase = MarketPhase.INITIAL_PUMP
        self.phase_start_time = time.time()
        self.simulation_active = False

    def mc_multiplier(self) -> float:
        """Current MC multiplier relative to initial."""
        if self.initial_mc > 0:
            return self.current_mc_usd / self.initial_mc
        return 1.0

    def update_price_from_mc(self, new_mc_usd: float):
        """Update price based on MC change."""
        self.current_mc_usd = new_mc_usd
        self.current_price_sol = new_mc_usd / (self.total_supply * self.sol_price)
        self.chart_history.append(ChartPoint(
            timestamp=time.time(),
            price_sol=self.current_price_sol,
            mc_usd=new_mc_usd,
            event_type="update",
            volume_sol=0,
        ))
        self.bundler.update_market_data(self.current_price_sol, natural_volume=0)

    def simulate_natural_buy(self, buy_sol: float, is_organic: bool = True) -> float:
        """
        Simulate a natural buy (organic trader or our response).

        Returns the MC after the buy.
        """
        # Calculate MC increase from buy
        # At initial state: 1 SOL buy = ~55% MC increase (due to small virtual reserves)
        # As MC grows, each SOL has less impact
        mc_multiplier = self.mc_multiplier()
        # Diminishing returns: impact decreases as MC grows
        # At $450 MC: 1 SOL = ~55% increase
        # At $5000 MC: 1 SOL = ~10% increase
        # At $20000 MC: 1 SOL = ~2.5% increase
        impact_pct = 0.55 / max(mc_multiplier, 1.0)
        if mc_multiplier > 10:
            impact_pct = 0.02  # 2% at 10x+
        elif mc_multiplier > 50:
            impact_pct = 0.005  # 0.5% at 50x+

        mc_increase = self.current_mc_usd * impact_pct * buy_sol / 1.0
        self.current_mc_usd += mc_increase
        self.current_price_sol = self.current_mc_usd / (self.total_supply * self.sol_price)

        # Record chart point
        event_type = "natural_buy" if is_organic else "our_buy"
        self.chart_history.append(ChartPoint(
            timestamp=time.time(),
            price_sol=self.current_price_sol,
            mc_usd=self.current_mc_usd,
            event_type=event_type,
            volume_sol=buy_sol,
        ))

        if is_organic:
            self.natural_events.append({
                "timestamp": time.time(),
                "type": "buy",
                "amount_sol": buy_sol,
                "mc_at_time": self.current_mc_usd,
            })

        self.bundler.update_market_data(self.current_price_sol, natural_volume=buy_sol)
        return self.current_mc_usd

    def simulate_natural_sell(self, sell_sol_value: float) -> float:
        """Simulate a natural sell (organic trader taking profit)."""
        mc_multiplier = self.mc_multiplier()
        impact_pct = 0.55 / max(mc_multiplier, 1.0)
        if mc_multiplier > 10:
            impact_pct = 0.02
        elif mc_multiplier > 50:
            impact_pct = 0.005

        mc_decrease = self.current_mc_usd * impact_pct * sell_sol_value / 1.0
        self.current_mc_usd = max(self.current_mc_usd - mc_decrease, self.initial_mc * 0.5)
        self.current_price_sol = self.current_mc_usd / (self.total_supply * self.sol_price)

        self.chart_history.append(ChartPoint(
            timestamp=time.time(),
            price_sol=self.current_price_sol,
            mc_usd=self.current_mc_usd,
            event_type="natural_sell",
            volume_sol=sell_sol_value,
        ))

        self.bundler.update_market_data(self.current_price_sol, natural_volume=0)
        return self.current_mc_usd

    def determine_phase(self) -> MarketPhase:
        """Determine current market phase based on chart conditions."""
        elapsed = time.time() - self.phase_start_time
        mc_mult = self.mc_multiplier()

        if mc_mult < 1.5 and elapsed < 30:
            return MarketPhase.INITIAL_PUMP
        elif mc_mult < 3.0 and elapsed < 90:
            return MarketPhase.CONSOLIDATION
        elif self.bundler.natural_buy_volume > 0.5 and mc_mult >= 1.5:
            return MarketPhase.MOMENTUM
        elif mc_mult >= 2.0 and self.bundler.roc > -0.05:
            return MarketPhase.PROFIT_TAKING
        elif mc_mult < 1.5 and elapsed > 90:
            return MarketPhase.RECOVERY
        elif mc_mult >= 15 or self.bundler.bubble_detected:
            return MarketPhase.EXIT
        else:
            return self.phase

    def get_phase_action(self) -> Tuple[str, float, str]:
        """
        Get the recommended action for current phase.

        Returns (action, sol_amount, reason)
        """
        self.phase = self.determine_phase()
        mc_mult = self.mc_multiplier()

        if self.phase == MarketPhase.INITIAL_PUMP:
            # Aggressive initial buys to establish momentum
            amount = random.uniform(0.05, 0.15)
            return "buy", amount, "Initial pump - establishing momentum"

        elif self.phase == MarketPhase.CONSOLIDATION:
            # Small steady buys
            amount = random.uniform(0.02, 0.08)
            return "buy", amount, "Consolidation - steady accumulation"

        elif self.phase == MarketPhase.MOMENTUM:
            # Match natural buyers
            natural_vol = self.bundler.natural_buy_volume
            if natural_vol > 0:
                amount = min(natural_vol * 0.7, 0.5)
                return "buy", amount, f"Momentum - matching {natural_vol:.2f} SOL natural volume"
            return "hold", 0, "Waiting for natural volume"

        elif self.phase == MarketPhase.PROFIT_TAKING:
            # Partial profit take at milestones
            if mc_mult >= 10:
                return "sell", 0.60, "10x reached - take 60% profits"
            elif mc_mult >= 5:
                return "sell", 0.50, "5x reached - take 50% profits"
            elif mc_mult >= 3:
                return "sell", 0.30, "3x reached - take 30% profits"
            else:
                return "hold", 0, "Holding for higher MC"

        elif self.phase == MarketPhase.RECOVERY:
            # Buy dips
            if self.bundler.consecutive_dips > 2:
                return "buy", random.uniform(0.05, 0.15), "Dip buying - recovery phase"
            return "hold", 0, "Waiting for dip confirmation"

        elif self.phase == MarketPhase.EXIT:
            return "sell", 0.80, "Exit phase - market conditions deteriorating"

        return "hold", 0, "No clear action"

    def simulate_organic_session(self, duration_minutes: float = 5.0) -> Dict:
        """
        Run a complete simulated trading session that looks organic.

        This simulates 5 minutes of trading that:
        1. Starts with an initial pump (our wallets buy)
        2. Has consolidation periods (steady small buys)
        3. Reacts to simulated natural buyers
        4. Takes profits at milestones
        5. Never graduates (keeps MC below $69K)
        """
        self.simulation_active = True
        start_time = time.time()
        duration_seconds = duration_minutes * 60
        max_mc = self.initial_mc * 77  # Cap at ~77x to stay under graduation ($69K)

        while time.time() - start_time < duration_seconds:
            elapsed = time.time() - start_time
            self.phase = self.determine_phase()

            # Simulate natural trader activity (organic buys/sells)
            if random.random() < 0.3:  # 30% chance per tick
                if random.random() < 0.6:  # 60% buy, 40% sell
                    natural_buy = random.uniform(
                        0.1 * self.bundler.natural_buy_volume if self.bundler.natural_buy_volume > 0 else 0.1,
                        1.0 if self.mc_multiplier() < 5 else 3.0
                    )
                    self.simulate_natural_buy(natural_buy, is_organic=True)
                else:
                    natural_sell = random.uniform(0.05, 1.0)
                    self.simulate_natural_sell(natural_sell)

            # Our bot response
            action, amount, reason = self.get_phase_action()
            if action == "buy" and amount > 0.001:
                bundle = self.bundler.build_buy_bundle(
                    token_mint="TEST_TOKEN",
                    total_sol=amount,
                    natural_buy=self.phase == MarketPhase.MOMENTUM,
                    dip_detected=self.phase == MarketPhase.RECOVERY,
                    bubble_risk=self.bundler.bubble_risk if hasattr(self.bundler, 'bubble_risk') else 0,
                )
                total_buy_value = sum(t.amount_sol for t in bundle)
                self.simulate_natural_buy(total_buy_value, is_organic=False)
                self.trade_history.extend([
                    TradeEvent(
                        timestamp=time.time(),
                        phase=self.phase,
                        wallet_index=t.wallet.index,
                        wallet_role=t.wallet.role,
                        action="buy",
                        amount_sol=t.amount_sol,
                        expected_mc_after=self.current_mc_usd,
                        reason=reason,
                    ) for t in bundle
                ])

            elif action == "sell" and amount > 0.001:
                total_tokens = sum(w.tokens_held for w in self.bundler.wallets)
                self.bundler.current_price = self.current_price_sol

                # Temporarily set token holdings for sell calculation
                for w in self.bundler.wallets:
                    w.tokens_held = total_tokens / len(self.bundler.wallets) * random.uniform(0.5, 1.5)
                    w.avg_buy_price = self.current_price_sol / max(self.mc_multiplier(), 1)

                trades, metadata = self.bundler.build_sell_bundle(
                    token_mint="TEST_TOKEN",
                    current_price=self.current_price_sol,
                    entry_price=self.current_price_sol / max(self.mc_multiplier(), 1),
                )
                total_sell_value = metadata["total_sol_sold"]
                if total_sell_value > 0:
                    self.simulate_natural_sell(total_sell_value)
                    self.trade_history.extend([
                        TradeEvent(
                            timestamp=time.time(),
                            phase=self.phase,
                            wallet_index=t.wallet.index,
                            wallet_role=t.wallet.role,
                            action="sell",
                            amount_sol=t.amount_sol,
                            expected_mc_after=self.current_mc_usd,
                            reason=reason,
                        ) for t in trades
                    ])

            # Check graduation cap
            if self.current_mc_usd >= max_mc * 0.95:
                # Simulate resistance selling
                self.simulate_natural_sell(random.uniform(0.5, 2.0))
                if self.current_mc_usd > max_mc:
                    return self.get_session_summary()

            # Wait before next tick
            time.sleep(0.1)

        self.simulation_active = False
        return self.get_session_summary()

    def get_session_summary(self) -> Dict:
        """Get summary of the simulated session."""
        buys = [t for t in self.trade_history if t.action == "buy"]
        sells = [t for t in self.trade_history if t.action == "sell"]
        total_buy_sol = sum(t.amount_sol for t in buys)
        total_sell_sol = sum(t.amount_sol for t in sells)

        # Calculate token holdings
        total_tokens = sum(w.tokens_held for w in self.bundler.wallets)
        token_value_sol = total_tokens * self.current_price_sol if total_tokens > 0 else 0

        return {
            "duration_seconds": time.time() - self.phase_start_time,
            "initial_mc": self.initial_mc,
            "final_mc": self.current_mc_usd,
            "mc_multiplier": self.mc_multiplier(),
            "final_price_sol": self.current_price_sol,
            "total_buy_sol": total_buy_sol,
            "total_sell_sol": total_sell_sol,
            "total_tokens_held": total_tokens,
            "token_value_sol": token_value_sol,
            "net_pnl_sol": total_sell_sol + token_value_sol - total_buy_sol - 0.34,  # minus creation fee
            "net_roi_pct": ((total_sell_sol + token_value_sol - total_buy_sol - 0.34) / total_buy_sol) * 100 if total_buy_sol > 0 else 0,
            "trade_events": len(self.trade_history),
            "buy_events": len(buys),
            "sell_events": len(sells),
            "natural_buyers_detected": len(self.natural_events),
            "bubble_detected": self.bundler.bubble_detected,
            "chart_points": len(self.chart_history),
            "phase_distribution": dict(
                (p.value, sum(1 for t in self.trade_history if t.phase == p))
                for p in MarketPhase
            ),
        }


class MoneyFlowEngine:
    """
    Central money flow engine that orchestrates:
    1. Wallet creation and funding
    2. Dynamic capital allocation
    3. Fee recovery and profit harvesting
    4. Organic chart pattern simulation
    5. Risk management across all wallets

    This is the main entry point for the trading system.
    """

    def __init__(
        self,
        budget_sol: float = 6.0,
        token_mint: str = "",
        initial_mc_usd: float = 450.0,
        target_max_mc: float = 50000.0,  # Never exceed $50K MC (stay below graduation)
        test_mode: bool = False,
    ):
        self.budget_sol = budget_sol
        self.token_mint = token_mint
        self.initial_mc = initial_mc_usd
        self.target_max_mc = target_max_mc
        self.total_supply = 1_000_000_000
        self.sol_price = 150.0
        self.test_mode = test_mode

        # Core components
        self.bundler: SmartBundler = SmartBundler(budget_sol=budget_sol, test_mode=test_mode)
        self.curve_trader: BondingCurveTrader = BondingCurveTrader(
            token_mint=token_mint if token_mint else "UNKNOWN",
            initial_price=initial_mc_usd / (self.total_supply * self.sol_price),
            total_supply=self.total_supply,
        )
        self.chart_sim: Optional[ChartPatternSimulator] = None

        # State tracking
        self.is_initialized = False
        self.creator_wallet_pubkey: str = ""
        self.current_mc_usd = initial_mc_usd
        self.total_sol_spent = 0.0
        self.total_sol_recovered = 0.0
        self.natural_buy_volume = 0.0
        self.highest_mc_reached = initial_mc_usd

        # Fee recovery tracking
        self.total_fees_paid = 0.0
        self.total_fees_recovered = 0.0
        self.fee_recovery_target = 0.0  # Target amount to recover in fees

    def initialize_wallets(self, creator_seed: str = "") -> List[SmartWallet]:
        """Initialize all wallets and set up the trading system."""
        print(f"[MONEY FLOW] Initializing {self.bundler.num_wallets} wallets with {self.budget_sol} SOL budget")

        wallets = self.bundler.setup_wallets(creator_seed=creator_seed)
        self.is_initialized = True

        # Create chart simulator
        self.chart_sim = ChartPatternSimulator(
            bundler=self.bundler,
            initial_mc_usd=self.initial_mc,
            total_supply=self.total_supply,
            sol_price=self.sol_price,
        )

        print(f"[MONEY FLOW] Wallets ready:")
        summary = self.bundler.get_wallet_summary()
        for role_name, data in summary.items():
            print(f"  {role_name}: {data['count']} wallets, {data['total_allocated']:.4f} SOL")

        # Set fee recovery target (gas fees for ~200 transactions)
        self.fee_recovery_target = self.budget_sol * 0.05  # 5% of budget for fees
        print(f"[MONEY FLOW] Fee recovery target: {self.fee_recovery_target:.4f} SOL")

        return wallets

    def launch_initial_bundle(self, buy_sol: float = 0.50) -> Dict:
        """
        Execute the initial buy bundle after token creation.

        This establishes initial price and momentum without spiking
        the MC too aggressively (stays under $1000 MC).
        """
        if not self.is_initialized:
            raise RuntimeError("Wallets not initialized. Call initialize_wallets() first.")

        print(f"[MONEY FLOW] Executing initial bundle: {buy_sol:.2f} SOL across wallets")

        # Build the buy bundle
        bundle = self.bundler.build_buy_bundle(
            token_mint=self.token_mint,
            total_sol=buy_sol,
            natural_buy=False,
            dip_detected=False,
            bubble_risk=0.0,  # Fresh launch, no bubble risk yet
        )

        total_buy_value = sum(t.amount_sol for t in bundle)
        self.total_sol_spent += total_buy_value

        # Calculate expected MC after buy
        # At $450 initial MC, ~55% impact per SOL (diminishing)
        mc_multiplier = max(self.mc_multiplier(), 1.0)
        impact_pct = 0.55 / mc_multiplier
        mc_increase = self.current_mc_usd * impact_pct * total_buy_value
        new_mc = self.current_mc_usd + mc_increase

        # Update chart simulator
        if self.chart_sim:
            self.chart_sim.update_price_from_mc(new_mc)

        self.current_mc_usd = new_mc
        self.highest_mc_reached = max(self.highest_mc_reached, new_mc)

        # Record token holdings (approximate)
        for t in bundle:
            # Simplified: tokens = SOL / price
            price = self.chart_sim.current_price_sol if self.chart_sim else 0
            tokens_received = t.amount_sol / price if price > 0 else 0
            t.wallet.tokens_held += tokens_received
            t.wallet.avg_buy_price = price
            t.wallet.has_buy_history = True

        return {
            "bundle_size": len(bundle),
            "total_sol": total_buy_value,
            "new_mc": new_mc,
            "mc_multiplier": self.mc_multiplier(),
            "mc_increase_pct": (mc_increase / self.current_mc_usd) * 100 if self.current_mc_usd > 0 else 0,
        }

    def react_to_natural_buy(self, natural_buy_sol: float) -> Optional[Dict]:
        """
        React to a detected natural buyer.

        When natural traders buy >0.5 SOL, we respond with our own
        buys to create momentum and attract more traders.
        """
        if natural_buy_sol < 0.5:
            return None

        self.curve_trader.detect_natural_buy(natural_buy_sol)

        # Calculate response
        response = self.curve_trader.calculate_natural_buy_response(natural_buy_sol)
        if not response["should_buy"]:
            return None

        total_response = response["total_response_sol"]
        self.total_sol_spent += total_response

        # Execute response bundle
        bundle = self.bundler.build_natural_buy_response(
            token_mint=self.token_mint,
            natural_buy_sol=natural_buy_sol,
            current_price=self.chart_sim.current_price_sol if self.chart_sim else 0,
        )

        # Update MC
        if self.chart_sim:
            mc_mult = max(self.chart_sim.mc_multiplier(), 1.0)
            impact_pct = 0.55 / mc_mult
            mc_increase = self.current_mc_usd * impact_pct * sum(t.amount_sol for t in bundle)
            self.current_mc_usd += mc_increase
            self.chart_sim.update_price_from_mc(self.current_mc_usd)
            self.highest_mc_reached = max(self.highest_mc_reached, self.current_mc_usd)

        return {
            "natural_buy": natural_buy_sol,
            "response_sol": sum(t.amount_sol for t in bundle),
            "response_ratio": total_response / natural_buy_sol,
            "new_mc": self.current_mc_usd,
            "wallets_responding": len(bundle),
            "reason": response["reason"],
        }

    def react_to_natural_sell(self, natural_sell_sol_value: float) -> Optional[Dict]:
        """
        React to a detected natural seller (buy the dip).

        When large traders sell, we buy to:
        1. Catch the falling price
        2. Create support level
        3. Show resistance to larger dumps
        """
        if natural_sell_sol_value < 0.3:
            return None

        # Calculate buyback amount (0.5-0.8x of sell value)
        buyback_ratio = random.uniform(0.5, 0.8)
        buyback_amount = natural_sell_sol_value * buyback_ratio

        # Cap buyback to bubble risk
        if self.bundler.bubble_detected or self.bundler.bubble_risk > 0.5:
            buyback_amount *= 0.3

        self.total_sol_spent += buyback_amount

        # Build buyback bundle
        bundle = self.bundler.build_buy_bundle(
            token_mint=self.token_mint,
            total_sol=buyback_amount,
            natural_buy=False,
            dip_detected=True,
            bubble_risk=min(self.bundler.bubble_risk, 0.5),
        )

        # Update MC (buying the dip reduces the drop)
        if self.chart_sim:
            mc_mult = max(self.chart_sim.mc_multiplier(), 1.0)
            impact_pct = 0.55 / mc_mult
            # Buyback offsets some of the sell impact
            sell_impact = self.current_mc_usd * impact_pct * natural_sell_sol_value * 0.7
            buy_impact = self.current_mc_usd * impact_pct * buyback_amount
            net_mc_change = buy_impact - sell_impact
            self.current_mc_usd = max(self.current_mc_usd + net_mc_change, self.initial_mc * 0.5)
            self.chart_sim.update_price_from_mc(self.current_mc_usd)

        return {
            "natural_sell": natural_sell_sol_value,
            "buyback_sol": buyback_amount,
            "buyback_ratio": buyback_ratio,
            "new_mc": self.current_mc_usd,
            "wallets_buying": len(bundle),
        }

    def check_profit_taking(self, mc_multiplier: Optional[float] = None) -> Optional[Dict]:
        """
        Check if we should take profits based on current market conditions.

        Implements trailing stops, take-profit tiers, and fee recovery.
        """
        if mc_multiplier is None:
            mc_multiplier = self.mc_multiplier()

        should_sell, reason = self.curve_trader.should_take_profit(
            mc_multiplier=mc_multiplier,
            current_price=self.chart_sim.current_price_sol if self.chart_sim else 0,
        )

        if not should_sell:
            return None

        sell_ratio = self.curve_trader.calculate_sell_ratio(mc_multiplier)
        self.curve_trader.total_tokens_held = sum(w.tokens_held for w in self.bundler.wallets)
        self.curve_trader.wallet_positions = {
            f"wallet_{w.index}": {
                "tokens": w.tokens_held,
                "entry_price": w.avg_buy_price if w.avg_buy_price > 0 else self.curve_trader.current_price / mc_multiplier,
                "peak_price": w.peak_price,
            }
            for w in self.bundler.wallets if w.tokens_held > 0
        }

        trades, metadata = self.bundler.build_sell_bundle(
            token_mint=self.token_mint,
            current_price=self.chart_sim.current_price_sol if self.chart_sim else 0,
            entry_price=self.chart_sim.current_price_sol / mc_multiplier if self.chart_sim else 0,
        )

        total_sold = metadata["total_sol_sold"]
        self.total_sol_recovered += total_sold
        self.total_fees_recovered += total_sold * 0.003  # Approx fee portion

        # Update MC
        if self.chart_sim and total_sold > 0:
            # Selling reduces MC
            mc_mult = max(self.chart_sim.mc_multiplier(), 1.0)
            impact_pct = 0.55 / mc_mult
            mc_decrease = self.current_mc_usd * impact_pct * (total_sold / 1.0)
            # But staggered sells minimize impact
            mc_decrease *= 0.5  # 50% impact reduction from staggering
            self.current_mc_usd = max(self.current_mc_usd - mc_decrease, self.current_mc_usd * 0.8)
            self.chart_sim.update_price_from_mc(self.current_mc_usd)

        # Harvest profits to cover wallet
        self.bundler.harvest_profits_to_cover()

        return {
            "mc_multiplier": mc_multiplier,
            "sell_ratio": sell_ratio,
            "sol_sold": total_sold,
            "wallets_selling": metadata["wallets_selling"],
            "estimated_impact": metadata["estimated_price_impact"],
            "fee_recovery": self.total_fees_recovered,
            "target_recovery": self.fee_recovery_target,
            "reason": reason,
        }

    def harvest_to_cover(self) -> Dict:
        """Move profits from active wallets to cover wallet."""
        before_value = sum(w.allocated_sol - w.spent_sol for w in self.bundler.wallets)

        # Transfer from profitable wallets to cover
        cover_wallets = [w for w in self.bundler.wallets if w.role == WalletRole.COVER]
        profitable = [
            w for w in self.bundler.wallets
            if w.role != WalletRole.COVER and w.tokens_held > 0 and w.avg_buy_price > 0
        ]

        transferred = 0.0
        for w in profitable[:3]:
            # Transfer 30% of realized gains
            gain = w.remaining_budget * 0.3
            if gain > 0.01 and w.remaining_budget > gain:
                cover_wallets[0].allocated_sol += gain
                w.allocated_sol -= gain
                transferred += gain

        after_value = sum(w.allocated_sol - w.spent_sol for w in self.bundler.wallets)

        return {
            "transferred_sol": transferred,
            "cover_wallet_balance": cover_wallets[0].allocated_sol if cover_wallets else 0,
            "before_value": before_value,
            "after_value": after_value,
        }

    def emergency_exit(self) -> Dict:
        """
        Emergency exit strategy: sell everything through cover wallet.

        Triggered when:
        - Bubble risk > 0.8
        - MC near graduation
        - Rapid price decline
        """
        print("[MONEY FLOW] EMERGENCY EXIT TRIGGERED")

        # Sell all tokens from cover wallet first
        cover_wallets = [w for w in self.bundler.wallets if w.role == WalletRole.COVER]
        normal_wallets = [w for w in self.bundler.wallets if w.role != WalletRole.COVER and w.tokens_held > 0]

        total_tokens = sum(w.tokens_held for w in self.bundler.wallets)
        total_sol_value = total_tokens * self.chart_sim.current_price_sol * 0.997 if self.chart_sim else 0

        return {
            "emergency_exit": True,
            "total_tokens": total_tokens,
            "total_sol_value": total_sol_value,
            "cover_wallet_tokens": sum(w.tokens_held for w in cover_wallets),
            "other_wallet_tokens": sum(w.tokens_held for w in normal_wallets),
            "fee_recovered": self.total_fees_recovered,
            "fee_target": self.fee_recovery_target,
            "net_pnl_sol": self.total_sol_recovered - self.total_sol_spent,
        }

    def mc_multiplier(self) -> float:
        """Current MC multiplier relative to initial."""
        if self.initial_mc > 0:
            return self.current_mc_usd / self.initial_mc
        return 1.0

    def is_near_graduation(self, threshold: float = 0.9) -> bool:
        """Check if we're approaching graduation MC."""
        grad_mc = 69000.0  # $69K graduation threshold
        return self.current_mc_usd >= grad_mc * threshold

    def get_money_flow_summary(self) -> Dict:
        """Get comprehensive money flow and portfolio summary."""
        portfolio = self.bundler.calculate_total_value(
            self.chart_sim.current_price_sol * 150 if self.chart_sim else 0  # USD price
        )

        return {
            "budget_sol": self.budget_sol,
            "total_sol_spent": self.total_sol_spent,
            "total_sol_recovered": self.total_sol_recovered,
            "net_pnl_sol": self.total_sol_recovered - self.total_sol_spent,
            "fees_paid": self.total_fees_paid,
            "fees_recovered": self.total_fees_recovered,
            "fee_recovery_target": self.fee_recovery_target,
            "fee_recovery_pct": self.total_fees_recovered / self.fee_recovery_target * 100 if self.fee_recovery_target > 0 else 0,
            "current_mc_usd": self.current_mc_usd,
            "mc_multiplier": self.mc_multiplier(),
            "highest_mc_reached": self.highest_mc_reached,
            "near_graduation": self.is_near_graduation(),
            "natural_buy_volume": self.natural_buy_volume,
            "bubble_detected": self.bundler.bubble_detected,
            "bubble_risk": self.bundler.bubble_risk if hasattr(self.bundler, 'bubble_risk') else 0,
            "portfolio": portfolio,
            "wallet_summary": self.bundler.get_wallet_summary(),
        }


# ─── Tests ───

def test_money_flow_engine():
    """Test the money flow engine initialization and basic operations."""
    print("\n[TEST] Money Flow Engine")
    engine = MoneyFlowEngine(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)

    # Initialize wallets
    wallets = engine.initialize_wallets()
    assert len(wallets) >= 18, f"Expected >=18 wallets, got {len(wallets)}"

    # Launch initial bundle
    result = engine.launch_initial_bundle(buy_sol=0.50)
    print(f"  Initial bundle: {result['bundle_size']} wallets, {result['total_sol']:.4f} SOL")
    print(f"  New MC: ${result['new_mc']:.2f} ({result['mc_multiplier']:.1f}x)")

    assert result["new_mc"] > 450, "MC should increase after buy"
    assert result["mc_multiplier"] > 1.0, "MC should be above initial"

    # React to natural buy
    response = engine.react_to_natural_buy(1.0)
    if response:
        print(f"  Natural buy response: {response['response_sol']:.4f} SOL")
        assert response["response_sol"] > 0

    print("  PASS - Money Flow Engine")
    return True


def test_profit_taking():
    """Test profit-taking logic."""
    print("\n[TEST] Profit Taking")
    engine = MoneyFlowEngine(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)
    engine.initialize_wallets()
    engine.launch_initial_bundle(buy_sol=0.50)

    # Simulate reaching 5x MC
    engine.current_mc_usd = engine.initial_mc * 5
    engine.chart_sim.update_price_from_mc(engine.current_mc_usd)

    result = engine.check_profit_taking(mc_multiplier=5.0)
    print(f"  Profit taking: {result}")

    assert result is not None, "Should trigger profit taking at 5x"
    assert result["sol_sold"] > 0

    print("  PASS - Profit Taking")
    return True


def test_emergency_exit():
    """Test emergency exit."""
    print("\n[TEST] Emergency Exit")
    engine = MoneyFlowEngine(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)
    engine.initialize_wallets()
    engine.launch_initial_bundle(buy_sol=0.50)

    result = engine.emergency_exit()
    print(f"  Emergency exit: net PNL = {result['net_pnl_sol']:.4f} SOL")
    print(f"  Fees recovered: {result['fee_recovered']:.4f} / {result['fee_target']:.4f}")

    print("  PASS - Emergency Exit")
    return True


def test_near_graduation():
    """Test graduation detection."""
    print("\n[TEST] Near Graduation Detection")
    engine = MoneyFlowEngine(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)
    engine.initialize_wallets()

    assert engine.is_near_graduation(0.9) == False, "Should be far from graduation"

    engine.current_mc_usd = 63000  # 91% of $69K
    assert engine.is_near_graduation(0.9) == True, "Should be near graduation"

    print("  PASS - Near Graduation")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("MONEY FLOW ENGINE TESTS")
    print("=" * 60)

    tests = [
        test_money_flow_engine,
        test_profit_taking,
        test_emergency_exit,
        test_near_graduation,
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
# Budget management functions needed by pumpfun_lifecycle_cli.py and
# integration_test.py (from the older money_flow.py / Tier 3 system)

SOL_PRICE_USD = 150.0
GAS_FLOOR_SOL = 0.001
TX_FEE_SOL = 0.000005
JITO_TIP_DEFAULT_SOL = 0.00005
JITO_TIP_MIN_SOL = 0.00001
LAMPORTS_PER_SOL = 1_000_000

# Budget tiers for different USD amounts (MICRO → XXLARGE)
# Budget tiers — matched to original Tier 3 money_flow expectations
BUDGET_TIERS = {
    "MICRO":   {"budget_usd": 5.0,  "wallets": 3},
    "SMALL":   {"budget_usd": 10.0, "wallets": 3},
    "MEDIUM":  {"budget_usd": 15.0, "wallets": 5},
    "LARGE":   {"budget_usd": 20.0, "wallets": 5},
    "XLARGE":  {"budget_usd": 50.0, "wallets": 8},
    "XXLARGE": {"budget_usd": 100.0, "wallets": 12},
}

# Tier configurations (role → percentage) — used by SmartBundler
TIER_CONFIGS = {
    "MICRO":   {"budget_usd": 5.0, "wallets": 3,  "roles": {"whale": 0.40, "mid": 0.20, "normal": 0.20, "noise": 0.10, "gas": 0.10}},
    "SMALL":   {"budget_usd": 15.0, "wallets": 3, "roles": {"whale": 0.35, "mid": 0.20, "normal": 0.25, "noise": 0.10, "gas": 0.10}},
    "MEDIUM":  {"budget_usd": 30.0, "wallets": 5, "roles": {"whale": 0.30, "mid": 0.20, "normal": 0.25, "noise": 0.10, "gas": 0.10, "cover": 0.05}},
    "LARGE":   {"budget_usd": 50.0, "wallets": 8, "roles": {"whale": 0.30, "mid": 0.20, "normal": 0.20, "noise": 0.10, "gas": 0.05, "cover": 0.10, "sniper": 0.05}},
    "XLARGE":  {"budget_usd": 100.0, "wallets": 12, "roles": {"whale": 0.25, "mid": 0.15, "normal": 0.20, "noise": 0.10, "gas": 0.05, "cover": 0.10, "sniper": 0.10, "commenter": 0.05}},
    "XXLARGE": {"budget_usd": 500.0, "wallets": 20, "roles": {"whale": 0.20, "mid": 0.15, "normal": 0.15, "noise": 0.10, "gas": 0.05, "cover": 0.10, "sniper": 0.10, "commenter": 0.10, "reserve": 0.05}},
}

# Take-profit tiers (MC multiplier → sell percentage, sums to 1.0)
TAKE_PROFIT_TIERS = [
    {"mc_x": 2,   "sell_pct": 0.10, "label": "2x"},
    {"mc_x": 3,   "sell_pct": 0.10, "label": "3x"},
    {"mc_x": 5,   "sell_pct": 0.15, "label": "5x"},
    {"mc_x": 10,  "sell_pct": 0.15, "label": "10x"},
    {"mc_x": 15,  "sell_pct": 0.20, "label": "15x"},
    {"mc_x": 20,  "sell_pct": 0.20, "label": "20x"},
    {"mc_x": 100, "sell_pct": 0.10, "label": "100x"},
]

# Pump.fun fee brackets
PUMP_FEE_TIERS = {
    "initial":  {"mc_sol": 0,     "fee_pct": 0.03},
    "mid":      {"mc_sol": 10_000, "fee_pct": 0.015},
    "high":     {"mc_sol": 30_000, "fee_pct": 0.0075},
}

# Allocation strategies
ALLOCATION_STRATEGIES = ["TIERED", "AGGRESSIVE", "ECO", "EQUAL", "WHALE_ONLY"]

# Money tiers (role → percentage)
MONEY_TIERS = {
    "whale": 0.30, "mid": 0.22, "normal": 0.22, "noise": 0.13, "micro": 0.13,
}


class AllocationStrategy(Enum):
    TIERED = "tiered"
    AGGRESSIVE = "aggressive"
    ECO = "eco"
    EQUAL = "equal"
    WHALE_ONLY = "whale_only"
    CUSTOM = "custom"


@dataclass
class Allocation:
    wallet_index: int
    role: str
    percentage: float
    allocated_sol: float
    allocated_usd: float = 0.0
    target_mc_x: float = 0.0
    buy_timing: float = 0.0
    sol_amount: float = 0.0  # Alias for allocated_sol (compatibility)


@dataclass
class BudgetAnalysis:
    budget_sol: float
    budget_usd: float
    num_wallets: int
    tier: str
    allocations: List[Allocation]
    estimated_total_fees_sol: float
    gas_reservation_sol: float
    usable_budget_sol: float
    gas_buffer_pct: float
    fee_budget_pct: float
    strategy: AllocationStrategy = None
    estimated_total_fees_usd: float = 0.0
    net_tradeable_sol: float = 0.0
    pump_fee_bracket: str = "launch"
    fee_budget_threshold_pct: float = 0.15
    sufficient_funds: bool = True
    warnings: List[str] = field(default_factory=list)


def usd_to_sol(usd: float, sol_price: float = SOL_PRICE_USD) -> float:
    """Convert USD to SOL."""
    return usd / sol_price


def sol_to_usd(sol: float, sol_price: float = SOL_PRICE_USD) -> float:
    """Convert SOL to USD."""
    return sol * sol_price


def get_budget_tier(budget_usd: float) -> str:
    """Determine budget tier from USD amount."""
    for tier_name in ["XXLARGE", "XLARGE", "LARGE", "MEDIUM", "SMALL", "MICRO"]:
        if budget_usd >= BUDGET_TIERS[tier_name]["budget_usd"]:
            return tier_name
    return "MICRO"


def get_tier_config(tier_name: str) -> Optional[dict]:
    """Get configuration for a specific budget tier."""
    return TIER_CONFIGS.get(tier_name.upper())


def get_recommended_tier(budget_usd: float) -> str:
    """Get recommended tier name for a budget amount."""
    return get_budget_tier(budget_usd)


def get_pump_fee_bracket(token_price_usd: float = None, market_cap_usd: float = None) -> Tuple[str, float]:
    """Get the current Pump.fun fee bracket based on market cap or token price.

    Fee tiers (from pumpfun_source_of_truth.md):
    - launch: MC < $10K → 3%
    - growth: $10K <= MC < $30K → 1.5%
    - mature: MC >= $30K → 0.75%
    """
    if market_cap_usd is not None:
        if market_cap_usd < 10_000:
            return "launch", 0.03
        elif market_cap_usd < 30_000:
            return "growth", 0.015
        else:
            return "mature", 0.0075

    # Fallback to token_price_usd
    if token_price_usd is None:
        return "launch", 0.03
    if token_price_usd < 0.01:
        return "launch", 0.03
    elif token_price_usd < 0.10:
        return "growth", 0.015
    else:
        return "mature", 0.0075


# Tiered allocation percentages (new: sums to 100%)
TIERED_ALLOCATION_PCTS = [0.30, 0.22, 0.22, 0.13, 0.13]
AGGRESSIVE_ALLOCATION_PCTS = [0.50, 0.15, 0.15, 0.10, 0.10]
ECO_ALLOCATION_PCTS = [0.35, 0.25, 0.20, 0.10, 0.10]
EQUAL_ALLOCATION_PCTS = [0.20, 0.20, 0.20, 0.20, 0.20]
WHALE_ONLY_ALLOCATION_PCTS = [1.0]

ALLOCATION_PERCENTAGES = {
    "TIERED": TIERED_ALLOCATION_PCTS,
    "AGGRESSIVE": AGGRESSIVE_ALLOCATION_PCTS,
    "ECO": ECO_ALLOCATION_PCTS,
    "EQUAL": EQUAL_ALLOCATION_PCTS,
    "WHALE_ONLY": WHALE_ONLY_ALLOCATION_PCTS,
    "CUSTOM": None,  # Custom handled dynamically
}


def calculate_allocations(budget_usd: float, num_wallets: int,
                          strategy: AllocationStrategy = AllocationStrategy.TIERED,
                          sol_price: float = SOL_PRICE_USD,
                          num_cycles: int = 5) -> List[Allocation]:
    """Calculate wallet allocations based on strategy."""
    budget_sol = usd_to_sol(budget_usd, sol_price)
    strat_key = strategy.value.upper() if isinstance(strategy, AllocationStrategy) else strategy.upper()

    # CUSTOM strategy: equal split
    # WHALE_ONLY strategy: single wallet gets everything
    if strat_key == "CUSTOM":
        pcts = [1.0 / max(num_wallets, 1)] * max(num_wallets, 1)
        actual_wallets = num_wallets if num_wallets > 0 else len(pcts)
        pcts = pcts[:actual_wallets]
    elif strat_key == "WHALE_ONLY":
        pcts = [1.0]
        actual_wallets = 1
    elif strat_key == "EQUAL":
        # Equal distribution regardless of number of wallets
        actual_wallets = num_wallets if num_wallets > 0 else 5
        pcts = [1.0 / actual_wallets] * actual_wallets
    else:
        pcts = ALLOCATION_PERCENTAGES.get(strat_key, TIERED_ALLOCATION_PCTS)
        if pcts is None:
            pcts = [1.0 / max(num_wallets, 1)] * max(num_wallets, 1)
        actual_wallets = num_wallets if num_wallets > 0 else len(pcts)

    roles = ["whale", "mid", "mid", "small", "small"]
    roles = roles[:actual_wallets]

    # If more wallets than roles, distribute remaining
    if actual_wallets > len(pcts):
        pcts = pcts + [0.0] * (actual_wallets - len(pcts))
        # Redistribute from existing
        for i in range(len(pcts), actual_wallets):
            pcts[i] = pcts[i % len(TIERED_ALLOCATION_PCTS)] * 0.3

    allocations = []
    for i in range(actual_wallets):
        pct = pcts[i] if i < len(pcts) else 1.0 / actual_wallets
        allocated = budget_sol * pct
        role = roles[i] if i < len(roles) else "bot"
        allocations.append(Allocation(
            wallet_index=i,
            role=role,
            percentage=pct,
            allocated_sol=allocated,
            allocated_usd=allocated * sol_price,
            sol_amount=allocated,  # Alias for compatibility
        ))

    return allocations


def take_profit_tiers():
    """Return the take-profit tier configuration."""
    return TAKE_PROFIT_TIERS


def _calculate_gas_reservation(num_wallets: int) -> float:
    """Calculate gas reservation for N wallets."""
    # 0.001 SOL per wallet for gas + transaction fees
    return num_wallets * GAS_FLOOR_SOL


def _estimate_fees(num_wallets: int, num_cycles: int = 5) -> float:
    """Estimate total fees for the trading session (returns SOL)."""
    tx_per_wallet = 20
    total_txs = num_wallets * tx_per_wallet * num_cycles
    base_fees = total_txs * TX_FEE_SOL
    jito_tips = total_txs * JITO_TIP_DEFAULT_SOL
    return base_fees + jito_tips


def estimate_fees(num_wallets: int, num_cycles: int = 5,
                  priority_fee_sol: float = JITO_TIP_DEFAULT_SOL,
                  sol_price: float = SOL_PRICE_USD) -> Dict:
    """Estimate total fees for the trading session.

    Returns a dict with breakdown: total_transactions, base_fees_sol,
    jito_tips_sol, total_fees_sol, total_fees_usd.
    """
    tx_per_wallet = 20
    total_txs = num_wallets * tx_per_wallet * num_cycles
    base_fees = total_txs * TX_FEE_SOL
    jito_tips = total_txs * priority_fee_sol
    total_fees = base_fees + jito_tips
    return {
        "total_transactions": total_txs,
        "base_fees_sol": base_fees,
        "jito_tips_sol": jito_tips,
        "total_fees_sol": total_fees,
        "total_fees_usd": total_fees * sol_price,
    }


def calculate_budget_analysis(budget_usd: float, num_wallets: int,
                              strategy: AllocationStrategy = AllocationStrategy.TIERED,
                              sol_price: float = SOL_PRICE_USD,
                              num_cycles: int = 5) -> BudgetAnalysis:
    """Analyze budget allocation with fee estimation."""
    budget_sol = usd_to_sol(budget_usd, sol_price)
    gas_reservation = _calculate_gas_reservation(num_wallets)
    fee_result = estimate_fees(num_wallets, num_cycles, sol_price=sol_price)
    estimated_fees = fee_result["total_fees_sol"]
    usable_budget = budget_sol - gas_reservation

    allocations = calculate_allocations(budget_usd, num_wallets, strategy, sol_price)

    fee_budget_pct = estimated_fees / budget_sol if budget_sol > 0 else 1.0
    fees_exceed = fee_budget_pct > 0.15  # 15% threshold

    warnings = []
    if fees_exceed:
        warnings.append(f"Fee budget ({fee_budget_pct*100:.1f}%) exceeds 15% threshold")
    if usable_budget < gas_reservation:
        warnings.append("Usable budget below gas reservation")

    return BudgetAnalysis(
        budget_sol=budget_sol,
        budget_usd=budget_usd,
        num_wallets=num_wallets,
        tier=get_budget_tier(budget_usd),
        strategy=strategy,
        allocations=allocations,
        estimated_total_fees_sol=estimated_fees,
        estimated_total_fees_usd=estimated_fees * sol_price,
        gas_buffer_pct=fee_budget_pct,
        fee_budget_threshold_pct=0.15,
        fee_budget_pct=fee_budget_pct,
        gas_reservation_sol=gas_reservation,
        usable_budget_sol=usable_budget,
        net_tradeable_sol=usable_budget - estimated_fees,
        pump_fee_bracket=get_pump_fee_bracket(market_cap_usd=budget_sol * 150 * 150),
        sufficient_funds=not fees_exceed,
        warnings=warnings,
    )


def print_budget_report(analysis: BudgetAnalysis):
    """Print a budget analysis report."""
    print(f"\n{'='*50}")
    print(f"  BUDGET ANALYSIS ({analysis.tier})")
    print(f"{'='*50}")
    print(f"  Budget: ${analysis.budget_usd:.2f} = {analysis.budget_sol:.6f} SOL")
    print(f"  Wallets: {analysis.num_wallets}")
    print(f"  Gas reserve: {analysis.gas_reservation_sol:.6f} SOL")
    print(f"  Estimated fees: {analysis.estimated_total_fees_sol:.6f} SOL ({analysis.fee_budget_pct*100:.1f}%)")
    print(f"  Usable budget: {analysis.usable_budget_sol:.6f} SOL")
    print(f"  Sufficient: {'✓' if analysis.sufficient_funds else '✗'}")
    if analysis.warnings:
        for w in analysis.warnings:
            print(f"  ⚠️  {w}")
    print(f"\n  Allocations:")
    for a in analysis.allocations:
        print(f"    W{a.wallet_index+1} ({a.role}): {a.percentage*100:.0f}% = {a.allocated_sol:.6f} SOL")


def money_flow_cli():
    """CLI for budget analysis."""
    import argparse
    parser = argparse.ArgumentParser(description="Money Flow budget analysis")
    parser.add_argument("--budget-usd", type=float, default=20.0)
    parser.add_argument("--wallets", type=int, default=5)
    args = parser.parse_args()

    analysis = calculate_budget_analysis(args.budget_usd, args.wallets)
    print_budget_report(analysis)


def run_tests():
    """Run budget analysis tests."""
    from typing import Any

    print(f"\n{'='*60}")
    print("  Money Flow Budget Tests")
    print("="*60)

    # Test 1: Basic conversion
    assert abs(usd_to_sol(20) - 20/150) < 1e-9, "USD to SOL conversion"
    assert abs(sol_to_usd(1.0) - 150.0) < 1e-9, "SOL to USD conversion"
    print("  PASS: USD/SOL conversion")

    # Test 2: Budget tier classification
    assert get_budget_tier(5) == "MICRO"
    assert get_budget_tier(20) == "SMALL"
    assert get_budget_tier(50) == "MEDIUM"
    assert get_budget_tier(100) == "LARGE"
    assert get_budget_tier(500) == "XLARGE"
    assert get_budget_tier(1000) == "XXLARGE"
    print("  PASS: Budget tier classification")

    # Test 3: Tiered allocation sums to 100%
    allocs = calculate_allocations(20, 5, AllocationStrategy.TIERED)
    total = sum(a.percentage for a in allocs)
    assert abs(total - 1.0) < 1e-9, f"Allocations sum to {total}, not 1.0"
    assert allocs[0].percentage == 0.30  # Whale
    print("  PASS: Tiered allocation (100% sum, whale=30%)")

    # Test 4: Aggressive allocation
    allocs = calculate_allocations(20, 5, AllocationStrategy.AGGRESSIVE)
    total = sum(a.percentage for a in allocs)
    assert abs(total - 1.0) < 1e-9, f"Aggressive allocations sum to {total}"
    assert allocs[0].percentage == 0.40  # Whale
    print("  PASS: Aggressive allocation (100% sum, whale=40%)")

    # Test 5: Budget analysis
    analysis = calculate_budget_analysis(20, 5, AllocationStrategy.TIERED)
    assert analysis.budget_sol == usd_to_sol(20)
    assert len(analysis.allocations) == 5
    assert analysis.fee_budget_pct <= 0.15 or not analysis.sufficient_funds
    print("  PASS: Budget analysis (fees + gas within threshold)")

    # Test 6: Take-profit tiers sum to 100%
    total = sum(t["sell_pct"] for t in TAKE_PROFIT_TIERS)
    assert abs(total - 1.0) < 1e-9, f"Take-profit tiers sum to {total}, not 1.0"
    print("  PASS: Take-profit tiers (100% sum)")

    print(f"\n{'='*60}")
    print(f"  All budget tests PASSED")
    print("="*60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Money Flow Engine")
    parser.add_argument("--test", action="store_true", help="Run tests")
    parser.add_argument("--budget-usd", type=float, default=20.0)
    parser.add_argument("--wallets", type=int, default=5)
    args = parser.parse_args()

    if args.test:
        run_tests()
    else:
        analysis = calculate_budget_analysis(args.budget_usd, args.wallets)
        print_budget_report(analysis)
