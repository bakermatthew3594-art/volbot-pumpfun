#!/usr/bin/env python3
"""
Budget Configuration & Wallet Allocation System for Pump.fun Trading.

This module provides:
1. Dynamic wallet allocation for any budget amount (USD)
2. Automatic SOL price lookup (CoinGecko)
3. Flexible role distribution based on budget tier
4. Gas fee budgeting and transaction limiting
5. Pump.fun parameter integration (fees, MC thresholds, graduation)

Usage:
    from budget_config import get_budget_config, allocate_wallets, get_recommended_config
    
    config = get_budget_config(budget_usd=20)
    allocation = allocate_wallets(config)
    print(config)
"""

import os
import sys
import json
import random
import math
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any
from enum import Enum

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


# ─── Pump.fun Protocol Constants (Source of Truth) ───
# Verified from pump-fun-sdk (nirholas/pump-fun-sdk) GitHub repo
# These values are HARD-CODED by Pump.fun and never change per token

PUMP_FUN_CREATION_FEE_SOL = 0.002  # Rent only — NO launch fee on Pump.fun!
PUMP_INITIAL_VIRTUAL_SOL_RESERVES = 30.0   # 30 SOL at launch
PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES = 1_073_000_000  # 1.073B tokens
PUMP_REAL_TOKEN_RESERVES = 793_100_000  # Tokens available for sale
PUMP_TOKEN_SUPPLY = 1_000_000_000  # 1B tokens (6 decimals)
PUMP_TOKEN_DECIMALS = 6

# Graduation: when real tokens run out
PUMP_GRADUATION_VIRTUAL_SOL = 115.0  # ~115 SOL virtual reserves
PUMP_GRADUATION_MC_SOL = PUMP_GRADUATION_VIRTUAL_SOL * PUMP_TOKEN_SUPPLY / (PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES - PUMP_REAL_TOKEN_RESERVES)
# = 115 * 1e9 / 280e6 ≈ 410.7 SOL

# Fee tiers: [threshold_sol_mc, protocol_fee_bps, creator_fee_bps]
PUMP_FEE_TIERS = [
    (0, 200, 100),        # Below 100 SOL MC: 3% total
    (100, 100, 50),       # 100-1000 SOL MC: 1.5% total
    (1000, 50, 25),       # Above 1000 SOL MC: 0.75% total
]

PUMP_LAUNCH_PRICE_SOL = PUMP_INITIAL_VIRTUAL_SOL_RESERVES / PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES  # ~0.00002795


def get_sol_price_usd() -> float:
    """Fetch current SOL/USD price from CoinGecko. Falls back to default if unavailable."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data["solana"]["usd"]
    except Exception:
        return 75.0  # Safe fallback


def get_current_sol_price() -> float:
    """Alias for get_sol_price_usd. Always call before budget calculations."""
    return get_sol_price_usd()


def sol_to_usd(sol: float, price: Optional[float] = None) -> float:
    """Convert SOL to USD at current or given price."""
    if price is None:
        price = get_sol_price_usd()
    return sol * price


def usd_to_sol(usd: float, price: Optional[float] = None) -> float:
    """Convert USD to SOL at current or given price."""
    if price is None:
        price = get_sol_price_usd()
    return usd / price


def get_fee_bps_for_mc(mc_sol: float) -> Tuple[int, int, int]:
    """Get fee bps (protocol, creator, total) for a given market cap in SOL."""
    for threshold, proto_bps, creator_bps in reversed(PUMP_FEE_TIERS):
        if mc_sol >= threshold:
            return (proto_bps, creator_bps, proto_bps + creator_bps)
    return PUMP_FEE_TIERS[0][1], PUMP_FEE_TIERS[0][2], PUMP_FEE_TIERS[0][1] + PUMP_FEE_TIERS[0][2]


def get_mc_progress_percent(current_virtual_sol: float) -> float:
    """Get graduation progress as percentage (0-100)."""
    return (current_virtual_sol - PUMP_INITIAL_VIRTUAL_SOL_RESERVES) / (
        PUMP_GRADUATION_VIRTUAL_SOL - PUMP_INITIAL_VIRTUAL_SOL_RESERVES
    ) * 100


def get_mc_at_current_price(current_virtual_sol: float) -> float:
    """Calculate current market cap in SOL at given virtual SOL reserves."""
    k = PUMP_INITIAL_VIRTUAL_SOL_RESERVES * PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES
    virtual_token_reserves = k / current_virtual_sol
    remaining_tokens = PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES - virtual_token_reserves
    # This gives us market cap in SOL
    return current_virtual_sol * PUMP_TOKEN_SUPPLY / virtual_token_reserves


def calculate_buy_impact(sol_in: float, current_virtual_sol: Optional[float] = None) -> float:
    """
    Calculate the % MC increase from buying `sol_in` SOL at current curve state.
    
    Formula: new_virtual_sol = current + sol_in
    new_mc = new_virtual_sol * supply / (k / new_virtual_sol) = new_virtual_sol^2 * supply / k
    mc_increase = (new_mc - old_mc) / old_mc
    
    Simplified for small buys: impact ≈ sol_in / current_virtual_sol
    """
    if current_virtual_sol is None:
        current_virtual_sol = PUMP_INITIAL_VIRTUAL_SOL_RESERVES
    
    old_mc = current_virtual_sol * PUMP_TOKEN_SUPPLY / (PUMP_INITIAL_VIRTUAL_SOL_RESERVES * PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES / current_virtual_sol)
    new_virtual_sol = current_virtual_sol + sol_in
    new_mc = new_virtual_sol * PUMP_TOKEN_SUPPLY / (PUMP_INITIAL_VIRTUAL_SOL_RESERVES * PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES / new_virtual_sol)
    
    return (new_mc - old_mc) / old_mc


# ─── Budget Tier Classification ───

class BudgetTier(Enum):
    MICRO = "MICRO"      # $5  - 3 wallets
    SMALL = "SMALL"      # $10 - 5 wallets
    MEDIUM = "MEDIUM"    # $15 - 6 wallets
    LARGE = "LARGE"      # $20 - 8 wallets
    XLARGE = "XLARGE"    # $50 - 12 wallets
    XXLARGE = "XXLARGE"  # $100+ - 20 wallets


def classify_budget(budget_usd: float) -> BudgetTier:
    """Classify a USD budget into a tier."""
    if budget_usd <= 5:
        return BudgetTier.MICRO
    elif budget_usd <= 10:
        return BudgetTier.SMALL
    elif budget_usd <= 15:
        return BudgetTier.MEDIUM
    elif budget_usd <= 20:
        return BudgetTier.LARGE
    elif budget_usd <= 50:
        return BudgetTier.XLARGE
    else:
        return BudgetTier.XXLARGE


@dataclass
class WalletAllocation:
    """Defines allocation for a specific wallet role."""
    role: str
    count: int
    sol_allocated: float
    usd_allocated: float
    percentage: float
    description: str = ""


@dataclass
class BudgetConfig:
    """Complete configuration for a trading budget."""
    budget_usd: float
    sol_price: float
    budget_sol: float
    tier: BudgetTier
    
    # Wallet allocation
    num_wallets: int
    allocations: List[WalletAllocation]
    
    # Trading parameters
    initial_buy_sol: float
    sol_per_phase: List[float]  # Buy amounts per phase
    sell_thresholds: Dict[int, float]  # MC_mult -> sell %
    
    # Gas and transaction budgeting
    gas_budget_sol: float
    max_transactions: int
    use_jito_bundles: bool
    priority_fee_micro_lamports: int
    
    # Pump.fun specific
    creation_fee_sol: float
    usable_sol: float
    graduation_mc_usd: float
    launch_mc_usd: float
    
    # Strategy flags
    strategies: List[str]
    
    def to_dict(self) -> Dict:
        return {
            "budget_usd": self.budget_usd,
            "sol_price": self.sol_price,
            "budget_sol": self.budget_sol,
            "tier": self.tier.value,
            "num_wallets": self.num_wallets,
            "allocations": [asdict(a) for a in self.allocations],
            "initial_buy_sol": self.initial_buy_sol,
            "sol_per_phase": self.sol_per_phase,
            "sell_thresholds": self.sell_thresholds,
            "gas_budget_sol": self.gas_budget_sol,
            "max_transactions": self.max_transactions,
            "use_jito_bundles": self.use_jito_bundles,
            "priority_fee_micro_lamports": self.priority_fee_micro_lamports,
            "creation_fee_sol": self.creation_fee_sol,
            "usable_sol": self.usable_sol,
            "graduation_mc_usd": self.graduation_mc_usd,
            "launch_mc_usd": self.launch_mc_usd,
            "strategies": self.strategies,
        }


# ─── Allocation Strategies ───

def get_role_distribution(budget_sol: float, num_wallets: int = None, tier: BudgetTier = None) -> Dict[str, int]:
    """
    Determine wallet role distribution based on budget size.
    
    Rules:
    - Always include a Whale (large buy), Cover (clean sells), Gas (fees)
    - More wallets for larger budgets
    - Smaller budgets: fewer roles, bigger allocations
    - Larger budgets: all roles available for maximum obfuscation
    """
    if tier is None:
        tier = classify_budget(budget_sol * get_sol_price_usd())
    
    if tier == BudgetTier.MICRO:
        # $5: 3 wallets — minimal
        return {
            "whale": 1,
            "normal": 2,
        }
    elif tier == BudgetTier.SMALL:
        # $10: 5 wallets
        return {
            "whale": 1,
            "normal": 2,
            "small": 1,
            "cover": 1,
        }
    elif tier == BudgetTier.MEDIUM:
        # $15: 6 wallets
        return {
            "whale": 1,
            "mid": 2,
            "normal": 2,
            "cover": 1,
        }
    elif tier == BudgetTier.LARGE:
        # $20: 8 wallets
        return {
            "whale": 1,
            "mid": 2,
            "normal": 3,
            "sniper": 1,
            "cover": 1,
        }
    elif tier == BudgetTier.XLARGE:
        # $50: 12 wallets
        return {
            "whale": 1,
            "mid": 3,
            "normal": 4,
            "sniper": 2,
            "noise": 1,
            "cover": 1,
        }
    else:  # XXLARGE (= tier, which is XXLARGE)
        # $100+: 20 wallets
        return {
            "whale": 1,
            "mid": 3,
            "normal": 6,
            "noise": 4,
            "sniper": 2,
            "cover": 1,
            "gas": 1,
            "commenter": 2,
        }


def get_percentage_allocation(budget_sol: float, role: str, tier: BudgetTier = None) -> float:
    """
    Get the percentage of total budget for a given role.
    Returns the % of usable budget allocated to this role (total, across all wallets in role).
    """
    if tier is None:
        tier = classify_budget(budget_sol * get_sol_price_usd())
    
    # Base allocations (as percentage of usable budget)
    allocations = {
        BudgetTier.MICRO: {
            "whale": 0.55,     # 55% in one whale wallet
            "normal": 0.45,    # 45% in 2 normal wallets
        },
        BudgetTier.SMALL: {
            "whale": 0.50,       # 50% in whale
            "normal": 0.25,      # 25% in 2 normal
            "small": 0.15,       # 15% in 1 small
            "cover": 0.10,       # 10% in cover
        },
        BudgetTier.MEDIUM: {
            "whale": 0.45,       # 45%
            "mid": 0.30,         # 30% in 2 mid
            "normal": 0.15,      # 15% in 2 normal
            "cover": 0.10,       # 10%
        },
        BudgetTier.LARGE: {
            "whale": 0.40,       # 40%
            "mid": 0.25,         # 25% in 2 mid
            "normal": 0.20,      # 20% in 3 normal
            "sniper": 0.10,      # 10% in 1 sniper
            "cover": 0.05,       # 5%
        },
        BudgetTier.XLARGE: {
            "whale": 0.35,       # 35%
            "mid": 0.25,         # 25% in 3 mid
            "normal": 0.20,      # 20% in 4 normal
            "sniper": 0.10,      # 10% in 2 sniper
            "noise": 0.05,       # 5% in 1 noise
            "cover": 0.05,       # 5%
        },
        BudgetTier.XXLARGE: {
            "whale": 0.20,       # 20% (1 wallet)
            "mid": 0.15,         # 15% (3 wallets, 5% each)
            "normal": 0.30,      # 30% (6 wallets, 5% each)
            "noise": 0.12,       # 12% (4 wallets, 3% each)
            "sniper": 0.06,      # 6% (2 wallets, 3% each)
            "cover": 0.05,       # 5% (1 wallet)
            "gas": 0.08,         # 8% (1 wallet)
            "commenter": 0.04,   # 4% (2 wallets, 2% each)
        },
    }
    
    return allocations.get(tier, {}).get(role, 0.0)


# ─── Gas Budget Calculation ───

def calculate_gas_budget(budget_sol: float, tier: BudgetTier) -> Tuple[float, int]:
    """
    Calculate gas budget and max transactions for given budget.
    
    Returns: (gas_budget_sol, max_transactions)
    
    Rules:
    - Gas budget = 5-10% of total budget (lower % for bigger budgets)
    - Base tx fee: ~0.000005 SOL
    - Priority fee (micro lamports): varies by tier
    - Jito bundle tip: 0.00025-0.0005 SOL per bundle
    """
    if tier == BudgetTier.MICRO:
        gas_budget = budget_sol * 0.10  # 10% for micro (need to be very conservative)
        max_tx = 20  # Only 20 transactions max
    elif tier == BudgetTier.SMALL:
        gas_budget = budget_sol * 0.08  # 8%
        max_tx = 30
    elif tier == BudgetTier.MEDIUM:
        gas_budget = budget_sol * 0.06  # 6%
        max_tx = 50
    elif tier == BudgetTier.LARGE:
        gas_budget = budget_sol * 0.05  # 5%
        max_tx = 70
    elif tier == BudgetTier.XLARGE:
        gas_budget = budget_sol * 0.03  # 3%
        max_tx = 100
    else:  # XXLARGE
        gas_budget = budget_sol * 0.02  # 2%
        max_tx = 150
    
    # Ensure minimum gas budget
    gas_budget = max(gas_budget, 0.005)
    
    return (round(gas_budget, 6), max_tx)


# ─── Buy Phase Calculation ───

def calculate_buy_phases(budget_sol: float, tier: BudgetTier) -> List[float]:
    """
    Calculate SOL amounts for each buy phase.
    
    Creates an organic-looking buy pattern with:
    - Initial strong buy (30-40% of trading capital)
    - Follow-up buys (20-25% each, staggered)
    - Small dip buy (10-15%)
    - Reserve for natural buy matching
    """
    trading_capital = budget_sol * 0.85  # 85% for trading, 15% for gas + response
    
    if tier == BudgetTier.MICRO:
        # Single buy — can't afford multiple phases
        return [trading_capital * 0.6, trading_capital * 0.4]
    elif tier == BudgetTier.SMALL:
        # Two phases
        return [trading_capital * 0.5, trading_capital * 0.3, trading_capital * 0.2]
    elif tier == BudgetTier.MEDIUM:
        # Three phases
        return [trading_capital * 0.4, trading_capital * 0.3, trading_capital * 0.2, trading_capital * 0.1]
    elif tier == BudgetTier.LARGE:
        # Three phases + reserve
        return [trading_capital * 0.35, trading_capital * 0.25, trading_capital * 0.2, trading_capital * 0.2]
    elif tier == BudgetTier.XLARGE:
        # Multiple phases
        return [trading_capital * 0.3, trading_capital * 0.2, trading_capital * 0.15, trading_capital * 0.15, trading_capital * 0.2]
    else:  # XXLARGE
        # Full multi-phase deployment
        return [
            trading_capital * 0.25,  # Initial big buy
            trading_capital * 0.20,  # Follow-up
            trading_capital * 0.15,  # Momentum buy
            trading_capital * 0.10,  # Dip buy
            trading_capital * 0.15,  # Natural buy response
            trading_capital * 0.15,  # Reserve for whale sells
        ]


# ─── Sell Thresholds ───

def calculate_sell_thresholds(budget_sol: float, tier: BudgetTier) -> Dict[int, float]:
    """
    Calculate MC multiplier thresholds for take-profit selling.
    
    Returns dict: {mc_multiplier: percentage_to_sell}
    
    Rules:
    - Must recover gas budget at minimum 2x MC
    - Larger budgets can afford more staggered sells
    - Never sell 100% — always keep some for upward potential
    """
    gas_budget_sol, _ = calculate_gas_budget(budget_sol, tier)
    
    if tier == BudgetTier.MICRO:
        # Need to recover ALL gas at 3x, then exit at 5x
        return {2: 0.15, 3: 0.40, 5: 0.80, 10: 1.0}
    elif tier == BudgetTier.SMALL:
        return {2: 0.15, 3: 0.30, 5: 0.50, 8: 0.80, 15: 1.0}
    elif tier == BudgetTier.MEDIUM:
        return {2: 0.10, 3: 0.25, 5: 0.40, 8: 0.60, 12: 0.80, 20: 1.0}
    elif tier == BudgetTier.LARGE:
        return {2: 0.10, 3: 0.20, 5: 0.35, 8: 0.50, 10: 0.70, 15: 0.90, 25: 1.0}
    elif tier == BudgetTier.XLARGE:
        return {2: 0.05, 3: 0.15, 5: 0.30, 8: 0.45, 10: 0.60, 15: 0.75, 20: 0.90, 30: 1.0}
    else:  # XXLARGE
        return {2: 0.05, 3: 0.10, 5: 0.20, 8: 0.35, 10: 0.50, 15: 0.65, 20: 0.80, 30: 0.95, 50: 1.0}


# ─── Main Configuration Generator ───

def get_budget_config(budget_usd: float, sol_price: Optional[float] = None) -> BudgetConfig:
    """
    Generate complete budget configuration for a given USD amount.
    
    This is the main entry point. Returns a BudgetConfig with:
    - Wallet allocation (role counts and SOL amounts)
    - Buy phase amounts
    - Sell thresholds
    - Gas budget
    - Pump.fun parameters
    - Strategy list
    
    Args:
        budget_usd: Total budget in USD (e.g., 5, 10, 20, 50, 100)
        sol_price: SOL/USD price. If None, fetches from CoinGecko.
    
    Returns:
        BudgetConfig with all parameters for the given budget
    """
    if sol_price is None:
        sol_price = get_sol_price_usd()
    
    budget_sol = usd_to_sol(budget_usd, sol_price)
    tier = classify_budget(budget_usd)
    
    # Calculate creation fee (rent only — Pump.fun has NO launch fee!)
    creation_fee = PUMP_FUN_CREATION_FEE_SOL  # ~0.002 SOL = ~$0.15
    usable_sol = budget_sol - creation_fee
    
    # Gas budget
    gas_budget_sol, max_transactions = calculate_gas_budget(budget_sol, tier)
    
    # Trading capital (usable - gas)
    trading_capital = usable_sol - gas_budget_sol
    
    # Get role distribution
    tier = classify_budget(budget_usd)
    role_dist = get_role_distribution(budget_sol, tier)
    num_wallets = sum(role_dist.values())
    
    # Create allocations
    allocations = []
    for role, count in role_dist.items():
        pct = get_percentage_allocation(budget_sol, role, tier)
        role_sol = round(trading_capital * pct, 6)
        role_usd = round(role_sol * sol_price, 2)
        allocations.append(WalletAllocation(
            role=role,
            count=count,
            sol_allocated=role_sol,
            usd_allocated=role_usd,
            percentage=pct,
            description=f"{role.replace('_', ' ').title()} wallet(s)",
        ))
    
    # Buy phases
    sol_per_phase = calculate_buy_phases(trading_capital, tier)
    initial_buy_sol = sol_per_phase[0]
    
    # Sell thresholds
    sell_thresholds = calculate_sell_thresholds(budget_sol, tier)
    
    # Pump.fun parameters
    graduation_mc_usd = round(PUMP_GRADUATION_MC_SOL * sol_price, 2)
    launch_mc_usd = round(get_mc_at_current_price(PUMP_INITIAL_VIRTUAL_SOL_RESERVES) * sol_price, 2)
    
    # Strategies
    strategies = ["dip_buy"]
    if tier.value in ("MEDIUM", "LARGE", "XLARGE"):
        strategies.extend(["trailing_stop", "mean_reversion"])
    if tier.value in ("LARGE", "XLARGE", "XXLARGE"):
        strategies.extend(["momentum", "natural_buy_response"])
    if tier.value == "XXLARGE":
        strategies.extend(["whale_dance", "bubble_arbitrage"])
    
    # Priority fee
    priority_fees = {
        BudgetTier.MICRO: 100_000,
        BudgetTier.SMALL: 250_000,
        BudgetTier.MEDIUM: 500_000,
        BudgetTier.LARGE: 1_000_000,
        BudgetTier.XLARGE: 1_500_000,
        BudgetTier.XXLARGE: 2_000_000,
    }
    
    # Jito bundles
    use_jito = tier.value in ("MEDIUM", "LARGE", "XLARGE", "XXLARGE")
    
    return BudgetConfig(
        budget_usd=budget_usd,
        sol_price=sol_price,
        budget_sol=round(budget_sol, 6),
        tier=tier,
        num_wallets=num_wallets,
        allocations=allocations,
        initial_buy_sol=round(initial_buy_sol, 6),
        sol_per_phase=[round(p, 6) for p in sol_per_phase],
        sell_thresholds=sell_thresholds,
        gas_budget_sol=round(gas_budget_sol, 6),
        max_transactions=max_transactions,
        use_jito_bundles=use_jito,
        priority_fee_micro_lamports=priority_fees[tier],
        creation_fee_sol=creation_fee,
        usable_sol=round(usable_sol, 6),
        graduation_mc_usd=graduation_mc_usd,
        launch_mc_usd=round(launch_mc_usd, 2),
        strategies=strategies,
    )


def get_recommended_config(budget_usd: float, sol_price: Optional[float] = None) -> BudgetConfig:
    """
    Get recommended configuration with optimal wallet count and allocation.
    
    This is the primary function users should call.
    """
    return get_budget_config(budget_usd, sol_price)


def print_config_summary(config: BudgetConfig) -> str:
    """Print a human-readable summary of a budget configuration."""
    lines = [
        f"={'='*58}",
        f" BUDGET CONFIGURATION",
        f"={'='*58}",
        f"Budget: ${config.budget_usd} ({config.budget_sol:.4f} SOL @ ${config.sol_price})",
        f"Tier: {config.tier.value}",
        f"",
        f"┌── Wallet Allocation ({config.num_wallets} wallets)",
        f"│  Trading Capital: {sum(a.sol_allocated for a in config.allocations):.4f} SOL",
        f"│  Gas Budget: {config.gas_budget_sol:.4f} SOL",
        f"│  Max Transactions: {config.max_transactions}",
        f"└─  Jito Bundles: {'Yes' if config.use_jito_bundles else 'No'}",
        f"",
        f"┌── Pump.fun Parameters",
        f"│  Creation Fee: {config.creation_fee_sol} SOL (rent only, no launch fee!)",
        f"│  Usable SOL: {config.usable_sol:.4f} SOL",
        f"│  Launch MC: ${config.launch_mc_usd:.2f} ({get_mc_at_current_price(PUMP_INITIAL_VIRTUAL_SOL_RESERVES):.1f} SOL)",
        f"│  Graduation MC: ${config.graduation_mc_usd:.2f} ({PUMP_GRADUATION_MC_SOL:.1f} SOL)",
        f"└─  Current Price: {PUMP_LAUNCH_PRICE_SOL:.8f} SOL/token (${PUMP_LAUNCH_PRICE_SOL * config.sol_price:.6f}/token)",
        f"",
        f"┌── Buy Phases ({len(config.sol_per_phase)} phases)",
    ]
    
    for i, phase in enumerate(config.sol_per_phase):
        lines.append(f"│  Phase {i+1}: {phase:.4f} SOL (${phase * config.sol_price:.2f})")
    
    lines.append(f"└─  Initial Buy: {config.initial_buy_sol:.4f} SOL (${config.initial_buy_sol * config.sol_price:.2f})")
    lines.append(f"")
    lines.append(f"┌── Sell Thresholds")
    
    for mc_mult, sell_pct in sorted(config.sell_thresholds.items()):
        lines.append(f"│  {mc_mult}x MC: sell {sell_pct*100:.0f}%")
    
    lines.append(f"└─  Buyback threshold: 0.85x (emergency defense)")
    lines.append(f"")
    lines.append(f"┌── Wallet Allocation Details")
    
    for alloc in config.allocations:
        lines.append(f"│  {alloc.role.title():12s} x{alloc.count} | {alloc.sol_allocated:.4f} SOL | ${alloc.usd_allocated:.2f} | {alloc.percentage*100:.0f}%")
    
    lines.append(f"└─  Total: {sum(a.sol_allocated for a in config.allocations):.4f} SOL")
    lines.append(f"")
    lines.append(f"Strategies: {', '.join(config.strategies)}")
    lines.append(f"{'='*58}")
    
    return "\n".join(lines)


def calculate_trade_impact(sol_amount: float, current_virtual_sol: float) -> Dict[str, float]:
    """
    Calculate the impact of a trade on the bonding curve.
    
    Returns dict with:
    - mc_increase_pct: % increase in market cap
    - price_increase_pct: % increase in token price
    - new_mc_sol: new market cap in SOL
    - new_virtual_sol: new virtual SOL reserves
    - fee_sol: fee paid (at current MC tier)
    """
    k = PUMP_INITIAL_VIRTUAL_SOL_RESERVES * PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES
    old_mc = get_mc_at_current_price(current_virtual_sol)
    
    new_virtual_sol = current_virtual_sol + sol_amount
    new_mc = get_mc_at_current_price(new_virtual_sol)
    
    mc_increase = (new_mc - old_mc) / old_mc if old_mc > 0 else 0
    
    # Token price calculation
    old_price = current_virtual_sol / (k / current_virtual_sol)
    new_price = new_virtual_sol / (k / new_virtual_sol)
    price_increase = (new_price - old_price) / old_price if old_price > 0 else 0
    
    # Fee calculation
    old_fee_bps = get_fee_bps_for_mc(old_mc)
    fee_sol = sol_amount * old_fee_bps[2] / 10000
    
    return {
        "mc_increase_pct": round(mc_increase * 100, 2),
        "price_increase_pct": round(price_increase * 100, 2),
        "new_mc_sol": round(new_mc, 2),
        "new_mc_usd": round(new_mc * get_sol_price_usd(), 2),
        "new_virtual_sol": round(new_virtual_sol, 4),
        "fee_sol": round(fee_sol, 8),
        "fee_usd": round(fee_sol * get_sol_price_usd(), 6),
    }


def calculate_sell_impact(token_amount: float, current_virtual_sol: float) -> Dict[str, float]:
    """
    Calculate the impact of selling tokens on the bonding curve.
    """
    k = PUMP_INITIAL_VIRTUAL_SOL_RESERVES * PUMP_INITIAL_VIRTUAL_TOKEN_RESERVES
    virtual_token_reserves = k / current_virtual_sol
    
    old_mc = get_mc_at_current_price(current_virtual_sol)
    
    # Sell: tokens come in, virtual SOL goes out
    new_virtual_token = virtual_token_reserves + token_amount
    new_virtual_sol = k / new_virtual_token
    new_mc = get_mc_at_current_price(new_virtual_sol)
    
    mc_decrease = (old_mc - new_mc) / old_mc if old_mc > 0 else 0
    
    # SOL received
    sol_received = current_virtual_sol - new_virtual_sol
    
    # Fee
    fee_bps = get_fee_bps_for_mc(old_mc)
    fee_sol = sol_received * fee_bps[2] / 10000
    
    return {
        "mc_decrease_pct": round(mc_decrease * 100, 2),
        "sol_received": round(sol_received, 8),
        "sol_received_usd": round(sol_received * get_sol_price_usd(), 4),
        "new_mc_sol": round(new_mc, 2),
        "new_mc_usd": round(new_mc * get_sol_price_usd(), 2),
        "fee_sol": round(fee_sol, 8),
        "fee_usd": round(fee_sol * get_sol_price_usd(), 6),
        "slippage_pct": round(abs((sol_received / token_amount / (current_virtual_sol / virtual_token_reserves) - 1)) * 100, 2) if token_amount > 0 else 0,
    }


# ─── Convenience Functions ───

def allocate_wallets(config: BudgetConfig) -> List[Tuple[str, int, float, float]]:
    """
    Generate wallet allocation list from config.
    
    Returns: [(role, count, sol_per_wallet, total_sol), ...]
    """
    result = []
    for alloc in config.allocations:
        sol_per_wallet = alloc.sol_allocated / alloc.count if alloc.count > 0 else 0
        result.append((alloc.role, alloc.count, sol_per_wallet, alloc.sol_allocated))
    return result


# ─── Tests ───

def test_micro_budget():
    """Test $5 budget configuration."""
    print("\n[TEST] $5 Micro Budget")
    config = get_budget_config(5)
    print(print_config_summary(config))
    assert config.num_wallets >= 3, f"Expected >=3 wallets, got {config.num_wallets}"
    assert config.budget_sol <= 0.1, f"Expected <=0.1 SOL, got {config.budget_sol}"
    assert config.creation_fee_sol == 0.002, "Creation fee should be 0.002 SOL"
    assert config.use_jito_bundles == False, "Micro should not use Jito"
    assert len(config.sol_per_phase) == 2, "Micro should have 2 phases"
    print("  PASS")
    return True


def test_small_budget():
    """Test $10 budget configuration."""
    print("\n[TEST] $10 Small Budget")
    config = get_budget_config(10)
    print(print_config_summary(config))
    assert config.num_wallets >= 5, f"Expected >=5 wallets, got {config.num_wallets}"
    assert config.budget_sol <= 0.2, f"Expected <=0.2 SOL, got {config.budget_sol}"
    print("  PASS")
    return True


def test_large_budget():
    """Test $20 budget configuration."""
    print("\n[TEST] $20 Large Budget")
    config = get_budget_config(20)
    print(print_config_summary(config))
    assert config.num_wallets >= 8, f"Expected >=8 wallets, got {config.num_wallets}"
    assert config.budget_sol <= 0.5, f"Expected <=0.5 SOL, got {config.budget_sol}"
    assert config.use_jito_bundles == True, "Large should use Jito"
    assert len(config.sol_per_phase) >= 3, "Large should have >=3 phases"
    print("  PASS")
    return True


def test_xxlarge_budget():
    """Test $100 budget configuration."""
    print("\n[TEST] $100 XXLARGE Budget")
    config = get_budget_config(100)
    print(print_config_summary(config))
    assert config.num_wallets >= 15, f"Expected >=15 wallets, got {config.num_wallets}"
    assert config.budget_sol >= 1.0, f"Expected >=1.0 SOL, got {config.budget_sol}"
    assert "whale_dance" in config.strategies
    assert "bubble_arbitrage" in config.strategies
    print("  PASS")
    return True


def test_trade_impact():
    """Test trade impact calculations."""
    print("\n[TEST] Trade Impact Calculation")
    result = calculate_trade_impact(0.01, PUMP_INITIAL_VIRTUAL_SOL_RESERVES)
    print(f"  0.01 SOL buy at launch MC:")
    print(f"  MC increase: {result['mc_increase_pct']:.2f}%")
    print(f"  New MC: {result['new_mc_usd']:.2f} USD")
    print(f"  Fee: {result['fee_usd']:.4f} USD")
    
    assert result["mc_increase_pct"] > 0
    assert result["fee_sol"] > 0
    print("  PASS")
    return True


def test_sell_impact():
    """Test sell impact calculations."""
    print("\n[TEST] Sell Impact Calculation")
    result = calculate_sell_impact(10000000, PUMP_INITIAL_VIRTUAL_SOL_RESERVES)
    print(f"  10M token sell at launch:")
    print(f"  MC decrease: {result['mc_decrease_pct']:.2f}%")
    print(f"  SOL received: ${result['sol_received_usd']:.4f}")
    print(f"  Fee: {result['fee_usd']:.4f} USD")
    
    assert result["mc_decrease_pct"] >= 0
    print("  PASS")
    return True


def test_dynamic_fees():
    """Test fee tiers based on market cap."""
    print("\n[TEST] Dynamic Fee Tiers")
    
    # Below 100 SOL MC
    bbs, cs, total = get_fee_bps_for_mc(50)
    print(f"  MC < 100 SOL: protocol={bbs}bps, creator={cs}bps, total={total}bps")
    assert total == 300, f"Expected 300 bps, got {total}"
    
    # 100-1000 SOL MC
    bbs, cs, total = get_fee_bps_for_mc(500)
    print(f"  MC 100-1000 SOL: protocol={bbs}bps, creator={cs}bps, total={total}bps")
    assert total == 150, f"Expected 150 bps, got {total}"
    
    # Above 1000 SOL MC
    bbs, cs, total = get_fee_bps_for_mc(2000)
    print(f"  MC > 1000 SOL: protocol={bbs}bps, creator={cs}bps, total={total}bps")
    assert total == 75, f"Expected 75 bps, got {total}"
    
    print("  PASS")
    return True


def test_graduation():
    """Test graduation calculations."""
    print("\n[TEST] Graduation Calculations")
    print(f"  Launch MC: {get_mc_at_current_price(PUMP_INITIAL_VIRTUAL_SOL_RESERVES):.1f} SOL")
    print(f"  Graduation MC: {PUMP_GRADUATION_MC_SOL:.1f} SOL")
    print(f"  Progress at 50 SOL virtual: {get_mc_progress_percent(50):.1f}%")
    
    assert PUMP_GRADUATION_VIRTUAL_SOL > PUMP_INITIAL_VIRTUAL_SOL_RESERVES
    assert get_mc_progress_percent(PUMP_INITIAL_VIRTUAL_SOL_RESERVES) == 0.0
    assert get_mc_progress_percent(PUMP_GRADUATION_VIRTUAL_SOL) == 100.0
    print("  PASS")
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("BUDGET CONFIG MODULE TESTS")
    print("=" * 60)
    
    tests = [
        test_micro_budget,
        test_small_budget,
        test_large_budget,
        test_xxlarge_budget,
        test_trade_impact,
        test_sell_impact,
        test_dynamic_fees,
        test_graduation,
    ]
    
    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed}/{len(tests)} tests passed")
    print(f"{'=' * 60}")
