"""
Backtesting engine for Solana trading strategies.

Tests strategies against historical price data to evaluate performance.
Calculates key metrics: Sharpe ratio, max drawdown, win rate, profit factor.

Usage:
    from backtest import BacktestEngine, generate_mock_price_data
    from strategies import DipBuyStrategy, PriceBuffer
    from trading_engine import get_advanced_quote

    # Or use with mock data
    prices = generate_mock_price_data(100, start_price=100, volatility=0.02)
    engine = BacktestEngine(prices, fee_per_trade=0.000015)
    results = engine.run_backtest(DipBuyStrategy, initial_capital=1000)
"""

import math
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Type

from strategies import TradingStrategy, PriceBuffer
from trading_engine import LAMPORTS_PER_SOL


def generate_mock_price_data(
    num_periods: int,
    start_price: float = 100.0,
    volatility: float = 0.02,
    trend: float = 0.0,
    seed: Optional[int] = None,
) -> List[Dict[str, float]]:
    """
    Generate realistic mock price data for backtesting.

    Args:
        num_periods: Number of time periods (e.g., minutes)
        start_price: Starting price
        volatility: Standard deviation of price changes
        trend: Average drift per period (positive = uptrend, negative = downtrend)
        seed: Random seed for reproducibility

    Returns:
        List of dicts with keys: price, volume, timestamp
    """
    if seed is not None:
        random.seed(seed)

    data = []
    current_price = start_price
    base_time = time.time() - num_periods * 60  # 1 minute per period

    for i in range(num_periods):
        # Random walk with drift
        change = random.gauss(trend, volatility)
        current_price *= (1 + change)
        current_price = max(current_price, 0.01)  # Prevent negative prices

        # Volume inversely correlated with price drops (panic selling)
        if change < 0:
            volume = random.uniform(500000, 1000000) * (1 - change * 2)
        else:
            volume = random.uniform(500000, 1000000)

        # Add occasional spikes (5% chance)
        if random.random() < 0.05:
            spike = random.choice([-1, 1]) * random.uniform(0.05, 0.15)
            current_price *= (1 + spike)
            volume *= 5

        data.append({
            "price": round(current_price, 4),
            "volume": round(volume, 2),
            "timestamp": base_time + i * 60,
        })

    return data


def generate_mean_reverting_data(
    num_periods: int,
    mean_price: float = 100.0,
    amplitude: float = 10.0,
    noise: float = 0.5,
    seed: Optional[int] = None,
) -> List[Dict[str, float]]:
    """Generate mean-reverting price data (good for testing mean reversion strategies)."""
    if seed is not None:
        random.seed(seed)

    data = []
    current_price = mean_price
    base_time = time.time() - num_periods * 60

    for i in range(num_periods):
        # Mean reversion: pull toward mean + oscillating noise
        mean_pull = (mean_price - current_price) * 0.1
        oscillation = amplitude * math.sin(i * 0.3 + random.gauss(0, 0.5))
        random_noise = random.gauss(0, noise)

        current_price += mean_pull + oscillation + random_noise
        current_price = max(current_price, 0.01)

        volume = random.uniform(500000, 800000)

        data.append({
            "price": round(current_price, 4),
            "volume": round(volume, 2),
            "timestamp": base_time + i * 60,
        })

    return data


def generate_trending_data(
    num_periods: int,
    start_price: float = 100.0,
    trend_strength: float = 0.01,
    volatility: float = 0.01,
    seed: Optional[int] = None,
) -> List[Dict[str, float]]:
    """Generate trending price data (good for testing momentum strategies)."""
    if seed is not None:
        random.seed(seed)

    data = []
    current_price = start_price
    base_time = time.time() - num_periods * 60

    for i in range(num_periods):
        # Strong trend with low volatility
        change = random.gauss(trend_strength, volatility)
        current_price *= (1 + change)
        current_price = max(current_price, 0.01)

        # Volume decreases during trends (less noise)
        volume = random.uniform(300000, 600000)

        # Add volume spikes at trend reversals
        if i > 10 and (data[i-1]["price"] < data[i-2]["price"] and current_price > data[i-1]["price"]):
            volume *= 3

        data.append({
            "price": round(current_price, 4),
            "volume": round(volume, 2),
            "timestamp": base_time + i * 60,
        })

    return data


class BacktestEngine:
    """
    Backtesting engine for trading strategies.

    Runs a strategy against historical price data and calculates:
    - Total return
    - Sharpe ratio
    - Max drawdown
    - Win rate
    - Profit factor
    - Number of trades
    """

    def __init__(
        self,
        price_data: List[Dict[str, float]],
        fee_per_trade: float = 0.000015,  # SOL per transaction
        slippage_pct: float = 0.003,  # 0.3% slippage per trade
    ):
        self.price_data = price_data
        self.fee_per_trade = fee_per_trade
        self.slippage_pct = slippage_pct
        self.trades: List[Dict] = []

    def run_backtest(
        self,
        strategy_class: Type[TradingStrategy],
        initial_capital: float = 1000,
        strategy_kwargs: Dict = None,
    ) -> Dict[str, Any]:
        """
        Run a backtest with a given strategy.

        Args:
            strategy_class: A TradingStrategy subclass
            initial_capital: Starting capital in USD
            strategy_kwargs: Additional kwargs for strategy constructor

        Returns:
            Dict with backtest results and metrics
        """
        if strategy_kwargs is None:
            strategy_kwargs = {}

        capital = initial_capital
        capital_history = [initial_capital]
        price_history = []
        volume_history = []

        # Create a SINGLE strategy instance that persists across all steps
        # This allows stateful strategies (TrailingStop, MeanReversion) to track positions
        # Use a large maxlen to hold all price data
        strategy_instance = strategy_class(PriceBuffer(maxlen=len(self.price_data)), **strategy_kwargs) if strategy_kwargs else strategy_class(PriceBuffer(maxlen=len(self.price_data)))

        # Track position state externally so backtest can manage capital
        position_size = 0  # How much of the position we hold (in token units)
        entry_price = 0
        in_position = False

        for i, data_point in enumerate(self.price_data):
            price = data_point["price"]
            volume = data_point["volume"]

            price_history.append(price)
            volume_history.append(volume)

            # Only start trading after enough data for indicators
            if len(price_history) < 20:
                capital_history.append(capital)
                continue

            # Update the strategy's internal buffer (reuse same instance)
            strategy_instance.buffer.add(price, volume=volume)
            # Check position state BEFORE signal() modifies it
            was_in_position = hasattr(strategy_instance, 'entry_price') and strategy_instance.entry_price is not None

            signal, confidence = strategy_instance.signal()

            # Execute trades based on signal
            # Only buy if we weren't already in a position before this signal
            if signal == "BUY" and confidence > 0.3 and not was_in_position:
                # Buy with position sizing based on confidence
                trade_capital = capital * min(confidence, 0.5)  # Max 50% of capital
                fee = self.fee_per_trade * 150  # ~0.000015 SOL * $150/SOL
                net_trade = trade_capital * (1 - self.slippage_pct) - fee
                position_size = net_trade / price if price > 0 else 0
                entry_price = price
                capital -= trade_capital
                self.trades.append({
                    "time": data_point.get("timestamp", i),
                    "action": "BUY",
                    "price": price,
                    "size": trade_capital,
                    "value": net_trade,
                    "confidence": confidence,
                    "capital_after": capital,
                })

            elif signal == "SELL" and confidence > 0.3 and position_size > 0:
                # Sell the position
                gross_value = position_size * price
                fee = self.fee_per_trade * 150
                net_value = gross_value * (1 - self.slippage_pct) - fee
                capital += net_value
                self.trades.append({
                    "time": data_point.get("timestamp", i),
                    "action": "SELL",
                    "price": price,
                    "size": gross_value,
                    "value": net_value,
                    "confidence": confidence,
                    "capital_after": capital,
                })
                position_size = 0
                entry_price = 0

            capital_history.append(capital)

        return self._calculate_metrics(capital, capital_history, initial_capital)

    def _calculate_metrics(self, final_capital: float, capital_history: List[float],
                          initial_capital: float) -> Dict[str, Any]:
        """Calculate backtest performance metrics."""
        total_return = ((final_capital - initial_capital) / initial_capital) * 100

        # Calculate returns per period
        returns = []
        for i in range(1, len(capital_history)):
            if capital_history[i-1] > 0:
                r = (capital_history[i] - capital_history[i-1]) / capital_history[i-1]
                returns.append(r)

        avg_return = sum(returns) / len(returns) if returns else 0
        std_return = math.sqrt(sum((r - avg_return) ** 2 for r in returns) / len(returns)) if returns else 0

        # Sharpe ratio (annualized assuming 1-min periods)
        sharpe = (avg_return / std_return) * math.sqrt(525600) if std_return > 0 else 0

        # Max drawdown
        peak = capital_history[0] if capital_history else initial_capital
        max_drawdown = 0
        for value in capital_history:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak if peak > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        # Win rate
        buy_trades = [t for t in self.trades if t["action"] == "BUY"]
        sell_trades = [t for t in self.trades if t["action"] == "SELL"]

        winning_trades = 0
        losing_trades = 0
        for i in range(1, len(self.trades)):
            if self.trades[i]["action"] == "SELL":
                # Compare sell price to previous buy price
                prev_buy = None
                for j in range(i-1, -1, -1):
                    if self.trades[j]["action"] == "BUY":
                        prev_buy = self.trades[j]["price"]
                        break
                if prev_buy:
                    if self.trades[i]["price"] > prev_buy:
                        winning_trades += 1
                    else:
                        losing_trades += 1

        total_trades = winning_trades + losing_trades
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # Profit factor
        gross_profit = sum(t["value"] for t in sell_trades if t["value"] > 0)
        gross_loss = abs(sum(t["value"] for t in sell_trades if t["value"] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "initial_capital": initial_capital,
            "final_capital": round(final_capital, 2),
            "total_return_pct": round(total_return, 2),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
            "total_trades": len(self.trades),
            "buy_trades": len(buy_trades),
            "sell_trades": len(sell_trades),
            "capital_history": [round(c, 2) for c in capital_history],
        }

    def get_trade_log(self) -> List[Dict]:
        """Return the list of all trades executed during the backtest."""
        return self.trades


def run_strategy_comparison(
    price_data: List[Dict[str, float]],
    strategies: List[tuple],
    initial_capital: float = 1000,
) -> Dict[str, Dict]:
    """
    Run multiple strategies against the same price data and compare results.

    Args:
        price_data: Historical price data
        strategies: List of (name, strategy_class, kwargs) tuples
        initial_capital: Starting capital

    Returns:
        Dict of {strategy_name: results}
    """
    results = {}
    for name, strategy_class, kwargs in strategies:
        engine = BacktestEngine(price_data, fee_per_trade=0.000015)
        results[name] = engine.run_backtest(strategy_class, initial_capital, kwargs)
        results[name]["trade_count"] = len(engine.get_trade_log())
    return results


def format_backtest_results(results: Dict[str, Any]) -> str:
    """Format backtest results as a readable summary."""
    lines = [
        "=== Backtest Results ===",
        f"Initial Capital: ${results['initial_capital']:.2f}",
        f"Final Capital: ${results['final_capital']:.2f}",
        f"Total Return: {results['total_return_pct']:.2f}%",
        f"Sharpe Ratio: {results['sharpe_ratio']:.4f}",
        f"Max Drawdown: {results['max_drawdown_pct']:.2f}%",
        f"Win Rate: {results['win_rate_pct']:.2f}%",
        f"Profit Factor: {results['profit_factor']:.2f}x",
        f"Total Trades: {results['total_trades']}",
        f"  Buys: {results['buy_trades']}",
        f"  Sells: {results['sell_trades']}",
        "=== End Results ===",
    ]
    return "\n".join(lines)


def generate_volatile_data(
    num_periods: int,
    start_price: float = 100.0,
    volatility: float = 0.05,
    spike_freq: float = 0.1,
    seed: Optional[int] = None,
) -> List[Dict[str, float]]:
    """
    Generate highly volatile price data with frequent spikes.
    Good for testing volume spike and momentum strategies.
    """
    if seed is not None:
        random.seed(seed)

    data = []
    current_price = start_price
    base_time = time.time() - num_periods * 60

    for i in range(num_periods):
        # Base random walk with high volatility
        change = random.gauss(0, volatility)

        # Add random spikes
        if random.random() < spike_freq:
            spike = random.choice([-1, 1]) * random.uniform(0.05, 0.20)
            change += spike

        current_price *= (1 + change)
        current_price = max(current_price, 0.01)

        # High volume on spikes
        if abs(change) > volatility * 2:
            volume = random.uniform(1000000, 3000000) * abs(change) / volatility
        else:
            volume = random.uniform(500000, 1000000)

        data.append({
            "price": round(current_price, 4),
            "volume": round(volume, 2),
            "timestamp": base_time + i * 60,
        })

    return data
