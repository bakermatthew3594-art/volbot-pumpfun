#!/usr/bin/env python3
"""
Integrated Trading Orchestrator for Pump.fun.

This module orchestrates the complete trading system:
1. SmartBundler (20+ wallet management with anti-detection)
2. BondingCurveTrader (price impact modeling, natural buy detection)
3. MoneyFlowEngine (capital allocation, fee recovery, emergency exits)
4. ChartPatternSimulator (organic chart pattern generation)

Key features:
- Natural buy matching with proportional response
- Smart selling into big natural buyers (selling into strength)
- Resistance buying when whale sells are detected
- Fee recovery through strategic partial sells
- Organic chart simulation that looks like natural trader activity
- Never graduating on purpose (stays below $69K MC)

Usage:
    from trading_orchestrator import TradingOrchestrator
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TOKEN_MINT")
    orch.run_cycle(duration_minutes=5, initial_buy_sol=0.50)
"""

import os
import sys
import time
import json
import random
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from smart_bundler import (
    SmartBundler, SmartWallet, WalletRole, BundledTrade,
    create_bundler, get_recommended_wallet_count,
)
from bonding_curve_trader import (
    BondingCurveTrader, PriceImpactModel, PricePoint,
    NaturalBuyerEvent, SellDecision,
)
from money_flow import (
    MoneyFlowEngine, ChartPatternSimulator, MarketPhase, TradeEvent,
)


@dataclass
class OnChainEvent:
    """Represents an on-chain event detected from the blockchain."""
    timestamp: float
    event_type: str  # 'buy', 'sell', 'liquidity_add', 'transfer'
    wallet: str
    amount_sol: float
    token_amount: float = 0.0
    tx_signature: str = ""
    is_natural: bool = True  # Not from our wallets
    price_impact_pct: float = 0.0
    is_large: bool = False  # >0.5 SOL


class NaturalSellDefender:
    """
    Defends against large natural sells by buying the dip.

    When a large natural sell is detected (>0.5 SOL), we respond with
    buys from 2-3 wallets to create a support level and prevent price
    from crashing, which makes the chart look like it has strong hands.
    """

    def __init__(self, money_flow: MoneyFlowEngine):
        self.money_flow = money_flow
        self.defense_events: List[Dict] = []

    def evaluate_natural_sell(self, event: OnChainEvent) -> Optional[Dict]:
        """Evaluate if a natural sell warrants a buyback response."""
        if not event.is_natural or event.event_type != "sell":
            return None

        if event.amount_sol < 0.3:
            return None  # Too small to matter

        # Calculate response size
        # For a 1.0 SOL natural sell, respond with 0.3-0.5 SOL buy
        response_ratio = random.uniform(0.4, 0.6)
        response_sol = event.amount_sol * response_ratio

        # Don't respond if bubble risk is high
        if self.money_flow.bundler.bubble_risk > 0.6:
            response_sol *= 0.3

        # Don't respond if we don't have budget
        total_available = sum(w.remaining_budget for w in self.money_flow.bundler.wallets
                             if w.role not in (WalletRole.COVER, WalletRole.GAS))
        response_sol = min(response_sol, total_available * 0.3)

        if response_sol < 0.05:
            return None  # Not worth the gas

        # Build buyback bundle
        bundle = self.money_flow.bundler.build_buy_bundle(
            token_mint=self.money_flow.token_mint,
            total_sol=response_sol,
            natural_buy=False,
            dip_detected=True,
            bubble_risk=self.money_flow.bundler.bubble_risk,
        )

        if not bundle:
            return None

        actual_response = sum(t.amount_sol for t in bundle)
        self.money_flow.total_sol_spent += actual_response
        self.money_flow.bundler.total_sol_spent = self.money_flow.total_sol_spent

        # Update MC (buyback supports price)
        if self.money_flow.chart_sim:
            mc_mult = max(self.money_flow.chart_sim.mc_multiplier(), 1.0)
            impact_pct = 0.55 / mc_mult
            buy_impact = self.money_flow.current_mc_usd * impact_pct * actual_response
            sell_impact = self.money_flow.current_mc_usd * impact_pct * event.amount_sol * 0.7
            net_change = buy_impact - sell_impact
            self.money_flow.current_mc_usd = max(
                self.money_flow.current_mc_usd + net_change,
                self.money_flow.current_mc_usd * 0.9  # Don't let MC drop too fast
            )
            self.money_flow.chart_sim.update_price_from_mc(self.money_flow.current_mc_usd)

        defense = {
            "natural_sell": event.amount_sol,
            "response_sol": actual_response,
            "response_ratio": response_ratio,
            "wallets_active": len(bundle),
            "new_mc": self.money_flow.current_mc_usd if self.money_flow.chart_sim else 0,
            "reason": f"Buying dip after {event.amount_sol:.2f} SOL natural sell",
        }
        self.defense_events.append(defense)
        print(f"[DEFENSE] Natural sell: {event.amount_sol:.2f} SOL → Response: {actual_response:.4f} SOL ({len(bundle)} wallets)")
        return defense


class NaturalBuySeller:
    """
    Intelligent selling system that profits from natural buyer activity.

    When large natural buyers pump the price, we sell small amounts
    from multiple wallets to capture profit without crashing the market.

    Key rules:
    - Only sell when natural volume > 1.0 SOL (market is hot)
    - Sell 20-50% of position depending on MC multiplier
    - Never sell more than 5% of circulating supply per minute
    - Stagger sells across 3-5 wallets over 10-30 seconds
    - Use cover wallet for largest sells (clean history)
    - Whale wallet sells smallest percentage (maintain market presence)
    """

    def __init__(self, money_flow: MoneyFlowEngine):
        self.money_flow = money_flow
        self.sell_events: List[Dict] = []

    def evaluate_sell_opportunity(self, current_mc_multiplier: float, natural_volume_60s: float) -> Optional[Dict]:
        """
        Evaluate if current conditions are good for selling.

        Criteria:
        1. MC multiplier >= 2x (minimum profit threshold)
        2. Natural volume > 0.5 SOL (market has liquidity)
        3. No strong momentum (price isn't rocketing up)
        4. Bubble risk < 0.6 (don't sell in bubble territory)
        5. Enough tokens held across wallets
        """
        # Check minimum MC threshold
        if current_mc_multiplier < 2.0:
            return None

        # Check natural volume (need buyers for liquidity)
        if natural_volume_60s < 0.3:
            return None  # Not enough liquidity, sells would crash price

        # Check momentum - don't sell if price is rocketing
        if self.money_flow.bundler.roc > 0.15:
            return None  # Strong upward momentum, hold

        # Check bubble risk
        if self.money_flow.bundler.bubble_risk > 0.6:
            return None  # Too risky to sell in bubble conditions

        # Check we have tokens
        total_tokens = sum(w.tokens_held for w in self.money_flow.bundler.wallets)
        if total_tokens <= 0:
            return None

        # Calculate sell ratio based on MC multiplier
        if current_mc_multiplier >= 10:
            sell_ratio = 0.70
        elif current_mc_multiplier >= 5:
            sell_ratio = 0.50
        elif current_mc_multiplier >= 3:
            sell_ratio = 0.30
        else:
            sell_ratio = 0.20

        return {
            "mc_multiplier": current_mc_multiplier,
            "natural_volume": natural_volume_60s,
            "sell_ratio": sell_ratio,
            "total_tokens": total_tokens,
            "should_sell": True,
            "reason": f"MC {current_mc_multiplier:.1f}x with {natural_volume_60s:.1f} SOL natural volume",
        }

    def execute_natural_sell(self, sell_ratio: float, current_price: float, entry_price: float) -> Optional[Dict]:
        """
        Execute a smart sell that profits from natural buyer liquidity.

        Sells are distributed across wallets with:
        - Cover wallet sells first (clean exit)
        - Noise/Sniper wallets sell second (smaller amounts)
        - Normal/Mid wallets sell third (moderate amounts)
        - Whale wallet sells last and smallest (maintains presence)
        """
        trades, metadata = self.money_flow.bundler.build_sell_bundle(
            token_mint=self.money_flow.token_mint,
            current_price=current_price,
            entry_price=entry_price,
        )

        if not trades:
            return None

        total_sold = metadata["total_sol_sold"]
        self.money_flow.total_sol_recovered += total_sold
        self.money_flow.total_fees_recovered += total_sold * 0.003  # Approx fee recovery

        # Update MC after sell
        if self.money_flow.chart_sim and total_sold > 0:
            mc_mult = max(self.money_flow.chart_sim.mc_multiplier(), 1.0)
            impact_pct = 0.55 / mc_mult
            mc_decrease = self.money_flow.current_mc_usd * impact_pct * (total_sold / 1.0)
            # Staggered sells reduce impact by 50%
            mc_decrease *= 0.5
            self.money_flow.current_mc_usd = max(
                self.money_flow.current_mc_usd - mc_decrease,
                self.money_flow.current_mc_usd * 0.9
            )
            self.money_flow.chart_sim.update_price_from_mc(self.money_flow.current_mc_usd)

        # Harvest profits to cover wallet
        self.money_flow.harvest_to_cover()

        result = {
            "mc_multiplier": self.money_flow.mc_multiplier(),
            "sol_sold": total_sold,
            "wallets_selling": metadata["wallets_selling"],
            "estimated_impact": metadata["estimated_price_impact"],
            "fee_recovery": self.money_flow.total_fees_recovered,
            "fee_target": self.money_flow.fee_recovery_target,
            "reason": metadata.get("reason", ""),
        }
        self.sell_events.append(result)
        print(f"[SELL] MC {result['mc_multiplier']:.1f}x: sold {total_sold:.4f} SOL ({metadata['wallets_selling']} wallets, impact: {metadata['estimated_price_impact']:.1%})")
        return result


class ChartPatternGenerator:
    """
    Generates organic-looking price charts that attract natural traders.

    Simulates:
    1. Initial pump from our wallets (looks like early adopters)
    2. Consolidation with small natural volume (looks like organic interest)
    3. Natural buyer waves (triggers FOMO)
    4. Dip and recovery (shows strong hands)
    5. Gradual profit taking (doesn't look like a rug pull)

    The pattern never goes parabolic (no 100x pumps) to avoid
    attracting sniper bots and creating unsustainable expectations.
    """

    # Organic chart templates (MC multipliers over time intervals)
    # Each step is ~5 seconds
    TEMPLATE_STEADY_GROWTH = [
        1.0, 1.1, 1.2, 1.15, 1.25, 1.4, 1.35, 1.5, 1.6, 1.7,
        1.65, 1.8, 1.9, 1.85, 2.0, 2.1, 2.0, 2.2, 2.3, 2.5,
        2.4, 2.6, 2.5, 2.7, 2.8, 2.7, 2.9, 3.0, 2.9, 3.1
    ]

    TEMPLATE_DIP_RECOVERY = [
        1.0, 1.3, 1.2, 0.9, 1.1, 1.4, 1.3, 1.6, 1.5, 1.8,
        1.7, 2.0, 1.9, 1.5, 1.8, 2.2, 2.0, 2.5, 2.3, 2.8,
        2.6, 1.9, 2.4, 2.8, 3.0, 2.7, 3.2, 3.0, 3.5, 3.3
    ]

    TEMPLATE_VOLATILE = [
        1.0, 1.5, 1.2, 1.8, 1.4, 2.2, 1.6, 2.5, 2.0, 3.0,
        2.4, 3.5, 2.8, 4.0, 3.2, 4.5, 3.8, 5.0, 4.2, 3.5,
        4.0, 2.8, 3.8, 4.5, 3.3, 4.0, 4.8, 5.5, 4.2, 5.0
    ]

    def __init__(self, orchestrator: "TradingOrchestrator"):
        self.orch = orchestrator
        self.template = random.choice([
            self.TEMPLATE_STEADY_GROWTH,
            self.TEMPLATE_DIP_RECOVERY,
            self.TEMPLATE_VOLATILE,
        ])
        self.step_index = 0
        self.natural_volume_profile = self._generate_volume_profile()

    def _generate_volume_profile(self) -> List[float]:
        """Generate natural volume profile matching the price template."""
        profile = []
        for i, mc_mult in enumerate(self.template):
            # Volume spikes at turning points
            if i > 0:
                prev = self.template[i - 1]
                change = abs(mc_mult - prev) / prev if prev > 0 else 0
            else:
                change = 0

            if change > 0.15:  # Big move = high volume
                vol = random.uniform(0.5, 1.5)
            elif change > 0.05:  # Medium move = medium volume
                vol = random.uniform(0.2, 0.8)
            else:  # Small move = low volume
                vol = random.uniform(0.05, 0.3)

            profile.append(vol)
        return profile

    def get_next_step(self) -> Tuple[float, float]:
        """Get the next chart step (MC multiplier, natural volume)."""
        if self.step_index >= len(self.template):
            # Loop or extend with random variation
            self.step_index = len(self.template) - 1
            self.template[-1] *= random.uniform(0.95, 1.05)

        mc_mult = self.template[self.step_index]
        natural_vol = self.natural_volume_profile[min(self.step_index, len(self.natural_volume_profile) - 1)]

        self.step_index += 1
        return mc_mult, natural_vol

    def reset_pattern(self) -> None:
        """Reset the chart pattern to inject new momentum after stagnant periods."""
        self.template = random.choice([
            self.TEMPLATE_STEADY_GROWTH,
            self.TEMPLATE_DIP_RECOVERY,
            self.TEMPLATE_VOLATILE,
        ])
        self.step_index = 0
        self.natural_volume_profile = self._generate_volume_profile()

    def generate_initial_buy_sequence(self, total_sol: float, phases: int = 5) -> List[BundledTrade]:
        """
        Generate the initial buy sequence that creates the first pump.

        Distributes buys across multiple phases with varying amounts
        to create a natural-looking price ramp rather than a single spike.
        """
        all_trades = []
        sol_per_phase = total_sol / phases

        for phase in range(phases):
            # Vary the amount per phase (anti-pattern detection)
            phase_amount = sol_per_phase * random.uniform(0.6, 1.4)
            phase_amount = min(phase_amount, total_sol * 0.4)  # Cap at 40% per phase

            bundle = self.orch.money_flow.bundler.build_buy_bundle(
                token_mint=self.orch.money_flow.token_mint,
                total_sol=phase_amount,
                natural_buy=False,
                dip_detected=phase > 0,  # Later phases look like dip buys
                bubble_risk=min(0.1 * phase, 0.3),  # Increasing bubble risk
            )
            all_trades.extend(bundle)
            total_buys = sum(t.amount_sol for t in bundle)
            self.orch.money_flow.total_sol_spent += total_buys

            # Update MC
            if self.orch.money_flow.chart_sim:
                mc_mult = max(self.orch.money_flow.chart_sim.mc_multiplier(), 1.0)
                impact_pct = 0.55 / mc_mult
                mc_increase = self.orch.money_flow.current_mc_usd * impact_pct * total_buys
                self.orch.money_flow.current_mc_usd += mc_increase
                self.orch.money_flow.chart_sim.update_price_from_mc(self.orch.money_flow.current_mc_usd)

            # Small delay between phases (simulated, very short in test)
            if not self.orch.test_mode:
                time.sleep(1.0)

        return all_trades


class TradingOrchestrator:
    """
    Main orchestrator that coordinates all trading components.

    Workflow:
    1. Initialize wallets and funding
    2. Launch token on Pump.fun
    3. Execute initial buy bundle (creates first price movement)
    4. Monitor natural volume and react accordingly
    5. Take profits at milestones
    6. Defend against natural sells
    7. Emergency exit if conditions deteriorate
    """

    def __init__(
        self,
        budget_sol: float = 6.0,
        token_mint: str = "",
        initial_mc_usd: float = 450.0,
        test_mode: bool = False,
    ):
        self.test_mode = test_mode
        self.money_flow = MoneyFlowEngine(
            budget_sol=budget_sol,
            token_mint=token_mint,
            initial_mc_usd=initial_mc_usd,
            test_mode=test_mode,
        )
        self.sell_manager: Optional[NaturalBuySeller] = None
        self.sell_defense: Optional[NaturalSellDefender] = None
        self.chart_gen: Optional[ChartPatternGenerator] = None
        self.is_running = False
        self.start_time: float = 0.0
        self.total_cycles = 0

    def initialize(self, creator_seed: str = "") -> Dict:
        """Initialize all components of the trading system."""
        wallets = self.money_flow.initialize_wallets(creator_seed=creator_seed)

        # Attach human-like profiles to each wallet based on role
        # Profiles influence trading behavior: buy amounts, frequency, comment style
        self._assign_wallet_profiles(wallets)

        # Create sub-components
        self.sell_manager = NaturalBuySeller(self.money_flow)
        self.sell_defense = NaturalSellDefender(self.money_flow)
        self.chart_gen = ChartPatternGenerator(self)

        return {
            "wallets_initialized": len(wallets),
            "budget_sol": self.money_flow.budget_sol,
            "fee_recovery_target": self.money_flow.fee_recovery_target,
            "initial_mc_usd": self.money_flow.initial_mc,
        }

    def _assign_wallet_profiles(self, wallets: List) -> None:
        """Assign trading profiles to wallets based on their role.

        Each profile adjusts buy_amount_multiplier and comment probability
        based on the wallet's trading_style (aggressive/moderate/scalper/etc.)
        """
        try:
            from profile_gen import generate_profiles_for_bundle
            profiles_data = generate_profiles_for_bundle(num_wallets=len(wallets), seed=None)
            for i, w in enumerate(wallets):
                if i < len(profiles_data["profiles"]):
                    p = profiles_data["profiles"][i]
                    w.profile = p
                    # Adjust trading behavior by style
                    style = p.get("trading_style", "moderate")
                    if style == "aggressive":
                        w.buy_amount_multiplier = 1.5
                        w.comment_probability = 0.7
                    elif style == "conservative":
                        w.buy_amount_multiplier = 0.5
                        w.comment_probability = 0.3
                    elif style == "scalper":
                        w.buy_amount_multiplier = 0.8
                        w.comment_probability = 0.6
                    elif style == "fan":
                        w.buy_amount_multiplier = 0.3
                        w.comment_probability = 0.9
                    else:
                        w.buy_amount_multiplier = 1.0
                        w.comment_probability = 0.5
        except ImportError:
            # Fallback: use role-based multipliers
            for w in wallets:
                role_str = getattr(w.role, 'name', str(w.role)).lower() if hasattr(w, 'role') else 'bot'
                if role_str == 'whale':
                    w.buy_amount_multiplier = 2.0
                    w.comment_probability = 0.4
                elif role_str == 'mid':
                    w.buy_amount_multiplier = 1.0
                    w.comment_probability = 0.5
                elif role_str == 'noise':
                    w.buy_amount_multiplier = 0.3
                    w.comment_probability = 0.8
                elif role_str == 'commenter':
                    w.buy_amount_multiplier = 0.2
                    w.comment_probability = 1.0
                else:
                    w.buy_amount_multiplier = 1.0
                    w.comment_probability = 0.5

    def launch_and_trade(
        self,
        initial_buy_sol: float = 0.50,
        duration_minutes: float = 5.0,
        target_mc_multiplier: float = 5.0,
    ) -> Dict:
        """
        Main trading cycle: launch, trade, and exit.

        Args:
            initial_buy_sol: SOL to deploy in initial buy bundle
            duration_minutes: How long to trade (max)
            target_mc_multiplier: Target MC multiplier before taking profits
        """
        if not self.money_flow.is_initialized:
            self.initialize()

        self.is_running = True
        self.start_time = time.time()
        duration_seconds = duration_minutes * 60

        print(f"\n{'=' * 60}")
        print(f"TRADING ORCHESTRATOR - Starting Cycle")
        print(f"Budget: {self.money_flow.budget_sol} SOL")
        print(f"Initial MC: ${self.money_flow.initial_mc:.2f}")
        print(f"Target: {target_mc_multiplier}x MC")
        print(f"Duration: {duration_minutes} minutes")
        print(f"{'=' * 60}")

        # Phase 1: Execute initial buy bundle
        print("\n[PHASE 1] Initial Bundle Launch")
        initial_result = self.money_flow.launch_initial_bundle(initial_buy_sol)
        print(f"  Bundle: {initial_result['bundle_size']} wallets, {initial_result['total_sol']:.4f} SOL")
        print(f"  MC: ${initial_result['new_mc']:.2f} ({initial_result['mc_multiplier']:.1f}x)")

        # Phase 2: Generate organic-looking chart
        print("\n[PHASE 2] Organic Chart Simulation")
        buy_sequence = self.chart_gen.generate_initial_buy_sequence(
            total_sol=initial_buy_sol * 0.5,  # Additional 50% of initial
            phases=3,
        )
        print(f"  Additional buys: {len(buy_sequence)} trades")

        # Phase 3: Main trading loop
        print("\n[PHASE 3] Active Trading Loop")
        cycle = 0
        consecutive_no_trade_cycles = 0  # Track cycles with zero real activity
        MAX_NO_TRADE_CYCLES = 10  # Break after this many idle cycles (bubble scenarios)
        while self.is_running and (time.time() - self.start_time) < duration_seconds:
            cycle += 1
            self.total_cycles = cycle

            # Get next chart step
            target_mc_mult, natural_vol = self.chart_gen.get_next_step()

            # Simulate natural buys
            if natural_vol > 0.3:
                mc_before = self.money_flow.current_mc_usd
                # Natural buy pushes MC up
                target_mc = self.money_flow.initial_mc * target_mc_mult
                if target_mc > mc_before:
                    self.money_flow.chart_sim.update_price_from_mc(target_mc)
                    self.money_flow.chart_sim.simulate_natural_buy(natural_vol, is_organic=True)

            # Check for our response to natural volume
            cycle_had_activity = False
            orch_cycle_last_response_sol = 0.0
            if self.money_flow.bundler.natural_buy_volume > 0.5:
                response = self.money_flow.react_to_natural_buy(
                    self.money_flow.bundler.natural_buy_volume
                )
                if response:
                    orch_cycle_last_response_sol = response['response_sol']
                    print(f"  Cycle {cycle}: Natural buy response ({response['response_sol']:.4f} SOL)")
                    if response['response_sol'] > 0:
                        cycle_had_activity = True

            # Check for profit-taking opportunities
            current_mc_mult = self.money_flow.mc_multiplier()
            if current_mc_mult >= 2.0:
                natural_vol_60s = self.money_flow.bundler.natural_buy_volume
                opportunity = self.sell_manager.evaluate_sell_opportunity(
                    current_mc_multiplier=current_mc_mult,
                    natural_volume_60s=natural_vol_60s,
                )
                if opportunity:
                    result = self.sell_manager.execute_natural_sell(
                        sell_ratio=opportunity["sell_ratio"],
                        current_price=self.money_flow.chart_sim.current_price_sol if self.money_flow.chart_sim else 0,
                        entry_price=self.money_flow.chart_sim.current_price_sol / current_mc_mult if self.money_flow.chart_sim else 0,
                    )
                    if result:
                        print(f"  Cycle {cycle}: Profit take ({result['sol_sold']:.4f} SOL recovered)")
                        if result['sol_sold'] > 0:
                            cycle_had_activity = True

            # Check for natural sell defense
            if natural_vol < 0.1 and self.money_flow.bundler.roc < -0.05:
                # Simulate natural sell
                event = OnChainEvent(
                    timestamp=time.time(),
                    event_type="sell",
                    wallet="external_whale",
                    amount_sol=random.uniform(0.5, 1.0),
                    is_natural=True,
                )
                defense = self.sell_defense.evaluate_natural_sell(event)
                if defense:
                    print(f"  Cycle {cycle}: Sell defense ({defense['response_sol']:.4f} SOL)")
                    if defense['response_sol'] > 0:
                        cycle_had_activity = True

            # Check for emergency conditions
            if self.money_flow.is_near_graduation(0.85):
                print(f"\n[WARNING] Approaching graduation! MC: ${self.money_flow.current_mc_usd:.2f}")
                # Take profits immediately
                emergency_result = self.sell_manager.execute_natural_sell(
                    sell_ratio=0.70,
                    current_price=self.money_flow.chart_sim.current_price_sol if self.money_flow.chart_sim else 0,
                    entry_price=self.money_flow.chart_sim.current_price_sol / self.money_flow.mc_multiplier() if self.money_flow.chart_sim else 0,
                )
                if emergency_result:
                    print(f"  Emergency profit take: {emergency_result['sol_sold']:.4f} SOL")
                    cycle_had_activity = True

            # Check for breakout (MC jumped significantly — could be organic momentum)
            # Only count as activity if actual trades occurred, not just chart-sim volume
            if current_mc_mult >= 2.0 and natural_vol > 0.5 and (
                    orch_cycle_last_response_sol > 0):
                cycle_had_activity = True  # Organic momentum counts as activity

            # Break condition: too many consecutive cycles with zero activity
            # This happens when bubble_risk is high and all response bundles are empty
            if cycle_had_activity:
                consecutive_no_trade_cycles = 0
            else:
                consecutive_no_trade_cycles += 1
                if consecutive_no_trade_cycles >= MAX_NO_TRADE_CYCLES:
                    bubble_risk = self.money_flow.bundler.bubble_risk if hasattr(self.money_flow.bundler, 'bubble_risk') else 0.0
                    if bubble_risk > 0.5:
                        print(f"\n[BREAK] Stagnant bubble risk ({bubble_risk:.2f}) — {consecutive_no_trade_cycles} idle cycles. Exiting trading loop.")
                        break
                    else:
                        # Re-initialize chart pattern to inject new momentum
                        print(f"  [RECOVERY] Cycle {cycle}: Low activity detected — injecting new chart pattern")
                        self.chart_gen.reset_pattern()
                        consecutive_no_trade_cycles = 0

            # Check if we hit target
            if current_mc_mult >= target_mc_multiplier:
                print(f"\n[TARGET] Reached {target_mc_mult:.1f}x MC (${self.money_flow.current_mc_usd:.2f})")
                break

            # Brief pause between cycles (reduced in test mode)
            cycle_time = 3.0 + random.uniform(2.0, 8.0)  # 3-11 seconds per cycle
            sleep_time = min(cycle_time, 1.0) if not self.test_mode else 0.01
            time.sleep(sleep_time)

        self.is_running = False

        # Final summary
        return self.get_final_summary()

    def get_final_summary(self) -> Dict:
        """Get comprehensive summary of the trading session."""
        summary = self.money_flow.get_money_flow_summary()

        summary.update({
            "total_cycles": self.total_cycles,
            "duration_seconds": time.time() - self.start_time if self.start_time else 0,
            "highest_mc": self.money_flow.highest_mc_reached,
            "final_mc_usd": self.money_flow.current_mc_usd,
            "exit_mc": self.money_flow.current_mc_usd,
            "mc_multiplier": self.money_flow.mc_multiplier(),
            "exit_mc_multiplier": self.money_flow.mc_multiplier(),
            "defense_events": len(self.sell_defense.defense_events) if self.sell_defense else 0,
            "sell_events": len(self.sell_manager.sell_events) if self.sell_manager else 0,
            "natural_buyers_detected": len(self.money_flow.bundler.wallets),
        })

        return summary

    def run_simulation(self, duration_minutes: float = 5.0, initial_buy_sol: float = 0.50) -> Dict:
        """Run a complete simulation cycle (test mode)."""
        result = self.launch_and_trade(
            initial_buy_sol=initial_buy_sol,
            duration_minutes=duration_minutes,
            target_mc_multiplier=3.0,  # Conservative for testing
        )
        return result


# ─── Tests ───

def test_orchestrator_initialization():
    """Test that the orchestrator initializes correctly."""
    print("\n[TEST] Orchestrator Initialization")
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)

    result = orch.initialize()
    print(f"  Wallets: {result['wallets_initialized']}")
    print(f"  Budget: {result['budget_sol']} SOL")
    print(f"  Fee target: {result['fee_recovery_target']:.4f} SOL")

    assert result["wallets_initialized"] >= 18
    assert result["budget_sol"] == 6.0
    assert orch.sell_manager is not None
    assert orch.sell_defense is not None
    assert orch.chart_gen is not None

    print("  PASS - Orchestrator initialization")
    return True


def test_full_cycle():
    """Test a complete trading cycle."""
    print("\n[TEST] Full Trading Cycle")
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)

    result = orch.run_simulation(duration_minutes=0.5, initial_buy_sol=0.3)

    print(f"  Cycles: {result['total_cycles']}")
    print(f"  Final MC: ${result['final_mc_usd']:.2f} ({result['mc_multiplier']:.1f}x)")
    print(f"  Total spent: {result['total_sol_spent']:.4f} SOL")
    print(f"  Total recovered: {result['total_sol_recovered']:.4f} SOL")
    print(f"  Net PNL: {result['net_pnl_sol']:.4f} SOL")
    print(f"  Fees recovered: {result.get('fees_recovered', 0):.6f} / {result.get('fee_recovery_target', result.get('fee_target', 0)):.4f}")
    print(f"  Defense events: {result['defense_events']}")
    print(f"  Sell events: {result['sell_events']}")

    # Basic sanity checks
    assert result["total_cycles"] >= 1
    assert result["final_mc_usd"] >= 400  # Should at least not crash

    print("  PASS - Full trading cycle")
    return True


def test_natural_sell_defense():
    """Test the natural sell defense system."""
    print("\n[TEST] Natural Sell Defense")
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)
    orch.initialize()
    orch.money_flow.launch_initial_bundle(buy_sol=0.50)

    # Simulate a large natural sell
    event = OnChainEvent(
        timestamp=time.time(),
        event_type="sell",
        wallet="external_whale_123",
        amount_sol=1.5,
        is_natural=True,
        is_large=True,
    )

    defense = orch.sell_defense.evaluate_natural_sell(event)
    assert defense is not None, "Should defend against large natural sell"
    print(f"  Natural sell: 1.5 SOL")
    print(f"  Response: {defense['response_sol']:.4f} SOL ({defense['wallets_active']} wallets)")

    assert defense["response_sol"] > 0.1, "Response should be significant"
    assert defense["wallets_active"] >= 2, "Should use multiple wallets"

    print("  PASS - Natural sell defense")
    return True


def test_natural_buy_seller():
    """Test the natural buy seller logic."""
    print("\n[TEST] Natural Buy Seller")
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)
    orch.initialize()

    # Set up some tokens
    for w in orch.money_flow.bundler.wallets:
        if w.role != WalletRole.COVER:
            w.tokens_held = random.uniform(10000, 50000)
            w.avg_buy_price = 0.00001

    # Set MC to 5x
    orch.money_flow.current_mc_usd = orch.money_flow.initial_mc * 5
    if orch.money_flow.chart_sim:
        orch.money_flow.chart_sim.update_price_from_mc(orch.money_flow.current_mc_usd)

    # Evaluate sell opportunity
    opportunity = orch.sell_manager.evaluate_sell_opportunity(
        current_mc_multiplier=5.0,
        natural_volume_60s=1.5,
    )
    print(f"  Sell opportunity: {opportunity}")
    assert opportunity is not None, "Should have sell opportunity at 5x with volume"
    assert opportunity["should_sell"] == True

    # Execute sell
    result = orch.sell_manager.execute_natural_sell(
        sell_ratio=opportunity["sell_ratio"],
        current_price=orch.money_flow.chart_sim.current_price_sol if orch.money_flow.chart_sim else 0.00005,
        entry_price=0.00001,
    )
    print(f"  Sell result: {result}")
    assert result is not None
    assert result["sol_sold"] > 0

    print("  PASS - Natural buy seller")
    return True


def test_chart_pattern():
    """Test chart pattern generation."""
    print("\n[TEST] Chart Pattern Generation")
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST_TOKEN", test_mode=True)
    orch.initialize()

    # Get chart steps
    for i in range(5):
        mc_mult, vol = orch.chart_gen.get_next_step()
        print(f"  Step {i+1}: MC {mc_mult:.1f}x, Volume {vol:.2f} SOL")

    assert orch.chart_gen.step_index > 0, "Should have advanced through steps"

    print("  PASS - Chart pattern generation")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("TRADING ORCHESTRATOR TESTS")
    print("=" * 60)

    tests = [
        test_orchestrator_initialization,
        test_natural_sell_defense,
        test_natural_buy_seller,
        test_chart_pattern,
        test_full_cycle,
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
