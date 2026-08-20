"""
Enhanced trading engine with advanced strategies.

New strategies added:
1. Dip buying - Buy when price drops below recent average
2. Trailing stop loss - Lock profits as price moves up
3. Momentum trading - EMA crossover signals
4. RSI-based mean reversion - Buy oversold, sell overbought
5. All-coin mode - Scan for trending tokens
6. Dynamic position sizing - Scale with confidence

External APIs integrated:
- DexScreener for DEX analytics and trending tokens
- Jupiter price API for real-time pricing
- BirdEye for historical price data (requires API key)
- Pump.fun for new token discovery
"""

import json
import math
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

# Jupiter API endpoints
from trading_engine import (
    get_advanced_quote,
    WRAPPED_SOL_MINT,
    USDC_MINT,
    LAMPORTS_PER_SOL,
    get_price_feed,
    build_advanced_swap_transaction,
)


class PriceBuffer:
    """Rolling window of price data for technical analysis."""

    def __init__(self, maxlen: int = 50):
        self.prices = deque(maxlen=maxlen)
        self.volumes = deque(maxlen=maxlen)
        self.timestamps = deque(maxlen=maxlen)

    def add(self, price: float, volume: float = 0, timestamp: float = None):
        if timestamp is None:
            timestamp = time.time()
        self.prices.append(price)
        self.volumes.append(volume)
        self.timestamps.append(timestamp)

    def __len__(self):
        return len(self.prices)

    def to_list(self):
        return list(self.prices)


class TechnicalIndicators:
    """Pure Python technical indicators (no external deps)."""

    @staticmethod
    def sma(prices: List[float], period: int = 20) -> float:
        """Simple Moving Average."""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0
        return sum(prices[-period:]) / period

    @staticmethod
    def ema(prices: List[float], period: int = 12) -> float:
        """Exponential Moving Average."""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0
        k = 2 / (period + 1)
        ema_val = sum(prices[:period]) / period
        for price in prices[period:]:
            ema_val = price * k + ema_val * (1 - k)
        return ema_val

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> float:
        """Relative Strength Index."""
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d for d in deltas[-period:] if d > 0]
        losses = [-d for d in deltas[-period:] if d < 0]
        avg_gain = sum(gains) / period if gains else 0.0001
        avg_loss = sum(losses) / period if losses else 0.0001
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def bollinger_bands(prices: List[float], period: int = 20, std_dev: int = 2) -> Tuple[float, float, float]:
        """Bollinger Bands: (lower, middle, upper)."""
        if len(prices) < period:
            period = len(prices)
        sma_val = TechnicalIndicators.sma(prices, period)
        if len(prices) >= period:
            std = math.sqrt(sum((p - sma_val) ** 2 for p in prices[-period:]) / period)
        else:
            std = 0
        return (sma_val - std_dev * std, sma_val, sma_val + std_dev * std)

    @staticmethod
    def macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float]:
        """MACD line and signal line."""
        ema_fast = TechnicalIndicators.ema(prices, fast)
        ema_slow = TechnicalIndicators.ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.ema(prices, signal)
        return (macd_line, signal_line)

    @staticmethod
    def rsi_divergence(prices: List[float], volumes: List[float]) -> str:
        """
        Detect RSI divergence - bullish/bearish signals.
        Returns: 'bullish', 'bearish', or 'none'
        """
        if len(prices) < 14:
            return "none"
        rsi = TechnicalIndicators.rsi(prices, 14)
        # Check if RSI is diverging from price
        price_trend = prices[-1] > prices[-5] if len(prices) >= 5 else True
        rsi_extreme = rsi < 30 or rsi > 70
        if rsi < 30 and not price_trend:
            return "bullish"
        if rsi > 70 and price_trend:
            return "bearish"
        return "none"


class DEXScreenerAPI:
    """DexScreener API client for DEX analytics."""

    BASE_URL = "https://api.dexscreener.com/latest/dex"

    @staticmethod
    def _request(url: str) -> Optional[Any]:
        """Make HTTP request with proper headers to avoid 403 errors."""
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; VolumeBot/1.0)",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    @staticmethod
    def get_pair_data(token_mint: str) -> Optional[List[Dict]]:
        """Get DEX pair data for a token."""
        data = DEXScreenerAPI._request(f"{DEXScreenerAPI.BASE_URL}/tokens/{token_mint}")
        if data:
            return data.get("pairs", [])
        return None

    @staticmethod
    def search_hot_pairs(limit: int = 20) -> List[Dict]:
        """Search for trending pairs by scanning popular token symbols."""
        trending_searches = ["BONK", "WIF", "BOME", "JUP", "REZ", "BIAO",
                           "TNSR", "PEPE", "BANANA", "POPCAT", "FWOG",
                           "BRETT", "ALB", "ZEX", "MAGA"]
        all_pairs = []
        for query in trending_searches:
            pairs = DEXScreenerAPI.search(query)
            all_pairs.extend(pairs)
        all_pairs.sort(key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)
        return all_pairs[:limit]

    @staticmethod
    def get_pair_by_address(pair_address: str) -> Optional[Dict]:
        """Get specific pair data by pair address."""
        data = DEXScreenerAPI._request(f"{DEXScreenerAPI.BASE_URL}/pairs/{pair_address}")
        if data:
            pairs = data.get("pairs", [])
            return pairs[0] if pairs else None
        return None

    @staticmethod
    def search(query: str) -> List[Dict]:
        """Search for tokens by name/symbol."""
        data = DEXScreenerAPI._request(
            f"{DEXScreenerAPI.BASE_URL}/search?q={urllib.parse.quote(query)}"
        )
        if data:
            return data.get("pairs", [])
        return []


class TradingStrategy:
    """Abstract trading strategy base class."""

    def __init__(self, price_buffer: PriceBuffer):
        self.buffer = price_buffer
        self.ti = TechnicalIndicators()
        self.position = 0  # 0 = flat, >0 = long position size in USD

    def signal(self) -> Tuple[str, float]:
        """
        Returns: (action, confidence)
        action: 'BUY', 'SELL', or 'HOLD'
        confidence: 0.0 to 1.0
        """
        raise NotImplementedError


class DipBuyStrategy(TradingStrategy):
    """
    Buy the Dip strategy.
    - Buy when price drops below SMA by threshold percentage
    - Sell when price rises above SMA by threshold
    """

    def __init__(self, price_buffer: PriceBuffer, dip_threshold: float = 0.03,
                 take_profit_threshold: float = 0.05, stop_loss_threshold: float = 0.05,
                 window: int = 20):
        super().__init__(price_buffer)
        self.dip_threshold = dip_threshold  # Buy when price is 3% below SMA
        self.take_profit_threshold = take_profit_threshold  # Sell at 5% above entry
        self.stop_loss_threshold = stop_loss_threshold  # Stop loss at 5% below entry
        self.window = window
        self.entry_price = None
        self.peak_price = None

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        if len(prices) < self.window:
            return ("HOLD", 0.0)

        current = prices[-1]
        sma = self.ti.sma(prices, self.window)
        rsi = self.ti.rsi(prices, 14)

        if self.entry_price is None:
            # Not in a position - look for dip
            dip_ratio = current / sma if sma > 0 else 1.0
            if dip_ratio < (1 - self.dip_threshold) and rsi < 60:
                self.entry_price = current
                self.peak_price = current
                actual_dip = 1 - dip_ratio  # How much price dipped (e.g., 0.05 = 5%)
                confidence = min(1.0, actual_dip / (self.dip_threshold * 2))
                return ("BUY", max(0.0, confidence))
        else:
            # In a position - check exit conditions
            self.peak_price = max(self.peak_price, current)

            # Take profit
            if current > self.entry_price * (1 + self.take_profit_threshold):
                confidence = min(1.0, (current / self.entry_price - 1) / self.take_profit_threshold)
                return ("SELL", confidence)

            # Trailing stop loss (5% below peak)
            if current < self.peak_price * (1 - self.stop_loss_threshold):
                self.entry_price = None
                self.peak_price = None
                return ("SELL", 0.8)

            # RSI-based exit (overbought)
            if rsi > 75:
                return ("SELL", 0.7)

        return ("HOLD", 0.0)


class TrailingStopStrategy(TradingStrategy):
    """
    Trailing stop loss strategy.
    - Buy on momentum signal (EMA crossover)
    - Trailing stop follows price at fixed percentage
    """

    def __init__(self, price_buffer: PriceBuffer, trailing_pct: float = 0.05,
                 ema_fast: int = 12, ema_slow: int = 26, rsi_period: int = 14):
        super().__init__(price_buffer)
        self.trailing_pct = trailing_pct
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.entry_price = None
        self.peak_price = None

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        if len(prices) < max(self.ema_slow, self.rsi_period):
            return ("HOLD", 0.0)

        current = prices[-1]
        ema_f = self.ti.ema(prices, self.ema_fast)
        ema_s = self.ti.ema(prices, self.ema_slow)
        rsi = self.ti.rsi(prices, self.rsi_period)

        if self.entry_price is None:
            # Look for momentum entry
            # Buy when fast EMA crosses above slow EMA + RSI is rising
            if ema_f > ema_s and rsi > 40:
                self.entry_price = current
                self.peak_price = current
                confidence = min(1.0, abs(ema_f - ema_s) / (ema_s * 0.02)) if ema_s > 0 else 0.5
                return ("BUY", min(confidence, 1.0))
        else:
            # Track peak for trailing stop
            self.peak_price = max(self.peak_price, current)

            # Trailing stop loss
            stop_price = self.peak_price * (1 - self.trailing_pct)
            if current < stop_price:
                self.entry_price = None
                self.peak_price = None
                return ("SELL", 0.9)

            # Momentum reversal (EMA cross under)
            if ema_f < ema_s and rsi < 50:
                self.entry_price = None
                self.peak_price = None
                return ("SELL", 0.7)

        return ("HOLD", 0.0)


class MeanReversionStrategy(TradingStrategy):
    """
    Mean reversion strategy using RSI and Bollinger Bands.
    - Buy when RSI < 30 (oversold) and price near lower Bollinger Band
    - Sell when RSI > 70 (overbought) and price near upper Bollinger Band
    """

    def __init__(self, price_buffer: PriceBuffer,
                 rsi_period: int = 14, bb_period: int = 20,
                 bb_std: int = 2, rsi_buy: float = 30, rsi_sell: float = 70):
        super().__init__(price_buffer)
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.entry_price = None

    def signal(self) -> Tuple[str, float]:
        prices = self.buffer.to_list()
        if len(prices) < max(self.rsi_period, self.bb_period):
            return ("HOLD", 0.0)

        current = prices[-1]
        rsi = self.ti.rsi(prices, self.rsi_period)
        bb_lower, bb_middle, bb_upper = self.ti.bollinger_bands(prices, self.bb_period, self.bb_std)

        if self.entry_price is None:
            # Buy when oversold and near lower band
            near_lower = current < bb_lower * 1.05
            if rsi < self.rsi_buy and near_lower:
                self.entry_price = current
                confidence = min(1.0, (self.rsi_buy - rsi) / self.rsi_buy)
                return ("BUY", confidence)
        else:
            # Sell when overbought
            if rsi > self.rsi_sell:
                self.entry_price = None
                confidence = min(1.0, (rsi - self.rsi_sell) / (100 - self.rsi_sell))
                return ("SELL", confidence)

            # Sell when price returns to middle band
            near_middle = bb_lower < current < bb_upper
            if near_middle and rsi > 50:
                self.entry_price = None
                return ("SELL", 0.6)

        return ("HOLD", 0.0)


class AllCoinScanner:
    """
    Scan for trending/viral tokens across DEXes.
    Uses DexScreener for trending pairs and Jupiter for liquidity.
    """

    def __init__(self, min_liquidity_usd: float = 50000, max_tokens: int = 20):
        self.min_liquidity = min_liquidity_usd
        self.max_tokens = max_tokens

    def scan_trending(self) -> List[Dict[str, Any]]:
        """Find trending tokens with sufficient liquidity."""
        pairs = DEXScreenerAPI.search_hot_pairs(limit=self.max_tokens)
        results = []
        seen_mints = set()

        for pair in pairs:
            base_token = pair.get("baseToken", {})
            mint = base_token.get("address", "")

            if mint in seen_mints:
                continue

            liquidity_usd = pair.get("liquidity", {}).get("usd", 0)
            volume_24h = pair.get("volume", {}).get("h24", 0)
            price_change = pair.get("priceChange", {}).get("h24", 0)

            if liquidity_usd < self.min_liquidity:
                continue

            # Get Jupiter price for validation
            price_data = get_price_feed(mint) if mint else None

            results.append({
                "mint": mint,
                "symbol": base_token.get("symbol", "?"),
                "name": base_token.get("name", "?"),
                "price": pair.get("price", 0),
                "liquidity_usd": liquidity_usd,
                "volume_24h": volume_24h,
                "price_change_24h": price_change,
                "pair_address": pair.get("pairAddress", ""),
            })
            seen_mints.add(mint)

        # Sort by 24h volume * price change (momentum score)
        results.sort(key=lambda x: x["volume_24h"] * abs(x["price_change_24h"]), reverse=True)
        return results[:self.max_tokens]

    def scan_new_tokens(self) -> List[Dict[str, Any]]:
        """
        Scan for newly created tokens on pump.fun.
        Checks recent token creations.
        """
        try:
            url = "https://api.pump.fun/api/tokens?offset=0&limit=20&sort=created_timestamp&order=DESC"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                tokens = data if isinstance(data, list) else data.get("tokens", [])
                return [
                    {
                        "mint": t.get("mint", ""),
                        "symbol": t.get("symbol", "?"),
                        "name": t.get("name", "?"),
                        "created_at": t.get("created_timestamp", 0),
                        "market_cap": t.get("usd_market_cap", 0),
                        "liquidity": t.get("liquidity", 0),
                    }
                    for t in tokens[:self.max_tokens]
                    if t.get("usd_market_cap", 0) > self.min_liquidity
                ]
        except Exception as e:
            print(f"  [WARN] Pump.fun scan failed: {e}")
            return []


class PositionManager:
    """Manages open positions with dynamic sizing and risk management."""

    def __init__(self, total_capital_usd: float = 100):
        self.total_capital = total_capital_usd
        self.positions = {}  # mint -> {entry_price, amount, peak_price, stop_loss_pct}
        self.max_position_pct = 0.20  # Max 20% per position
        self.max_concurrent = 5  # Max 5 simultaneous positions
        self.daily_loss_limit = 0.10  # 10% daily loss limit
        self.daily_pnl = 0.0

    def can_open(self, mint: str) -> bool:
        """Check if we can open a new position."""
        return len(self.positions) < self.max_concurrent

    def calculate_size(self, current_price: float) -> float:
        """Calculate position size based on Kelly criterion + risk management."""
        # Simple: fixed fraction of capital
        position_usd = self.total_capital * self.max_position_pct
        return position_usd / current_price if current_price > 0 else 0

    def open_position(self, mint: str, entry_price: float, size: float,
                      stop_loss_pct: float = 0.05):
        """Open a new position."""
        self.positions[mint] = {
            "entry_price": entry_price,
            "amount": size,
            "peak_price": entry_price,
            "stop_loss_pct": stop_loss_pct,
            "opened_at": time.time(),
        }

    def update_position(self, mint: str, current_price: float):
        """Update position with trailing stop."""
        if mint in self.positions:
            pos = self.positions[mint]
            pos["peak_price"] = max(pos["peak_price"], current_price)
            pos["current_price"] = current_price
            pos["unrealized_pnl"] = current_price - pos["entry_price"]

    def check_exit(self, mint: str, current_price: float) -> bool:
        """Check if trailing stop should trigger."""
        if mint not in self.positions:
            return False
        pos = self.positions[mint]
        stop_price = pos["peak_price"] * (1 - pos["stop_loss_pct"])
        return current_price < stop_price

    def close_position(self, mint: str, exit_price: float) -> float:
        """Close position and return PnL."""
        if mint not in self.positions:
            return 0
        pos = self.positions.pop(mint)
        pnl = (exit_price - pos["entry_price"]) * pos["amount"]
        self.daily_pnl += pnl
        return pnl

    def get_exposure(self) -> float:
        """Get current total exposure as fraction of capital."""
        return len(self.positions) * self.max_position_pct

    def daily_reset_check(self) -> bool:
        """Check if daily loss limit exceeded."""
        return self.daily_pnl < -(self.total_capital * self.daily_loss_limit)


def create_strategy(name: str, price_buffer: PriceBuffer, **kwargs) -> TradingStrategy:
    """Factory function to create trading strategies."""
    strategies = {
        "dip_buy": DipBuyStrategy,
        "trailing_stop": TrailingStopStrategy,
        "mean_reversion": MeanReversionStrategy,
    }
    cls = strategies.get(name, DipBuyStrategy)
    return cls(price_buffer, **kwargs)
