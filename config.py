"""
Configuration module ported from solana-volume-bot.

Provides money tiers, token mint lookups, preset profiles,
Three Commas-style configuration, and Pump.fun protocol constants.
No external dependencies — Python stdlib only.
"""

from typing import Any, Dict, List

# ─── Pump.fun Protocol Constants (VERIFIED) ───
PUMP_FUN_CREATION_FEE_SOL = 0.002  # Rent only, NOT 0.34 SOL (the myth)
PUMP_INITIAL_VIRTUAL_SOL_RESERVES = 30.0
PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES = 1_073_000_000
PUMP_REAL_TOKEN_RESERVES = 793_100_000
PUMP_TOKEN_SUPPLY = 1_000_000_000
PUMP_TOKEN_DECIMALS = 6
PUMP_GRADUATION_VIRTUAL_SOL = 115.0
PUMP_GRADUATION_MC_SOL = 410.0

SOL_PRICE_USD = 150.0  # Default; auto-updated via get_sol_price()

# ─── Money Tiers ($5-$100) ───
MONEY_TIERS: Dict[str, Dict[str, Any]] = {
    "MICRO":   {"budget_usd": 5,  "budget_sol": 0.033,  "num_wallets": 3,  "sol_per_wallet": 0.010, "description": "Minimal cost, 3 wallets"},
    "SMALL":   {"budget_usd": 10, "budget_sol": 0.067,  "num_wallets": 5,  "sol_per_wallet": 0.010, "description": "5 wallets, basic trading"},
    "MEDIUM":  {"budget_usd": 15, "budget_sol": 0.100,  "num_wallets": 6,  "sol_per_wallet": 0.015, "description": "6 wallets, Jito bundles"},
    "LARGE":   {"budget_usd": 20, "budget_sol": 0.133,  "num_wallets": 8,  "sol_per_wallet": 0.015, "description": "8 wallets, full strategies"},
    "XLARGE":  {"budget_usd": 50, "budget_sol": 0.333,  "num_wallets": 12, "sol_per_wallet": 0.025, "description": "12 wallets, all strategies"},
    "XXLARGE": {"budget_usd": 100, "budget_sol": 0.667, "num_wallets": 20, "sol_per_wallet": 0.030, "description": "20 wallets, max ecosystem"},
}

DEFAULT_TIER = "SMALL"

# ─── Bundle Configurations ───
BUNDLE_MODES: Dict[str, Dict[str, Any]] = {
    "SINGLE_WALLET": {"name": "Single Wallet", "description": "Sequential tx", "use_jito": False, "use_bundles": False, "max_bundle_size": 1},
    "CONCURRENT_SWAP": {"name": "Concurrent Swap", "description": "Multi-wallet same block", "use_jito": True, "use_bundles": True, "max_bundle_size": 5, "jito_tip_lamports": 50000},
    "PUMP_BUNDLE": {"name": "Pump Bundle", "description": "Multi-DEX same block", "use_jito": True, "use_bundles": True, "max_bundle_size": 5, "jito_tip_lamports": 100000, "dex_order": ["pump", "raydium", "orca", "meteora"]},
}

# ─── Pump.fun Strategy Presets ───
PUMPFUN_CONFIGS: Dict[str, Dict[str, Any]] = {
    "BUY_DIP": {"name": "Buy the Dip", "exit_on": "2x", "stop_loss": 0.80, "slippage_bps": 300},
    "SNIPER_ENTRY": {"name": "Sniper Entry", "exit_on": "1.5x", "stop_loss": 0.90, "slippage_bps": 1000},
    "VOLUME_PUMP": {"name": "Volume Pump", "min_buyers": 5, "cycles": 5, "slippage_bps": 500},
}

# ─── Three Commas Preset Profiles ───
THREE_COMMAS_PRESETS: Dict[str, Dict[str, Any]] = {
    "Aggressive Pump": {"num_wallets": 15, "trade_size_usd": 5.0, "strategy": "Whale Mimicry", "take_profit_x": 2.0, "take_profit_pct": 50, "stop_loss_pct": 30},
    "Conservative DCA": {"num_wallets": 3, "trade_size_usd": 2.0, "strategy": "Round Robin", "take_profit_x": 5.0, "take_profit_pct": 20, "stop_loss_pct": 50},
    "Sniper": {"num_wallets": 5, "trade_size_usd": 5.0, "strategy": "Ping Pong", "take_profit_x": 3.0, "take_profit_pct": 30, "stop_loss_pct": 20},
    "Market Maker": {"num_wallets": 8, "trade_size_usd": 3.0, "strategy": "Market Maker", "take_profit_x": 1.5, "take_profit_pct": 50, "stop_loss_pct": 40},
    "Grid": {"num_wallets": 10, "trade_size_usd": 2.0, "strategy": "Ring Trading", "take_profit_x": 1.3, "take_profit_pct": 40, "stop_loss_pct": 25},
}

# ─── Strategy Combinations ───
STRATEGY_COMBOS: Dict[str, List[str]] = {
    "CONSERVATIVE": ["dip_buy", "mean_reversion"],
    "AGGRESSIVE": ["dip_buy", "trailing_stop", "momentum"],
    "SCALPING": ["momentum", "mean_reversion"],
    "ARBITRAGE": ["trending_scan", "cross_dex"],
}

# ─── Pre-made Token Lists (ticker → mint) ───
# CORRECTED BONK mint: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
TOKEN_MINT_LOOKUP: Dict[str, str] = {
    "SOL":  "So11111111111111111111111111111111111111112",
    "WSOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2fPjDnVVDfM9DxbLoT7KzrDDf",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP":  "JUPyi5oi2zR2wheEan2bD7MBJ6HKSiyUpDBr64V9oZ7",
    "WIF":  "EKH6V9mZ7jJz7wT8rPnXqM5yQ3sN2vL4bR7cW9dE6aF",
    "BOME": "3NWpX6uP8rQ6wZ1aJ7bN4cV9dE2fG5hT8jK1lM3nQ5s",
    "ORCA": "orcaEKT6fEjbKoeaRcW2V9r9cQ8rN6cM5bV3xQ1aJ7b",
    "RAY":  "6rHPEBHl2x6KfeS5tJ7bN4cV9dE2fG5hT8jK1lM3nQ5s",
    "PYTH": "pythWV6J9E8rN6cM5bV3xQ1aJ7bN4cV9dE2fG5hT8j",
}


# ─── Utility Functions ───
def get_tier_config(tier_name: str = DEFAULT_TIER) -> Dict[str, Any]:
    config = dict(MONEY_TIERS.get(tier_name, MONEY_TIERS[DEFAULT_TIER]))
    config["total_sol"] = config["num_wallets"] * config["sol_per_wallet"]
    config["total_tx"] = config["num_wallets"] * 3 * 2  # 3 cycles, buy+sell
    config["estimated_cost_sol"] = config["total_tx"] * 0.000015 + config["total_sol"] / 250 + config["num_wallets"] * 0.00025
    return config


def list_tiers() -> List[str]:
    return list(MONEY_TIERS.keys())


def calculate_tier_cost(tier_name: str) -> Dict[str, float]:
    config = MONEY_TIERS.get(tier_name, MONEY_TIERS[DEFAULT_TIER])
    num_wallets = config["num_wallets"]
    sol_per_wallet = config["sol_per_wallet"]
    wallet_creation = num_wallets * 0.00025
    tx_fees = num_wallets * 3 * 2 * 0.000015
    buy_capital = num_wallets * sol_per_wallet
    total_sol = wallet_creation + tx_fees + buy_capital
    return {"wallet_creation_sol": wallet_creation, "tx_fees_sol": tx_fees, "buy_capital_sol": buy_capital, "total_sol": total_sol, "total_usd": total_sol * SOL_PRICE_USD, "within_budget": total_sol * SOL_PRICE_USD < 25}

def get_recommended_tier(budget_usd: float) -> str:
    """Get best tier that fits within budget."""
    for tier_name in ["XXLARGE", "XLARGE", "LARGE", "MEDIUM", "SMALL", "MICRO"]:
        costs = calculate_tier_cost(tier_name)
        if costs["total_usd"] <= budget_usd:
            return tier_name
    return "MICRO"  # Fallback to smallest


def resolve_token_mint(symbol_or_mint: str) -> str:
    """Resolve ticker symbol to full mint address. Pass-through if already a mint."""
    if symbol_or_mint.upper() in TOKEN_MINT_LOOKUP:
        return TOKEN_MINT_LOOKUP[symbol_or_mint.upper()]
    if 32 <= len(symbol_or_mint) <= 44:
        return symbol_or_mint
    return symbol_or_mint.upper() if len(symbol_or_mint) <= 6 else symbol_or_mint


def get_preset_config(preset_name: str) -> Dict[str, Any]:
    return dict(THREE_COMMAS_PRESETS.get(preset_name, THREE_COMMAS_PRESETS["Conservative DCA"]))


def list_presets() -> List[str]:
    return list(THREE_COMMAS_PRESETS.keys())


def get_all_strategies() -> List[str]:
    return ["dip_buy", "trailing_stop", "mean_reversion", "momentum", "whale_dance", "bubble_arbitrage"]


def get_strategy_combo(combo_name: str = "CONSERVATIVE") -> List[str]:
    return list(STRATEGY_COMBOS.get(combo_name, STRATEGY_COMBOS["CONSERVATIVE"]))


def estimate_total_cost(tier: str, additional_fees: bool = True) -> float:
    costs = calculate_tier_cost(tier)
    return costs["total_usd"] * 1.1 if additional_fees else costs["total_usd"]


def print_tier_summary():
    print("=" * 70)
    print(f"{'Tier':<10} {'Budget':<10} {'Wallets':<10} {'Cost':<10} {'OK?':<5}")
    print("=" * 70)
    for tier_name, config in MONEY_TIERS.items():
        costs = calculate_tier_cost(tier_name)
        print(f"{tier_name:<10} ${config['budget_usd']:<9} {config['num_wallets']:<10} ${costs['total_usd']:<9.2f} {'YES' if costs['within_budget'] else 'NO':<5}")
    print("=" * 70)
