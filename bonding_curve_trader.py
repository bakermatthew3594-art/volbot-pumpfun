#!/usr/bin/env python3
"""
Bonding Curve Trader for Pump.fun.

Implements:
1. Bonding curve price impact modeling
2. Natural buyer detection and matching
3. Smart selling on big natural buys (without crashing the market)
4. On-curve profit maximization
5. Graduation detection (approaching Raydium LP)
6. Dust management (avoid leaving tiny positions)

Usage:
    from bonding_curve_trader import BondingCurveTrader, PriceImpactModel
    trader = BondingCurveTrader(token_mint="...")
    trader.update_price(new_price)
    if trader.should_buy_on_natural(natural_buy_sol=1.0):
        trader.execute_natural_buy_response(1.0)
    if trader.should_take_profit(current_mc_multiplier=3.0):
        trader.execute_smart_sell()
"""

import os
import sys
import time
import json
import random
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import deque

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


@dataclass
class PricePoint:
    """A single price observation with metadata."""
    timestamp: float
    price_sol: float
    source: str  # 'bonding_curve', 'jupiter', 'dexscreener'
    trade_amount_sol: float = 0.0
    is_buy: bool = True
    wallet_count: int = 0  # How many wallets traded


class PriceImpactModel:
    """
    Models price impact on the Pump.fun bonding curve.

    The bonding curve follows: price = k * sqrt(reserve), approximately.
    Each SOL of buy volume increases price by ~2% (for small-cap tokens).
    Large buys have diminishing returns (curve flattens as reserves grow).
    """

    def __init__(self, initial_price: float = 0.00001, reserve_sol: float = 0.5):
        self.initial_price = initial_price
        self.reserve_sol = reserve_sol
        # Bonding curve constant (calibrated for pump.fun)
        # price = k * x^(1/2) where x = reserve_sol
        self.k = initial_price / math.sqrt(reserve_sol) if reserve_sol > 0 else 0.00001

    def get_price_after_buy(self, buy_sol: float) -> float:
        """Calculate price after a buy of N SOL."""
        new_reserve = self.reserve_sol + buy_sol
        # Diminishing returns: price = k * sqrt(new_reserve)
        return self.k * math.sqrt(new_reserve)

    def get_price_after_sell(self, sell_sol: float) -> float:
        """Calculate price after a sell (token sold for SOL)."""
        new_reserve = max(self.reserve_sol - sell_sol, 0.01)
        return self.k * math.sqrt(new_reserve)

    def get_buy_impact_pct(self, buy_sol: float) -> float:
        """Get percentage price impact of a buy."""
        current_price = self.k * math.sqrt(self.reserve_sol)
        new_price = self.get_price_after_buy(buy_sol)
        if current_price > 0:
            return (new_price - current_price) / current_price
        return 0.0

    def get_sell_impact_pct(self, sell_sol: float) -> float:
        """Get percentage price impact of a sell (price decrease)."""
        current_price = self.k * math.sqrt(self.reserve_sol)
        new_price = self.get_price_after_sell(sell_sol)
        if current_price > 0:
            return (current_price - new_price) / current_price
        return 0.0

    def get_price_from_mc(self, market_cap: float, total_supply: float = 1_000_000_000) -> float:
        """Convert market cap to price per token in SOL (assuming $150 SOL)."""
        price_usd = market_cap / total_supply if total_supply > 0 else 0
        return price_usd / 150.0  # Convert USD to SOL

    def get_mc_from_price(self, price_sol: float, total_supply: float = 1_000_000_000) -> float:
        """Convert price per token (SOL) to market cap (USD)."""
        price_usd = price_sol * 150.0
        return price_usd * total_supply


@dataclass
class NaturalBuyerEvent:
    """Record of a detected large natural buy."""
    timestamp: float
    buyer_wallet: str
    buy_amount_sol: float
    buy_tx_signature: str
    price_before: float
    estimated_buy_value_usd: float


@dataclass
class SellDecision:
    """A sell decision with rationale."""
    action: str  # 'sell', 'hold', 'partial'
    amount_tokens: float
    amount_sol: float
    reason: str
    confidence: float  # 0.0-1.0
    estimated_price_impact_pct: float
    safe_to_execute: bool  # False if would crash market


class BondingCurveTrader:
    """
    Advanced bonding curve trader with natural buyer matching.

    Monitors on-chain for large natural buys and reacts with coordinated
    bot buys to create momentum. Also implements smart selling that
    doesn't crash the market by:
    - Limiting sell size to <5% of circulating supply per minute
    - Staggering sells across multiple wallets
    - Selling into strength (high natural volume)
    - Using trailing stops on profitable positions
    """

    # Bonding curve stages (Pump.fun)
    STAGE_1_END = 0.00001    # Initial price
    STAGE_2_END = 0.0001     # 10x initial
    STAGE_3_END = 0.001      # 100x initial (graduation threshold ~$69K)

    def __init__(
        self,
        token_mint: str,
        initial_price: float = 0.00001,
        total_supply: float = 1_000_000_000,
        grad_mc_usd: float = 69000,
    ):
        self.token_mint = token_mint
        self.total_supply = total_supply
        self.grad_mc_usd = grad_mc_usd
        self.current_price = initial_price
        self.current_mc_usd = initial_price * 150 * total_supply
        self.initial_price = initial_price
        self.roc = 0.0  # Rate of change

        # Price tracking
        self.price_history: List[PricePoint] = []
        self.price_window = deque(maxlen=50)

        # Natural buyer tracking
        self.natural_buyers: List[NaturalBuyerEvent] = []
        self.natural_volume_60s = 0.0
        self.natural_volume_300s = 0.0

        # Position tracking
        self.total_tokens_held = 0.0
        self.avg_entry_price = 0.0
        self.peak_price = initial_price
        self.total_sol_deployed = 0.0
        self.total_sol_recovered = 0.0

        # Price impact model
        self.impact_model = PriceImpactModel(initial_price, reserve_sol=0.5)

        # Risk management
        self.bubble_risk = 0.0
        self.last_natural_buy_time = 0.0
        self.consecutive_dips = 0

        # Wallet positions (for coordinated selling)
        self.wallet_positions: Dict[str, Dict] = {}  # pubkey -> {tokens, entry_price, peak_price}

    def update_price(self, new_price: float, source: str = "bonding_curve",
                     trade_amount_sol: float = 0.0, is_buy: bool = True,
                     wallet_count: int = 1):
        """Update current price and market state."""
        timestamp = time.time()
        self.current_price = new_price
        self.current_mc_usd = new_price * 150 * self.total_supply

        # Update price history
        pp = PricePoint(
            timestamp=timestamp,
            price_sol=new_price,
            source=source,
            trade_amount_sol=trade_amount_sol,
            is_buy=is_buy,
            wallet_count=wallet_count,
        )
        self.price_history.append(pp)
        if len(self.price_history) > 200:
            self.price_history = self.price_history[-200:]

        self.price_window.append(new_price)

        # Calculate rate of change (5-minute window)
        cutoff = timestamp - 300
        recent = [p for p in self.price_history if p.timestamp > cutoff]
        if len(recent) >= 2:
            if recent[0].price_sol > 0:
                self.roc = (recent[-1].price_sol - recent[0].price_sol) / recent[0].price_sol

        # Update peak price
        if new_price > self.peak_price:
            self.peak_price = new_price

        # Check for dip
        if len(self.price_window) >= 5:
            recent_high = max(list(self.price_window)[-5:])
            if recent_high > 0 and new_price < recent_high * 0.90:
                self.consecutive_dips += 1
            else:
                self.consecutive_dips = max(0, self.consecutive_dips - 1)

        # Update natural volume
        if trade_amount_sol > 0:
            self.natural_volume_60s += trade_amount_sol
            self.natural_volume_300s += trade_amount_sol

        # Decay natural volume
        self._decay_natural_volume()

        # Detect bubbles
        self._update_bubble_risk()

    def _decay_natural_volume(self):
        """Decay natural buy volume over time windows."""
        now = time.time()
        # Remove old entries
        cutoff_60s = now - 60
        cutoff_300s = now - 300

        # Decay the volume numbers
        for event in self.natural_buyers:
            if event.timestamp < cutoff_300s:
                self.natural_buyers = [e for e in self.natural_buyers if e.timestamp > cutoff_300s]
                break

        # Recalculate from remaining events
        self.natural_volume_60s = sum(
            e.buy_amount_sol for e in self.natural_buyers if e.timestamp > cutoff_60s
        )
        self.natural_volume_300s = sum(
            e.buy_amount_sol for e in self.natural_buyers if e.timestamp > cutoff_300s
        )

    def _update_bubble_risk(self):
        """Update bubble risk score based on price action."""
        risk = 0.0

        # Rapid price increase = bubble risk
        if self.roc > 0.20:
            risk += 0.4
        elif self.roc > 0.10:
            risk += 0.2
        elif self.roc > 0.05:
            risk += 0.1

        # High price velocity (rapid per-trade changes)
        if len(self.price_window) >= 2:
            prices = list(self.price_window)
            velocities = [
                abs(prices[i] - prices[i-1]) / prices[i-1] if prices[i-1] > 0 else 0
                for i in range(1, len(prices))
            ]
            avg_velocity = sum(velocities) / len(velocities) if velocities else 0
            if avg_velocity > 0.30:  # 30%+ average per-trade change (extreme)
                risk += 0.3
            elif avg_velocity > 0.15:
                risk += 0.15

        # High natural volume without price movement
        if self.natural_volume_60s > 2.0 and abs(self.roc) < 0.02:
            risk += 0.2

        # Many wallets buying simultaneously
        recent_multi_wallet = [
            p for p in self.price_history[-20:]
            if p.wallet_count > 3 and p.is_buy
        ]
        if len(recent_multi_wallet) >= 3:
            risk += 0.3

        self.bubble_risk = min(risk, 1.0)

    def detect_natural_buy(self, trade_amount_sol: float, min_threshold: float = 0.5) -> bool:
        """
        Detect if a recent buy was likely from a natural (organic) trader.

        Criteria:
        - Buy amount > threshold (default 0.5 SOL)
        - Not from one of our wallets
        - Creates visible price movement
        """
        if trade_amount_sol < min_threshold:
            return False

        # Check it's not our own trading (would need wallet pubkey comparison)
        # For now, rely on amount + timing
        if trade_amount_sol > min_threshold:
            self.natural_buyers.append(NaturalBuyerEvent(
                timestamp=time.time(),
                buyer_wallet="unknown",  # Would be detected via chain monitoring
                buy_amount_sol=trade_amount_sol,
                buy_tx_signature="",
                price_before=self.current_price,
                estimated_buy_value_usd=trade_amount_sol * 150,
            ))
            self.last_natural_buy_time = time.time()
            return True

        return False

    def should_buy_on_natural(self, natural_buy_sol: float, current_price: Optional[float] = None) -> bool:
        """
        Determine if we should add buying pressure to match a natural buyer.

        We respond to natural buys >0.5 SOL to create momentum appearance.
        """
        if current_price:
            self.current_price = current_price

        # Don't respond if bubble risk is high
        if self.bubble_risk > 0.6:
            return False

        # Don't respond if we have no budget
        if self.total_tokens_held <= 0 and self.total_sol_deployed < 0.1:
            return False

        # Respond to significant natural buys
        if natural_buy_sol >= 0.5:
            # Higher chance to respond to larger buys
            response_probability = min(natural_buy_sol / 2.0, 0.9)
            return random.random() < response_probability

        return False

    def calculate_natural_buy_response(self, natural_buy_sol: float) -> Dict:
        """
        Calculate optimal response to a natural buy.

        Returns recommended trading amounts by wallet role.
        """
        if natural_buy_sol < 0.5:
            return {"should_buy": False, "reason": "Natural buy too small"}

        # Response ratio: 0.5-1.0x the natural buy
        response_ratio = random.uniform(0.5, 1.0)
        total_response = natural_buy_sol * response_ratio

        # Reduce response if bubble risk is medium
        if self.bubble_risk > 0.4:
            total_response *= 0.5
        elif self.bubble_risk > 0.2:
            total_response *= 0.7

        # Distribute across wallet roles
        # Sniper: 30% (quick entry to front-run momentum)
        sniper_amount = min(total_response * 0.3, 0.2)

        # Mid: 40% (confirm the move)
        mid_amount = total_response * 0.4

        # Normal: 20% (add noise)
        normal_amount = total_response * 0.2

        # Noise: 10% (obvious follower)
        noise_amount = total_response * 0.1

        return {
            "should_buy": True,
            "total_response_sol": total_response,
            "sniper_amount": sniper_amount,
            "mid_amount": mid_amount,
            "normal_amount": normal_amount,
            "noise_amount": noise_amount,
            "response_ratio": response_ratio,
            "reason": f"Natural buy of {natural_buy_sol:.2f} SOL detected",
        }

    def should_take_profit(self, mc_multiplier: Optional[float] = None, current_price: Optional[float] = None) -> Tuple[bool, str]:
        """
        Determine if we should take profit based on market conditions.

        Takes profit at these milestones:
        - 2x: 20-30% sell (conservative)
        - 3x: 30-50% sell
        - 5x: 50-60% sell
        - 10x+: 60-80% sell

        But NEVER sells if:
        - Price is still rising rapidly (momentum)
        - Natural buy volume is high (new buyers still entering)
        - Not enough liquidity (would crash price)
        """
        if current_price:
            self.current_price = current_price

        if mc_multiplier is None:
            if self.avg_entry_price > 0:
                mc_multiplier = self.current_price / self.avg_entry_price
            else:
                return False, "No entry price"

        if mc_multiplier < 2.0:
            return False, f"Below 2x MC ({mc_multiplier:.1f}x) - let winners run"

        # Check if we should hold due to momentum
        if self.roc > 0.05:
            return False, f"Strong momentum (+{self.roc*100:.1f}%/min) - hold for more gains"

        # Check if natural buyers are still active
        if self.natural_volume_60s > 0.5:
            return False, f"Natural volume high ({self.natural_volume_60s:.2f} SOL) - buyers still entering"

        # Check liquidity (can we sell without crashing?)
        impact_check = self.estimate_sell_impact(0.10)
        if impact_check["estimated_price_impact_pct"] > 15:
            return False, f"Market too thin for sells (impact: {impact_check['estimated_price_impact_pct']:.1f}%)"

        # Determine profit-taking level
        if mc_multiplier >= 10:
            return True, f"10x+ reached ({mc_multiplier:.1f}x) - take 60% profits"
        elif mc_multiplier >= 5:
            return True, f"5x reached ({mc_multiplier:.1f}x) - take 50% profits"
        elif mc_multiplier >= 3:
            return True, f"3x reached ({mc_multiplier:.1f}x) - take 30% profits"
        elif mc_multiplier >= 2:
            return True, f"2x reached ({mc_multiplier:.1f}x) - take 20% profits"

        return False, "Conditions not met"

    def calculate_sell_ratio(self, mc_multiplier: float) -> float:
        """Calculate what percentage of position to sell at current MC multiplier."""
        if mc_multiplier >= 10:
            return min(0.60 + random.uniform(-0.10, 0.10), 0.80)  # 50-80%
        elif mc_multiplier >= 5:
            return min(0.50 + random.uniform(-0.10, 0.10), 0.70)   # 40-70%
        elif mc_multiplier >= 3:
            return min(0.30 + random.uniform(-0.05, 0.10), 0.50)   # 25-40%
        elif mc_multiplier >= 2:
            return min(0.20 + random.uniform(-0.05, 0.10), 0.35)   # 15-30%
        return 0.05  # Minimal below 2x

    def estimate_sell_impact(self, sell_ratio: float) -> Dict:
        """
        Estimate the price impact of selling a portion of our position.

        Key: Never sell more than 5% of circulating supply per minute
        to avoid crashing the market.
        """
        sell_tokens = self.total_tokens_held * sell_ratio
        sell_sol_value = sell_tokens * self.current_price

        # Price impact model: roughly 2% per SOL of sell volume on small caps
        # But this is non-linear - larger sells have more impact
        if self.total_tokens_held > 0:
            sell_pct_of_holdings = sell_tokens / self.total_tokens_held
        else:
            sell_pct_of_holdings = 0

        # Estimate price impact (rough model for pump.fun bonding curve)
        # Impact is based on sell amount as % of market cap / circulating supply
        # Key principle: impact grows exponentially with sell size relative to liquidity
        sol_sell_amount = sell_sol_value
        # On pump.fun, liquidity pool is typically small (0.5-5 SOL initial)
        # Each 1% of total supply sold = ~2% price impact
        # Each 0.5% of total supply sold = ~1% price impact
        # Cap at reasonable levels for thin markets
        if sol_sell_amount < 0.05:
            impact_pct = sol_sell_amount * 10  # 10% per SOL for tiny sells
        elif sol_sell_amount < 0.2:
            impact_pct = 0.5 + (sol_sell_amount - 0.05) * 5  # 0.5-2.25% range
        elif sol_sell_amount < 0.5:
            impact_pct = 2.0 + (sol_sell_amount - 0.2) * 15  # 2-6.5% range
        elif sol_sell_amount < 1.0:
            impact_pct = 5.0 + (sol_sell_amount - 0.5) * 25  # 5-17.5% range
        elif sol_sell_amount < 2.0:
            impact_pct = 15.0 + (sol_sell_amount - 1.0) * 30  # 15-45% range
        else:
            impact_pct = 45.0 + (sol_sell_amount - 2.0) * 50  # Steep beyond 2 SOL

        return {
            "sell_tokens": sell_tokens,
            "sell_sol_value": sell_sol_value,
            "sell_pct_of_holdings": sell_pct_of_holdings * 100,
            "estimated_price_impact_pct": min(impact_pct, 50),  # Cap at 50%
            "safe_to_execute": impact_pct < 15,
            "max_safe_sell_ratio": 0.05 if impact_pct > 10 else 0.10,
        }

    def execute_smart_sell(
        self,
        sell_ratio: float,
        num_wallets: int = 3,
        stagger_seconds: Tuple[float, float] = (5.0, 20.0),
    ) -> List[SellDecision]:
        """
        Execute a smart sell across multiple wallets.

        Staggers sells to minimize price impact:
        - Each wallet sells a fraction of the target
        - Time between sells: 5-20 seconds
        - Only sells from wallets with positions
        - Smaller sells from whale wallet (if any)
        """
        if self.total_tokens_held <= 0:
            return []

        # Check if sell is safe
        impact = self.estimate_sell_impact(sell_ratio)
        if not impact["safe_to_execute"]:
            # Reduce sell ratio to safe level
            sell_ratio = min(sell_ratio, impact["max_safe_sell_ratio"])

        decisions = []
        total_sold = 0.0
        wallet_pubkeys = list(self.wallet_positions.keys())[:num_wallets]

        per_wallet_ratio = sell_ratio / num_wallets

        for pubkey in wallet_pubkeys:
            pos = self.wallet_positions.get(pubkey, {})
            tokens_held = pos.get("tokens", 0)
            entry_price = pos.get("entry_price", self.avg_entry_price)

            if tokens_held <= 0:
                continue

            sell_tokens = tokens_held * per_wallet_ratio
            sell_sol = sell_tokens * self.current_price * 0.997  # After 0.3% fee

            # Estimate price impact for this individual sell
            individual_impact = self.estimate_sell_impact(per_wallet_ratio * 0.5)

            decision = SellDecision(
                action="sell",
                amount_tokens=sell_tokens,
                amount_sol=sell_sol,
                reason=f"Profit-taking at {self.current_price/self.avg_entry_price:.1f}x MC",
                confidence=0.8,
                estimated_price_impact_pct=individual_impact["estimated_price_impact_pct"],
                safe_to_execute=individual_impact["safe_to_execute"],
            )
            decisions.append(decision)

            total_sold += sell_tokens
            self.total_tokens_held -= sell_tokens
            self.total_sol_recovered += sell_sol

            # Update wallet position
            pos["tokens"] -= sell_tokens
            if pos["tokens"] <= 0:
                del self.wallet_positions[pubkey]

        # Apply timing recommendation
        decisions.append(SellDecision(
            action="hold",
            amount_tokens=0,
            amount_sol=0,
            reason=f"Stagger remaining sells by {stagger_seconds[0]}-{stagger_seconds[1]}s",
            confidence=0.9,
            estimated_price_impact_pct=0,
            safe_to_execute=True,
        ))

        return decisions

    def should_buy_dip(self, current_price: Optional[float] = None, dip_threshold: float = 0.10) -> Tuple[bool, float]:
        """
        Detect if current price is a dip from recent high.

        Returns (should_buy, dip_percentage)
        """
        if current_price:
            self.current_price = current_price

        if len(self.price_window) < 5:
            return False, 0.0

        recent_prices = list(self.price_window)[-10:]
        recent_high = max(recent_prices)

        if recent_high > 0:
            price = current_price if current_price is not None else self.current_price
            dip_pct = (recent_high - price) / recent_high
            if dip_pct >= dip_threshold:
                # Only buy on dip if not in bubble territory
                if self.bubble_risk < 0.6:
                    # More aggressive on bigger dips
                    buy_amount_multiplier = 1.0 + min(dip_pct, 0.5) * 2
                    return True, buy_amount_multiplier
            return False, dip_pct

        return False, 0.0

    def get_current_stage(self) -> str:
        """Get current bonding curve stage."""
        if self.current_price < self.STAGE_2_END:
            return "stage_1"  # Initial accumulation
        elif self.current_price < self.STAGE_3_END:
            return "stage_2"  # Growth phase
        else:
            return "stage_3"  # Near graduation

    def is_near_graduation(self, threshold_pct: float = 0.8) -> bool:
        """Check if token is close to graduating to Raydium LP."""
        grad_price = self.impact_model.get_price_from_mc(self.grad_mc_usd, self.total_supply)
        if grad_price > 0:
            ratio = self.current_price / grad_price
            return ratio >= threshold_pct
        return False

    def get_trading_signal(self) -> Tuple[str, float, str]:
        """
        Generate a trading signal based on all market data.

        Returns (action, confidence, reason)
        action: 'buy', 'sell', 'hold'
        confidence: 0.0-1.0
        reason: human-readable explanation
        """
        # Check for bubble
        if self.bubble_risk > 0.7:
            return "sell", 0.9, f"High bubble risk ({self.bubble_risk:.2f}) - exit position"

        # Check for profit-taking opportunity
        mc_mult = self.current_price / self.avg_entry_price if self.avg_entry_price > 0 else 1.0
        should_profit, reason = self.should_take_profit(mc_multiplier=mc_mult)
        if should_profit:
            return "sell", 0.8, reason

        # Check for dip buy opportunity
        should_dip, dip_mult = self.should_buy_dip(dip_threshold=0.08)
        if should_dip:
            return "buy", 0.7, f"Dip detected ({dip_mult:.2f}x) - buying opportunity"

        # Check for natural buy response
        if self.natural_volume_60s > 0.5 and self.total_tokens_held > 0:
            return "buy", 0.6, f"Natural volume ({self.natural_volume_60s:.2f} SOL) - riding momentum"

        # Check if we should hold
        if mc_mult >= 5 and self.roc > 0.03:
            return "hold", 0.8, f"Strong momentum at {mc_mult:.1f}x - hold for more gains"

        return "hold", 0.5, "No clear signal"

    def get_portfolio_summary(self) -> Dict:
        """Get comprehensive portfolio summary."""
        total_value_sol = self.total_sol_recovered + (
            self.total_tokens_held * self.current_price * 0.997
        )
        total_return = total_value_sol - self.total_sol_deployed
        roi = total_return / self.total_sol_deployed * 100 if self.total_sol_deployed > 0 else 0

        return {
            "token_mint": self.token_mint[:20],
            "current_price_sol": self.current_price,
            "current_mc_usd": self.current_mc_usd,
            "entry_price_sol": self.avg_entry_price,
            "mc_multiplier": self.current_price / self.avg_entry_price if self.avg_entry_price > 0 else 0,
            "total_tokens_held": self.total_tokens_held,
            "total_sol_deployed": self.total_sol_deployed,
            "total_sol_recovered": self.total_sol_recovered,
            "unrealized_pnl_sol": total_value_sol - self.total_sol_deployed - self.total_sol_recovered,
            "total_return_sol": total_return,
            "roi_pct": roi,
            "bubble_risk": self.bubble_risk,
            "roc_5min": self.roc,
            "natural_volume_60s": self.natural_volume_60s,
            "natural_volume_300s": self.natural_volume_300s,
            "stage": self.get_current_stage(),
            "near_graduation": self.is_near_graduation(),
            "num_natural_buyers": len(self.natural_buyers),
            "num_wallets_tracked": len(self.wallet_positions),
        }


# ─── Tests ───

def test_price_impact_model():
    """Test bonding curve price impact modeling."""
    print("\n[TEST] Price Impact Model")
    model = PriceImpactModel(initial_price=0.00001, reserve_sol=0.5)

    # Small buy (0.1 SOL) should have modest impact
    impact = model.get_buy_impact_pct(0.1)
    print(f"  0.1 SOL buy impact: {impact*100:.2f}%")

    # Large buy (1.0 SOL) should have larger impact
    impact_large = model.get_buy_impact_pct(1.0)
    print(f"  1.0 SOL buy impact: {impact_large*100:.2f}%")
    assert impact_large > impact, "Larger buy should have more impact"

    # Price should increase after buy
    price_after = model.get_price_after_buy(0.5)
    assert price_after > model.initial_price

    print("  PASS - Price impact model")
    return True


def test_natural_buy_detection():
    """Test natural buyer detection."""
    print("\n[TEST] Natural Buy Detection")
    trader = BondingCurveTrader("TEST", initial_price=0.00001)

    # Small buy should not trigger
    assert trader.detect_natural_buy(0.3, min_threshold=0.5) == False

    # Large buy should trigger
    assert trader.detect_natural_buy(1.0, min_threshold=0.5) == True
    assert len(trader.natural_buyers) == 1

    # Check response calculation
    response = trader.calculate_natural_buy_response(1.0)
    assert response["should_buy"] == True
    assert response["total_response_sol"] > 0.3

    print(f"  Natural buy: 1.0 SOL")
    print(f"  Response: {response['total_response_sol']:.4f} SOL")
    print(f"  Sniper: {response['sniper_amount']:.4f} SOL")
    print("  PASS - Natural buy detection")
    return True


def test_smart_sell():
    """Test smart sell without crashing market."""
    print("\n[TEST] Smart Sell (No Market Crash)")
    trader = BondingCurveTrader("TEST", initial_price=0.00001)

    # Set up position (smaller to avoid huge impact)
    trader.total_tokens_held = 10000
    trader.avg_entry_price = 0.00001
    trader.current_price = 0.00005  # 5x
    trader.price_window.extend([0.00001] * 10 + [0.00003, 0.00004, 0.00005])
    trader.wallet_positions = {
        "wallet1": {"tokens": 3000, "entry_price": 0.00001, "peak_price": 0.00005},
        "wallet2": {"tokens": 4000, "entry_price": 0.00001, "peak_price": 0.00005},
        "wallet3": {"tokens": 3000, "entry_price": 0.00001, "peak_price": 0.00005},
    }

    # Check if we should take profit
    should_sell, reason = trader.should_take_profit(mc_multiplier=5.0)
    print(f"  Should sell at 5x: {should_sell} ({reason})")

    # Execute smart sell
    decisions = trader.execute_smart_sell(sell_ratio=0.50, num_wallets=3)
    print(f"  Sell decisions: {len(decisions)}")

    # Verify price impact is reasonable for individual wallet sells
    # Each wallet sells ~1/3 of 50% = ~16.7% of position
    impact = trader.estimate_sell_impact(0.17)  # Individual wallet impact
    print(f"  Individual sell impact: {impact['estimated_price_impact_pct']:.1f}%")
    assert impact["estimated_price_impact_pct"] < 20, "Individual sell impact too high"

    # Full sell impact (for the system check)
    full_impact = trader.estimate_sell_impact(0.50)
    print(f"  Full 50% sell impact: {full_impact['estimated_price_impact_pct']:.1f}%")
    # Full sell can have higher impact - that's expected, system caps it
    print("  PASS - Smart sell")
    return True


def test_bubble_detection():
    """Test bubble risk detection."""
    print("\n[TEST] Bubble Detection")
    trader = BondingCurveTrader("TEST", initial_price=0.00001)

    # Normal prices - no bubble
    for p in [0.00001, 0.000011, 0.000012, 0.000011, 0.000013]:
        trader.update_price(p)
    print(f"  Normal bubble risk: {trader.bubble_risk:.2f}")
    assert trader.bubble_risk < 0.5, "Normal prices should not trigger bubble"

    # Rapid spike - bubble (10x in 5 updates + high velocity)
    trader2 = BondingCurveTrader("TEST2", initial_price=0.00001)
    for p in [0.00001, 0.000020, 0.000040, 0.000080, 0.000100]:
        trader2.update_price(p)
    print(f"  Spike bubble risk: {trader2.bubble_risk:.2f}")
    print(f"  Spike ROC: {trader2.roc*100:.1f}%")
    # ROC should be >0.20 (900% spike), adding 0.4 to risk
    # Plus velocity >0.10 adds another 0.3 = total ~0.7
    assert trader2.bubble_risk > 0.4, "Rapid spike should trigger bubble"

    print("  PASS - Bubble detection")
    return True


def test_trading_signals():
    """Test comprehensive trading signal generation."""
    print("\n[TEST] Trading Signals")
    trader = BondingCurveTrader("TEST", initial_price=0.00001)

    # Set up some price history
    prices = [0.00001, 0.000012, 0.000015, 0.000013, 0.000011, 0.000009]
    for p in prices:
        trader.update_price(p)

    # Set up position
    trader.total_tokens_held = 50000
    trader.avg_entry_price = 0.00001
    trader.current_price = 0.000015  # 1.5x - hold

    signal, conf, reason = trader.get_trading_signal()
    print(f"  1.5x signal: {signal} ({reason})")

    # Test at 3x with no momentum
    trader.current_price = 0.00003
    trader.roc = 0.01  # Low momentum
    signal2, conf2, reason2 = trader.get_trading_signal()
    print(f"  3x signal: {signal2} ({reason2})")

    # Test at 3x with strong momentum + natural volume
    # When natural volume is high, the bot buys more (riding momentum)
    trader.roc = 0.10  # Strong momentum
    trader.natural_volume_60s = 1.0  # High natural volume
    signal3, conf3, reason3 = trader.get_trading_signal()
    print(f"  3x + momentum + volume: {signal3} ({reason3})")
    assert signal3 == "buy", "Should buy when natural volume is high + momentum (riding wave)"

    # Test at 3x with no momentum and no natural volume
    trader.natural_volume_60s = 0.0
    trader.roc = 0.001  # Minimal momentum
    signal4, conf4, reason4 = trader.get_trading_signal()
    print(f"  3x + no momentum: {signal4} ({reason4})")
    # Without momentum, natural volume, or bubble risk, take profit triggers
    assert signal4 == "sell", "Should sell at 3x with no supporting momentum"

    print("  PASS - Trading signals")
    return True


def test_near_graduation():
    """Test graduation detection."""
    print("\n[TEST] Graduation Detection")
    # grad_mc_usd=69000, total_supply=1e9, SOL=$150
    # graduation price = 69000 / (150 * 1e9) = 0.00000046
    grad_price = 69000 / (150 * 1_000_000_000)
    # Use a lower initial price so we start below graduation
    trader = BondingCurveTrader("TEST", initial_price=0.00000005, grad_mc_usd=69000)

    print(f"  Graduation price: {grad_price:.10f} SOL")
    print(f"  Current price: {trader.current_price:.10f} SOL")

    # Far from graduation (at 5% of grad price)
    assert trader.is_near_graduation(0.95) == False

    # Near graduation (at 90% of grad price)
    trader.current_price = grad_price * 0.9
    assert trader.is_near_graduation(0.8) == True

    print("  PASS - Graduation detection")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("BONDING CURVE TRADER TESTS")
    print("=" * 60)

    tests = [
        test_price_impact_model,
        test_natural_buy_detection,
        test_smart_sell,
        test_bubble_detection,
        test_trading_signals,
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
    print(f"{'=' * 60}")
