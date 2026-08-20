"""
Enhanced trading strategies for Solana DEX bots.

Additional strategies beyond the basics:
1. Volume Spike Detection - Buy on sudden volume bursts
2. Bollinger Band Squeeze - Entry on band contraction
3. VWAP Deviation - Buy below VWAP, sell above
4. Fibonacci Retracement entries
5. Copy Trading - Follow whale wallet movements
6. Kelly Criterion position sizing
7. Max drawdown circuit breaker
"""

import math
import time
from typing import Any, Dict, List, Optional, Tuple

from strategies import TradingStrategy, PriceBuffer, TechnicalIndicators


class VolumeSpikeStrategy(TradingStrategy):
    """
    Buy when trading volume spikes significantly above average.
    Volume is a leading indicator - precedes price moves.

    Entry: Volume > 3x average + price starts to move
    Exit: Volume returns to normal OR price hits resistance
    """

    def __init__(self, price_buffer: PriceBuffer,
                 volume_multiplier: float = 3.0,
                 rsi_buy_threshold: float = 40,
                 window: int = 20):
        super().__init__(price_buffer)
        self.volume_multiplier = volume_multiplier
        self.rsi_buy_threshold = rsi_buy_threshold
        self.window = window
        self.entry_price = None
        self.peak_price = None

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        volumes = list(self.buffer.volumes)

        if len(prices) < self.window or len(volumes) < self.window:
            return ("HOLD", 0.0)

        current = prices[-1]
        avg_volume = sum(volumes[-self.window:]) / self.window
        current_volume = volumes[-1] if volumes[-1] > 0 else 1
        volume_spike = current_volume / avg_volume if avg_volume > 0 else 0

        rsi = self.ti.rsi(prices, 14)

        if self.entry_price is None:
            # Look for volume spike + price starting to rise
            if volume_spike > self.volume_multiplier and rsi > self.rsi_buy_threshold:
                self.entry_price = current
                self.peak_price = current
                confidence = min(1.0, volume_spike / (self.volume_multiplier * 2))
                return ("BUY", confidence)
        else:
            # Exit conditions
            self.peak_price = max(self.peak_price, current)
            current_vol = volumes[-1] if volumes[-1] > 0 else avg_volume
            if current_vol / avg_volume < 1.5:
                # Volume returning to normal
                self.entry_price = None
                return ("SELL", 0.8)

            # Trailing stop
            if current < self.peak_price * 0.95:
                self.entry_price = None
                return ("SELL", 0.7)

        return ("HOLD", 0.0)


class BollingerSqueezeStrategy(TradingStrategy):
    """
    Bollinger Band squeeze breakout strategy.
    When bands contract (squeeze), a breakout is imminent.
    Enter when price breaks out of the squeeze range.

    Entry: Price breaks above upper band after squeeze
    Exit: Price closes below middle band or hits 2% drop
    """

    def __init__(self, price_buffer: PriceBuffer,
                 bb_period: int = 20, bb_std: int = 2,
                 squeeze_threshold: float = 0.02,
                 profit_target: float = 0.03):
        super().__init__(price_buffer)
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.squeeze_threshold = squeeze_threshold  # Bands width as % of middle
        self.profit_target = profit_target
        self.entry_price = None

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        if len(prices) < self.bb_period + 5:
            return ("HOLD", 0.0)

        current = prices[-1]
        bb_lower, bb_middle, bb_upper = self.ti.bollinger_bands(prices, self.bb_period, self.bb_std)

        band_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 1.0

        if self.entry_price is None:
            # Check for squeeze (narrow bands) + breakout
            if band_width < self.squeeze_threshold and current > bb_upper:
                self.entry_price = current
                confidence = min(1.0, (band_width / self.squeeze_threshold) * 0.5)
                return ("BUY", confidence)
        else:
            # Exit: profit target or band reversion
            if current > self.entry_price * (1 + self.profit_target):
                self.entry_price = None
                return ("SELL", 0.9)

            if current < bb_middle:
                self.entry_price = None
                return ("SELL", 0.6)

        return ("HOLD", 0.0)


class VWAPDeviationStrategy(TradingStrategy):
    """
    Trade based on deviation from VWAP (Volume Weighted Average Price).
    Buy when price is below VWAP (undervalued), sell when above (overvalued).

    Entry: Price < VWAP by threshold
    Exit: Price crosses above VWAP + profit
    """

    def __init__(self, price_buffer: PriceBuffer,
                 vwap_period: int = 50,
                 deviation_threshold: float = 0.02):
        super().__init__(price_buffer)
        self.vwap_period = vwap_period
        self.deviation_threshold = deviation_threshold
        self.entry_price = None

    def _calculate_vwap(self, prices: List[float], volumes: List[float]) -> float:
        """Calculate VWAP for the last N periods."""
        if len(prices) < self.vwap_period or len(volumes) < self.vwap_period:
            actual_len = min(len(prices), len(volumes))
        else:
            actual_len = self.vwap_period

        recent_p = prices[-actual_len:]
        recent_v = volumes[-actual_len:]

        total_vol = sum(recent_v)
        if total_vol == 0:
            return sum(recent_p) / len(recent_p) if recent_p else 0

        vwap_value = sum(p * v for p, v in zip(recent_p, recent_v)) / total_vol
        return vwap_value

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        volumes = list(self.buffer.volumes)

        if len(prices) < 10:
            return ("HOLD", 0.0)

        current = prices[-1]
        vwap = self._calculate_vwap(prices, volumes)

        if vwap == 0:
            return ("HOLD", 0.0)

        deviation = abs(current - vwap) / vwap
        below_vwap = current < vwap

        if self.entry_price is None:
            if below_vwap and deviation > self.deviation_threshold:
                self.entry_price = current
                confidence = min(1.0, deviation / (self.deviation_threshold * 3))
                return ("BUY", confidence)
        else:
            if not below_vwap or current > self.entry_price * 1.01:
                self.entry_price = None
                return ("SELL", 0.7)

        return ("HOLD", 0.0)


class FibonacciRetracementStrategy(TradingStrategy):
    """
    Trade based on Fibonacci retracement levels.
    Buy at 38.2%-50% retracement, sell at 61.8% extension.

    Entry: Price retraces to 38.2% or 50% Fib level
    Exit: Price reaches 61.8% extension from entry
    """

    FIB_LEVELS = {
        0.236: "23.6%",
        0.382: "38.2%",
        0.500: "50.0%",
        0.618: "61.8%",
        0.786: "78.6%",
    }

    def __init__(self, price_buffer: PriceBuffer,
                 lookback: int = 50,
                 entry_level: str = "38.2%",
                 profit_level: str = "61.8%"):
        super().__init__(price_buffer)
        self.lookback = lookback
        self.entry_level = entry_level
        self.profit_level = profit_level
        self.entry_price = None
        self.swing_high = 0
        self.swing_low = 0

    def _get_fib_levels(self) -> Dict[str, float]:
        """Calculate Fibonacci retracement levels."""
        if len(self.buffer.prices) < self.lookback:
            return {}

        recent = self.buffer.to_list()[-self.lookback:]
        swing_high = max(recent)
        swing_low = min(recent)
        range_val = swing_high - swing_low

        if range_val == 0:
            return {}

        self.swing_high = swing_high
        self.swing_low = swing_low

        levels = {}
        for ratio, label in self.FIB_LEVELS.items():
            if swing_high > self.buffer.to_list()[-1]:  # Downtrend retracement
                levels[label] = swing_high - range_val * ratio
            else:  # Uptrend retracement
                levels[label] = swing_low + range_val * ratio
        return levels

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        if len(prices) < self.lookback:
            return ("HOLD", 0.0)

        current = prices[-1]
        fib_levels = self._get_fib_levels()
        if not fib_levels:
            return ("HOLD", 0.0)

        entry_price = fib_levels.get(self.entry_level, 0)
        profit_price = fib_levels.get(self.profit_level, 0)

        if self.entry_price is None:
            # Check if price is near Fibonacci entry level
            if entry_price > 0 and abs(current - entry_price) / entry_price < 0.02:
                self.entry_price = current
                confidence = 0.8
                return ("BUY", confidence)
        else:
            # Exit at profit level
            if profit_price > 0 and current > profit_price:
                self.entry_price = None
                return ("SELL", 0.9)

            # Stop loss at 23.6% level
            stop_loss = fib_levels.get("23.6%", 0)
            if stop_loss > 0 and current < stop_loss:
                self.entry_price = None
                return ("SELL", 0.5)

        return ("HOLD", 0.0)


class CopyTradingStrategy(TradingStrategy):
    """
    Copy trading strategy - follow a list of tracked wallet addresses.
    When a tracked wallet makes a large buy, replicate the trade.

    Requires: list of tracked pubkeys and access to transaction monitoring.
    """

    def __init__(self, price_buffer: PriceBuffer,
                 tracked_wallets: List[str],
                 min_trade_size_usd: float = 1000,
                 max_slippage_bps: int = 500):
        super().__init__(price_buffer)
        self.tracked_wallets = tracked_wallets
        self.min_trade_size_usd = min_trade_size_usd
        self.max_slippage_bps = max_slippage_bps
        self.last_whale_tx: Dict[str, float] = {}  # pubkey -> timestamp
        self.whitelist = set(tracked_wallets)
        self.pending_trades: List[Dict] = []

    def add_whale_trade(self, wallet: str, token_mint: str, usd_amount: float, side: str):
        """Register a whale trade to potentially replicate."""
        if wallet not in self.whitelist:
            return

        if usd_amount >= self.min_trade_size_usd:
            self.pending_trades.append({
                "wallet": wallet,
                "token_mint": token_mint,
                "usd_amount": usd_amount,
                "side": side,
                "timestamp": time.time(),
            })

    def signal(self) -> Tuple[str, float]:
        """Signal based on pending whale trades."""
        if not self.pending_trades:
            return ("HOLD", 0.0)

        # Get the most recent trade
        trade = self.pending_trades.pop(0)
        if trade["side"] == "buy":
            return ("BUY", min(1.0, trade["usd_amount"] / 10000))
        else:
            return ("SELL", min(1.0, trade["usd_amount"] / 10000))

        return ("HOLD", 0.0)


class KellyCriterionSizer:
    """
    Position sizing using the Kelly Criterion.
    Calculates optimal position size based on win rate and win/loss ratio.
    """

    @staticmethod
    def calculate_size(win_rate: float, avg_win: float, avg_loss: float,
                       capital: float, max_fraction: float = 0.25) -> float:
        """
        Calculate position size using Kelly Criterion.

        Args:
            win_rate: Probability of winning (0-1)
            avg_win: Average win amount (in USD)
            avg_loss: Average loss amount (in USD)
            capital: Total capital available
            max_fraction: Maximum fraction to risk (default 25%)

        Returns:
            Position size in USD
        """
        if avg_loss <= 0 or avg_win <= 0 or win_rate <= 0:
            return capital * 0.01  # Default 1% position

        win_loss_ratio = avg_win / avg_loss
        kelly_pct = win_rate - ((1 - win_rate) / win_loss_ratio)

        # Cap at max_fraction and prevent negative
        kelly_pct = max(0, min(kelly_pct, max_fraction))

        return capital * kelly_pct


class DrawdownCircuitBreaker:
    """
    Circuit breaker that stops trading when max drawdown is exceeded.
    """

    def __init__(self, max_drawdown_pct: float = 0.20, lookback_periods: int = 100):
        self.max_drawdown_pct = max_drawdown_pct
        self.lookback_periods = lookback_periods
        self.peak_value = 0.0
        self.current_value = 0.0
        self.trading_halted = False

    def update(self, equity: float) -> bool:
        """Update with current equity. Returns True if trading should halt."""
        if equity > self.peak_value:
            self.peak_value = equity

        self.current_value = equity
        drawdown = (self.peak_value - equity) / self.peak_value if self.peak_value > 0 else 0

        if drawdown > self.max_drawdown_pct:
            self.trading_halted = True
            return True

        return False

    def reset(self):
        """Reset after a recovery period."""
        self.trading_halted = False
        self.peak_value = self.current_value


class MomentumReversalStrategy(TradingStrategy):
    """
    Momentum reversal - buy on strong momentum, sell on exhaustion.
    Uses volume and price action to detect when momentum is fading.
    """

    def __init__(self, price_buffer: PriceBuffer,
                 momentum_window: int = 10,
                 lookback: int = 30,
                 profit_target: float = 0.05):
        super().__init__(price_buffer)
        self.momentum_window = momentum_window
        self.lookback = lookback
        self.profit_target = profit_target
        self.entry_price = None
        self.peak_price = None

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        volumes = list(self.buffer.volumes)

        if len(prices) < self.lookback:
            return ("HOLD", 0.0)

        current = prices[-1]
        recent_prices = prices[-self.lookback:]
        recent_volumes = volumes[-self.lookback:]

        # Calculate momentum (price change over window)
        momentum = (current - recent_prices[-self.momentum_window]) / recent_prices[-self.momentum_window] if recent_prices[-self.momentum_window] > 0 else 0

        # Calculate average volume
        avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 1
        current_volume = volumes[-1] if volumes[-1] > 0 else avg_volume
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        if self.entry_price is None:
            # Strong positive momentum + high volume = bullish
            if momentum > 0.02 and volume_ratio > 2.0:
                self.entry_price = current
                self.peak_price = current
                confidence = min(1.0, momentum * 20)
                return ("BUY", confidence)
        else:
            self.peak_price = max(self.peak_price, current)

            # Take profit
            if current > self.entry_price * (1 + self.profit_target):
                self.entry_price = None
                return ("SELL", 0.9)

            # Momentum exhaustion (price not rising despite high volume)
            if momentum < 0 and volume_ratio > 2.0:
                self.entry_price = None
                return ("SELL", 0.6)

            # Stop loss
            if current < self.peak_price * 0.95:
                self.entry_price = None
                return ("SELL", 0.5)

        return ("HOLD", 0.0)
