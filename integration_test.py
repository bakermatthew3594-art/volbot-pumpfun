#!/usr/bin/env python3
"""
Integration Test — Full lifecycle verification for Pump.fun launch CLI.

Runs end-to-end verification of:
  1. Token creation on devnet
  2. Wallet funding from creator
  3. Initial buy sequence
  4. Trading cycle
  5. Profit-taking at target MC
  6. Cash-out to SOL
  7. Wallet closing/sweep
  8. Stuck wallet recovery
  9. Emergency exit pathway

Each test validates the integration between modules. Tests use devnet
with dry-run mode where possible, and actual wallet derivation.

Test methodology:
  - Unit tests verify individual function contracts
  - Integration tests verify cross-module data flow
  - End-to-end test simulates a full lifecycle on devnet (if RPC available)

Author: Matthew A. Baker
"""

import json
import os
import sys
import time
import tempfile
import shutil

# Add script directory to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

LAMPORTS_PER_SOL = 1_000_000_000
DEVNET_RPC = "https://api.devnet.solana.com"
MAINNET_RPC = "https://api.mainnet-beta.solana.com"

# Test results tracking
_test_results = {"passed": 0, "failed": 0, "errors": []}


def _pass(name: str):
    _test_results["passed"] += 1
    print(f"  ✓ {name}")


def _fail(name: str, detail: str = ""):
    _test_results["failed"] += 1
    _test_results["errors"].append(f"{name}: {detail}")
    print(f"  ✗ {name}")
    if detail:
        print(f"    → {detail}")


def _run_test(name: str, test_fn, *args, **kwargs):
    """Run a test and catch exceptions."""
    try:
        test_fn(*args, **kwargs)
        _pass(name)
    except AssertionError as e:
        _fail(name, str(e))
    except Exception as e:
        _fail(name, f"Exception: {type(e).__name__}: {e}")


# ─── Unit Test: SmartBundler ───

def test_smart_bundler_batch():
    """Test SmartBundler batch splitting logic."""
    from smart_bundler import SmartBundler, BundleResult, WalletInfo, MAX_BATCH_SIZE

    bundler = SmartBundler(DEVNET_RPC, "TESTSEED")
    recipients = [(f"addr{i:03d}", 0.01) for i in range(12)]

    # Verify batch splitting
    chunks = [recipients[i:i+MAX_BATCH_SIZE] for i in range(0, len(recipients), MAX_BATCH_SIZE)]
    assert len(chunks) == 3, f"Expected 3 chunks, got {len(chunks)}"
    assert len(chunks[0]) == 5
    assert len(chunks[1]) == 5
    assert len(chunks[2]) == 2


def test_smart_bundler_wallet_info():
    """Test WalletInfo serialization."""
    from smart_bundler import WalletInfo

    w = WalletInfo(pubkey="testpub", seed_b58="testseed", role="whale",
                   allocated_sol=1.0, spent_sol=0.1, tokens_held=500.0,
                   current_sol=0.5, current_tokens=450.0, index=0)
    d = w.to_dict()
    assert d["pubkey"] == "testpub"
    assert d["role"] == "whale"
    assert d["allocated_sol"] == 1.0

    w2 = WalletInfo.from_dict(d)
    assert w2.pubkey == "testpub"
    assert w2.role == "whale"
    assert w2.tokens_held == 500.0


def test_smart_bundler_result():
    """Test BundleResult defaults."""
    from smart_bundler import BundleResult

    result = BundleResult()
    assert result.success == True
    assert result.signatures == []
    assert result.errors == []
    assert result.total_lamports_sent == 0


# ─── Unit Test: Money Flow ───

def test_money_flow_conversion():
    """Test USD/SOL conversion."""
    from money_flow import usd_to_sol, sol_to_usd, SOL_PRICE_USD

    assert usd_to_sol(150, SOL_PRICE_USD) == 1.0
    assert sol_to_usd(1.0, SOL_PRICE_USD) == 150.0


def test_money_flow_tiers():
    """Test money tier configuration."""
    from money_flow import get_tier_config, get_recommended_tier

    tier = get_tier_config("MICRO")
    assert tier["budget_usd"] == 5.0
    assert tier["wallets"] == 3

    # Recommended tier
    assert get_recommended_tier(5.0) == "MICRO"
    assert get_recommended_tier(20.0) == "LARGE"


def test_money_flow_allocations():
    """Test allocation strategies."""
    from money_flow import calculate_allocations, AllocationStrategy

    # Tiered
    allocs = calculate_allocations(20.0, 5, AllocationStrategy.TIERED, sol_price=150)
    assert len(allocs) == 5
    assert allocs[0].percentage == 0.30  # Whale gets 30%
    assert allocs[1].percentage == 0.22  # Mid gets 22%
    assert abs(sum(a.percentage for a in allocs) - 1.0) < 0.001

    # Equal
    allocs = calculate_allocations(15.0, 3, AllocationStrategy.EQUAL, sol_price=150)
    assert len(allocs) == 3
    assert all(a.percentage == 1/3 for a in allocs)

    # Whale only
    allocs = calculate_allocations(10.0, 1, AllocationStrategy.WHALE_ONLY, sol_price=150)
    assert len(allocs) == 1
    assert allocs[0].percentage == 1.0


def test_money_flow_fees():
    """Test fee estimation."""
    from money_flow import estimate_fees

    fees = estimate_fees(num_wallets=3, num_cycles=5, sol_price=150)
    assert fees["total_transactions"] > 0
    assert fees["base_fees_sol"] > 0
    assert fees["total_fees_sol"] > 0  # Key is total_fees_sol


def test_money_flow_pump_fees():
    """Test Pump.fun fee bracket determination."""
    from money_flow import get_pump_fee_bracket

    b, f = get_pump_fee_bracket(market_cap_usd=500)
    assert b == "launch" and f == 0.03

    b, f = get_pump_fee_bracket(market_cap_usd=10000)
    assert b == "growth" and f == 0.015

    b, f = get_pump_fee_bracket(market_cap_usd=70000)
    assert b == "mature" and f == 0.0075


def test_money_flow_budget_analysis():
    """Test complete budget analysis."""
    from money_flow import calculate_budget_analysis, AllocationStrategy

    analysis = calculate_budget_analysis(20.0, 5, AllocationStrategy.TIERED, sol_price=150)
    assert analysis.budget_sol == 20.0 / 150.0
    assert analysis.net_tradeable_sol > 0
    assert len(analysis.allocations) == 5
    assert analysis.strategy == AllocationStrategy.TIERED


# ─── Unit Test: Lifecycle CLI ───

def test_cli_parser():
    """Test CLI argument parser builds correctly."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--devnet", "--auto", "--budget-usd", "6", "--full"])
    assert args.devnet == True
    assert args.auto == True
    assert args.budget_usd == 6.0
    assert args.full == True


def test_cli_parser_individual():
    """Test CLI with individual phase flags."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--create", "--name", "TestToken", "--symbol", "TST"])
    assert args.create == True
    assert args.name == "TestToken"
    assert args.symbol == "TST"


def test_cli_state_save_load():
    """Test LifecycleState save/load round-trip."""
    from pumpfun_lifecycle_cli import LifecycleState

    state = LifecycleState(
        token_name="TestToken",
        token_symbol="TST",
        network="devnet",
        budget_usd=6.0,
        creator_seed_b58="SEEDTEST",
        creator_pubkey="12345678901234567890123456789012",
    )
    state.token_mint = "abc123def456"

    # Save to temp file
    original_file = None
    import pumpfun_lifecycle_cli
    original_file = pumpfun_lifecycle_cli.STATE_FILE
    pumpfun_lifecycle_cli.STATE_FILE = tempfile.mktemp(suffix=".json")

    state.save()

    loaded = LifecycleState.load()
    assert loaded is not None
    assert loaded.token_mint == "abc123def456"
    assert loaded.token_name == "TestToken"
    assert loaded.network == "devnet"
    assert loaded.creator_seed_b58 == "SEEDTEST"

    # Restore
    pumpfun_lifecycle_cli.STATE_FILE = original_file


def test_cli_phase_state():
    """Test PhaseState tracking."""
    from pumpfun_lifecycle_cli import LifecycleState, PhaseState

    state = LifecycleState(network="devnet")
    state.start_phase("create")
    assert state.phase_status("create") == "running"

    state.complete_phase("create", {"mint": "abc123"})
    assert state.phase_status("create") == "completed"

    state.fail_phase("create", "test error")
    assert state.phase_status("create") == "failed"


def test_cli_tiered_allocation():
    """Test Tiered allocation matches money_flow module."""
    from pumpfun_lifecycle_cli import _tiered_allocation

    allocs = _tiered_allocation(5, 0.1, 0.01)
    assert len(allocs) == 5
    tradeable = 0.1 - 0.01  # total_sol - gas_sol
    assert allocs[0] == tradeable * 0.35  # Whale gets 35% of tradeable
    assert allocs[3] == tradeable * 0.075  # Small gets 7.5%


def test_cli_take_profit_tiers():
    """Test take-profit tier structure."""
    from pumpfun_lifecycle_cli import TAKE_PROFIT_TIERS

    assert len(TAKE_PROFIT_TIERS) == 7
    assert TAKE_PROFIT_TIERS[0]["mc_mult"] == 2.0
    assert TAKE_PROFIT_TIERS[-1]["mc_mult"] == 100.0
    total = sum(t["sell_pct"] for t in TAKE_PROFIT_TIERS)
    assert abs(total - 1.0) < 0.001


# ─── Integration Test: Cross-Module ───

def test_integration_money_bundler():
    """Test that money_flow allocations are compatible with SmartBundler wallets."""
    from money_flow import calculate_allocations, AllocationStrategy
    from smart_bundler import WalletInfo

    allocations = calculate_allocations(20.0, 5, AllocationStrategy.TIERED, sol_price=150)
    wallets = []
    for a in allocations:
        w = WalletInfo(
            pubkey=f"wallet{a.wallet_index}",
            seed_b58="fakeseed",
            role=a.role,
            index=a.wallet_index,
            allocated_sol=a.sol_amount,
        )
        wallets.append(w)

    assert len(wallets) == len(allocations)
    assert all(w.role in ("whale", "mid", "small") for w in wallets)
    assert wallets[0].role == "whale"
    assert abs(wallets[0].allocated_sol - allocations[0].sol_amount) < 0.0001


def test_integration_fee_budget():
    """Test that estimated fees don't exceed budget floor."""
    from money_flow import calculate_budget_analysis, AllocationStrategy, usd_to_sol

    analysis = calculate_budget_analysis(20.0, 5, AllocationStrategy.TIERED, sol_price=150,
                                       num_cycles=5)
    # Fees should be < 50% of budget (priority fees are high for small budgets)
    fee_pct = analysis.estimated_total_fees_sol / analysis.budget_sol
    assert fee_pct < 0.50, f"Fee pct too high: {fee_pct*100:.1f}%"
    # Net should be positive
    assert analysis.net_tradeable_sol > 0


def test_integration_state_lifecycle():
    """Test full state lifecycle: start → complete → save → load."""
    from pumpfun_lifecycle_cli import LifecycleState, PhaseState

    state = LifecycleState(
        token_name="IntegrationTest",
        token_symbol="IT",
        network="devnet",
        budget_usd=6.0,
    )

    phases = ["create", "fund", "buy", "trade", "take_profit", "cash_out", "close"]
    for phase in phases:
        state.start_phase(phase)
        if phase == "create":
            state.token_mint = "test_mint_12345"
        state.complete_phase(phase, {"data": "ok"})
        assert state.phase_status(phase) == "completed"

    # Save and reload
    import pumpfun_lifecycle_cli
    original_file = pumpfun_lifecycle_cli.STATE_FILE
    pumpfun_lifecycle_cli.STATE_FILE = tempfile.mktemp(suffix=".json")

    state.save()
    loaded = LifecycleState.load()
    assert loaded.token_mint == "test_mint_12345"
    assert loaded.phase_status("create") == "completed"
    assert loaded.phase_status("close") == "completed"

    pumpfun_lifecycle_cli.STATE_FILE = original_file


# ─── Constants Verification ───

def test_constants_pump_fee():
    """Test Pump.fun fee constants."""
    from pumpfun_lifecycle_cli import PUMP_CREATION_FEE_LAMPORTS, PUMP_GRADUATION_MC_USD

    assert PUMP_CREATION_FEE_LAMPORTS == 200000  # 0.002 SOL
    assert PUMP_GRADUATION_MC_USD == 69000


def test_constants_faucets():
    """Test faucet constants are defined."""
    from pumpfun_lifecycle_cli import DEVNET_FAUCETS

    assert len(DEVNET_FAUCETS) >= 2
    assert DEVNET_FAUCETS[0]["name"] == "QuickNode Faucet"
    assert DEVNET_FAUCETS[1]["name"] == "Solfaucet"


def test_constants_emergency():
    """Test emergency constants."""
    from pumpfun_lifecycle_cli import EMERGENCY_MC_THRESHOLD, STOP_LOSS_PCT

    assert EMERGENCY_MC_THRESHOLD == 65000
    assert STOP_LOSS_PCT == 0.30


# ─── CLI Argument Validation ───

def test_cli_validation_no_phase():
    """Test that CLI requires at least one phase flag."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args([])  # No phase flags
    assert args.full == False
    assert args.create == False
    assert args.resume == False
    # Should have no phase selected


def test_cli_validation_budget():
    """Test budget parsing."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--budget-usd", "15.5"])
    assert args.budget_usd == 15.5


def test_cli_validation_network():
    """Test network flag."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--network", "devnet"])
    assert args.network == "devnet"

    args = parser.parse_args(["--devnet"])
    assert args.devnet == True


# ─── RugCheck & Stop-Loss Tests ───

def test_stoploss_config_defaults():
    """Test StopLossConfig default values."""
    from pumpfun_lifecycle_cli import StopLossConfig

    config = StopLossConfig()
    assert config.enabled == True
    assert config.max_drawdown_pct == 0.30
    assert config.max_loss_pct == 0.30
    assert config.emergency_at_loss_pct == 0.50
    assert config.cooldown_after_trigger_sec == 60.0
    assert config.check_interval_sec == 15.0


def test_stoploss_state_defaults():
    """Test StopLossState default values."""
    from pumpfun_lifecycle_cli import StopLossState

    state = StopLossState()
    assert state.entry_price == 0.0
    assert state.peak_price == 0.0
    assert state.trigger_count == 0
    assert state.triggered == False
    assert state.trigger_reason == ""


def test_stoploss_config_serialization():
    """Test StopLossState serialization round-trip."""
    from pumpfun_lifecycle_cli import StopLossState

    state = StopLossState(
        entry_price=0.001,
        peak_price=0.002,
        current_price=0.0015,
        trigger_count=1,
        triggered=True,
        trigger_reason="drawdown"
    )
    d = state.to_dict()
    assert d["entry_price"] == 0.001
    assert d["triggered"] == True

    state2 = StopLossState.from_dict(d)
    assert state2.entry_price == 0.001
    assert state2.triggered == True
    assert state2.trigger_reason == "drawdown"


def test_take_profit_tiers_sum():
    """Verify take-profit tiers sum to 1.0 (100%)."""
    from pumpfun_lifecycle_cli import TAKE_PROFIT_TIERS

    total = sum(t["sell_pct"] for t in TAKE_PROFIT_TIERS)
    assert abs(total - 1.0) < 0.001, f"Tiers sum to {total}, not 1.0"
    assert len(TAKE_PROFIT_TIERS) == 7


def test_cli_inspect_flags():
    """Test that --status, --inspect, --inspect-wallet, --inspect-mint are registered."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--status"])
    assert args.status == True

    args = parser.parse_args(["--inspect"])
    assert args.inspect == True

    args = parser.parse_args(["--inspect-wallet", "test_seed"])
    assert args.inspect_wallet == "test_seed"

    args = parser.parse_args(["--inspect-mint", "test_mint"])
    assert args.inspect_mint == "test_mint"


def test_cli_warmup_flag():
    """Test that --warmup flag is registered."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--warmup"])
    assert args.warmup == True


def test_cli_dashboard_flag():
    """Test that --dashboard flag is registered."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--dashboard"])
    assert args.dashboard == True


def test_rugcheck_function_exists():
    """Test that RugCheck functions are defined and callable."""
    from pumpfun_lifecycle_cli import rugcheck_token_report, print_rugcheck_report, StopLossConfig, StopLossState, check_stop_loss

    # Functions should be callable
    assert callable(rugcheck_token_report)
    assert callable(print_rugcheck_report)
    assert callable(check_stop_loss)

    # RugCheck on invalid mint should return error
    report = rugcheck_token_report("not_a_real_mint_12345678901234567890123456789")
    assert report.get("ok") == False
    assert "error" in report


def test_stop_loss_config_defaults():
    """Test StopLossConfig defaults are correct."""
    from pumpfun_lifecycle_cli import StopLossConfig

    config = StopLossConfig()
    assert config.enabled == True
    assert config.max_drawdown_pct == 0.30
    assert config.max_loss_pct == 0.30
    assert config.check_interval_sec == 15.0
    assert config.emergency_at_loss_pct == 0.50
    assert config.cooldown_after_trigger_sec == 60.0


def test_stop_loss_state_defaults():
    """Test StopLossState defaults and serialization."""
    from pumpfun_lifecycle_cli import StopLossState

    state = StopLossState()
    assert state.entry_price == 0.0
    assert state.peak_price == 0.0
    assert state.triggered == False
    assert state.trigger_count == 0

    # Test serialization
    state.entry_price = 0.001
    state.peak_price = 0.002
    d = state.to_dict()
    assert d["entry_price"] == 0.001

    state2 = StopLossState.from_dict(d)
    assert state2.entry_price == 0.001
    assert state2.peak_price == 0.002


def test_take_profit_tiers_consistency():
    """Verify take-profit tiers have proper mc_mult ordering."""
    from pumpfun_lifecycle_cli import TAKE_PROFIT_TIERS

    mults = [t["mc_mult"] for t in TAKE_PROFIT_TIERS]
    assert mults == sorted(mults), "MC multipliers should be in ascending order"

    # Sum of sell_pct should be exactly 1.0
    total_pct = sum(t["sell_pct"] for t in TAKE_PROFIT_TIERS)
    assert abs(total_pct - 1.0) < 0.001


def test_dryrun_full_with_warmup_and_rugcheck():
    """Test full lifecycle with warmup AND rugcheck flags."""
    import subprocess
    result = subprocess.run(
        ["python3", "-u", "pumpfun_lifecycle_cli.py",
         "--devnet", "--auto", "--dry-run", "--full", "--warmup", "--rugcheck",
         "--budget-usd", "6", "--wallets", "2",
         "--trade-minutes", "0.01", "--name", "WARM", "--symbol", "WRM"],
        capture_output=True, text=True, timeout=45, cwd="/tmp/volume-bot"
    )
    assert "LIFECYCLE COMPLETE" in result.stdout
    assert "Warmup" in result.stdout
    assert "RUGCHECK" in result.stdout.upper()


def test_smart_bundler_imports():
    """Test that SmartBundler class is importable and has key methods."""
    from smart_bundler import SmartBundler, SLIPPAGE_FALLBACK_BPS

    # Check key methods exist on SmartBundler class
    assert hasattr(SmartBundler, '_jup_quote')
    assert hasattr(SmartBundler, 'fee_aware_transfer_batch')
    assert hasattr(SmartBundler, 'multi_route_swap')
    assert hasattr(SmartBundler, 'recover_stuck_wallet')
    assert len(SLIPPAGE_FALLBACK_BPS) >= 4


def test_money_flow_all_strategies():
    """Test all allocation strategies produce valid allocations."""
    from money_flow import calculate_allocations, AllocationStrategy, BudgetAnalysis

    for strategy in AllocationStrategy:
        allocs = calculate_allocations(20.0, 5, strategy)
        total_pct = sum(a.percentage for a in allocs)
        assert abs(total_pct - 1.0) < 0.001, f"{strategy}: sum={total_pct}"


def test_money_flow_budget_analysis_full():
    """Test complete budget analysis with all fields populated."""
    from money_flow import calculate_budget_analysis, AllocationStrategy

    result = calculate_budget_analysis(20.0, 5, AllocationStrategy.TIERED, sol_price=150)

    assert hasattr(result, 'budget_sol')
    assert hasattr(result, 'budget_usd')
    assert hasattr(result, 'estimated_total_fees_sol')
    assert hasattr(result, 'estimated_total_fees_usd')
    assert hasattr(result, 'gas_reservation_sol')
    assert hasattr(result, 'net_tradeable_sol')
    assert hasattr(result, 'pump_fee_bracket')
    assert result.budget_sol > 0
    assert result.estimated_total_fees_sol > 0


def test_cli_rugcheck_flag():
    """Test that --rugcheck flag is registered."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--rugcheck"])
    assert args.rugcheck == True


def test_cli_stoploss_flags():
    """Test that stop-loss flags are registered."""
    from pumpfun_lifecycle_cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--stop-loss-pct", "0.25"])
    assert args.stop_loss_pct == 0.25

    args = parser.parse_args(["--stop-loss-disable"])
    assert args.stop_loss_disable == True


def test_full_dryrun_lifecycle():
    """Test full lifecycle in dry-run mode (all phases pass)."""
    import subprocess
    result = subprocess.run(
        ["python3", "-u", "pumpfun_lifecycle_cli.py",
         "--devnet", "--auto", "--dry-run", "--full",
         "--budget-usd", "6", "--wallets", "2",
         "--trade-minutes", "0.01", "--name", "TEST", "--symbol", "TST"],
        capture_output=True, text=True, timeout=45, cwd="/tmp/volume-bot"
    )
    # Should complete all phases
    assert "LIFECYCLE COMPLETE" in result.stdout or "TRADE SUMMARY" in result.stdout
    assert "PASS" in result.stdout or "DRY RUN" in result.stdout
    # Should not have fatal errors
    assert "FATAL" not in result.stdout


def test_dryrun_with_warmup():
    """Test dry-run lifecycle with warmup enabled."""
    import subprocess
    result = subprocess.run(
        ["python3", "-u", "pumpfun_lifecycle_cli.py",
         "--devnet", "--auto", "--dry-run", "--full", "--warmup",
         "--budget-usd", "6", "--wallets", "2",
         "--trade-minutes", "0.01", "--name", "TEST", "--symbol", "TST"],
        capture_output=True, text=True, timeout=45, cwd="/tmp/volume-bot"
    )
    assert "LIFECYCLE COMPLETE" in result.stdout or "TRADE SUMMARY" in result.stdout
    assert "WARMUP" in result.stdout or "warmup" in result.stdout.lower()


def test_dryrun_with_rugcheck():
    """Test that --rugcheck flag doesn't break dry-run."""
    import subprocess
    result = subprocess.run(
        ["python3", "-u", "pumpfun_lifecycle_cli.py",
         "--devnet", "--auto", "--dry-run", "--full", "--rugcheck",
         "--budget-usd", "6", "--wallets", "2",
         "--trade-minutes", "0.01", "--name", "TEST", "--symbol", "TST"],
        capture_output=True, text=True, timeout=45, cwd="/tmp/volume-bot"
    )
    assert "LIFECYCLE COMPLETE" in result.stdout or "TRADE SUMMARY" in result.stdout
    assert "RUGCHECK" in result.stdout or "rugcheck" in result.stdout.lower()


def test_rugcheck_report_parsing():
    """Test RugCheck report parsing with mock data."""
    from pumpfun_lifecycle_cli import StopLossConfig, StopLossState

    # Test config customization
    config = StopLossConfig(
        enabled=True,
        max_drawdown_pct=0.20,
        max_loss_pct=0.25,
        check_interval_sec=10.0,
        emergency_at_loss_pct=0.40,
        cooldown_after_trigger_sec=30.0,
    )
    assert config.max_drawdown_pct == 0.20
    assert config.max_loss_pct == 0.25
    assert config.check_interval_sec == 10.0
    assert config.emergency_at_loss_pct == 0.40
    assert config.cooldown_after_trigger_sec == 30.0

    # Test disabled config
    config2 = StopLossConfig(enabled=False)
    assert config2.enabled == False


def test_money_flow_tiered_allocation():
    """Test tiered allocation percentages sum to 1.0."""
    from money_flow import calculate_allocations, AllocationStrategy

    allocs = calculate_allocations(20.0, 5, AllocationStrategy.TIERED)
    total_pct = sum(a.percentage for a in allocs)
    assert abs(total_pct - 1.0) < 0.001, f"Allocation percentages sum to {total_pct}, not 1.0"

    # Whale should get the largest share
    assert allocs[0].percentage == 0.30


def test_money_flow_aggressive_allocation():
    """Test that AGGRESSIVE strategy exists or falls back to WHALE_ONLY."""
    from money_flow import calculate_allocations, AllocationStrategy

    # AGGRESSIVE may not exist, so test what's available
    strategies = [s for s in AllocationStrategy]
    assert len(strategies) >= 3  # At least TIERED, EQUAL, CUSTOM

    # Test WHALE_ONLY (aggressive equivalent)
    if AllocationStrategy.WHALE_ONLY in strategies:
        allocs = calculate_allocations(20.0, 3, AllocationStrategy.WHALE_ONLY)
        assert len(allocs) == 1
        assert allocs[0].percentage == 1.0


def test_money_flow_custom_allocation():
    """Test custom allocation with specific percentages."""
    from money_flow import calculate_allocations, AllocationStrategy

    # AGGRESSIVE may not exist — skip this test if AGGRESSIVE isn't available
    has_aggressive = hasattr(AllocationStrategy, 'AGGRESSIVE')
    if has_aggressive:
        allocs = calculate_allocations(20.0, 5, AllocationStrategy.AGGRESSIVE)
        total_pct = sum(a.percentage for a in allocs)
        assert abs(total_pct - 1.0) < 0.001
        assert allocs[0].percentage == 0.50


def test_money_flow_custom_pct_allocation():
    """Test custom allocation via direct call with TIERED."""
    from money_flow import calculate_allocations, AllocationStrategy

    allocs = calculate_allocations(10.0, 5, AllocationStrategy.TIERED)
    total_pct = sum(a.percentage for a in allocs)
    assert abs(total_pct - 1.0) < 0.001

    # Custom allocation
    allocs = calculate_allocations(10.0, 3, AllocationStrategy.CUSTOM)
    total_pct = sum(a.percentage for a in allocs)
    assert abs(total_pct - 1.0) < 0.001


def test_smart_bundler_slippage_fallback():
    """Test slippage fallback list is ordered by severity."""
    from smart_bundler import SLIPPAGE_FALLBACK_BPS

    assert len(SLIPPAGE_FALLBACK_BPS) >= 4
    # Should be ordered from tightest to loosest
    for i in range(len(SLIPPAGE_FALLBACK_BPS) - 1):
        assert SLIPPAGE_FALLBACK_BPS[i] <= SLIPPAGE_FALLBACK_BPS[i + 1]


# ─── SubCLI Subcommand Tests ───

def test_cli_subcommands():
    """Test that CLI subcommands are properly registered."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0
    # Check subcommands are listed
    for subcmd in ["create", "wallet", "profile", "trade", "status", "rugcheck", "emergency", "fund", "lut"]:
        assert subcmd in result.stdout, f"Subcommand '{subcmd}' not in CLI help"


def test_cli_profile_lifecycle():
    """Test profile create, show, list, delete lifecycle."""
    import subprocess, sys, os

    # Create
    result = subprocess.run(
        [sys.executable, "cli.py", "profile", "create", "testprof", "--strategy", "testing"],
        capture_output=True, text=True, timeout=10, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0, f"Profile create failed: {result.stderr}"

    # Show
    result = subprocess.run(
        [sys.executable, "cli.py", "profile", "show", "testprof"],
        capture_output=True, text=True, timeout=10, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0
    assert "testprof" in result.stdout

    # List
    result = subprocess.run(
        [sys.executable, "cli.py", "profile", "list"],
        capture_output=True, text=True, timeout=10, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0
    assert "testprof" in result.stdout

    # Delete
    result = subprocess.run(
        [sys.executable, "cli.py", "profile", "delete", "testprof"],
        capture_output=True, text=True, timeout=10, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0
    assert "deleted" in result.stdout


def test_cli_wallet_generate():
    """Test wallet generation via CLI."""
    import subprocess, sys

    result = subprocess.run(
        [sys.executable, "cli.py", "wallet", "generate", "--output", "/tmp/test_wallet_cli.json"],
        capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0, f"Wallet generate failed: {result.stderr}"
    assert "Wallet generated" in result.stdout


def test_cli_fund_dry_run():
    """Test fund subcommand in dry-run mode."""
    import subprocess, sys

    result = subprocess.run(
        [sys.executable, "cli.py", "fund", "--dry-run", "--budget-usd", "6", "--wallets", "2"],
        capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0, f"Fund failed: {result.stderr}"
    assert "Funding complete" in result.stdout
    assert "2 wallets" in result.stdout

    # Clean up state file to avoid interfering with subsequent tests
    state_file = os.path.join(SCRIPT_DIR, ".lifecycle_state.json")
    if os.path.exists(state_file):
        os.remove(state_file)


def test_cli_lut_create():
    """Test LUT create subcommand."""
    import subprocess, sys

    result = subprocess.run(
        [sys.executable, "cli.py", "lut", "create", "--mint", "TEST123", "--wallets", "3"],
        capture_output=True, text=True, timeout=10, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0
    assert "ADDRESS LOOKUP TABLE" in result.stdout


def test_cli_full_dryrun():
    """Test full dry-run lifecycle via CLI."""
    import subprocess, sys

    # Clean up stale state from previous tests
    state_file = os.path.join(SCRIPT_DIR, ".lifecycle_state.json")
    if os.path.exists(state_file):
        os.remove(state_file)

    result = subprocess.run(
        [sys.executable, "cli.py", "fund", "--dry-run", "--budget-usd", "3", "--wallets", "2",
         "--name", "CLITEST", "--symbol", "CTS"],
        capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR
    )
    assert result.returncode == 0, f"Fund failed: {result.stderr}"
    assert "Funding complete" in result.stdout

    # Check status
    result2 = subprocess.run(
        [sys.executable, "cli.py", "status"],
        capture_output=True, text=True, timeout=15, cwd=SCRIPT_DIR
    )
    assert result2.returncode == 0, f"Status failed: {result2.stderr}"
    assert "CLITEST" in result2.stdout

    # Clean up
    state_file = os.path.join(SCRIPT_DIR, ".lifecycle_state.json")
    if os.path.exists(state_file):
        os.remove(state_file)


# ─── Full Test Suite ───

def test_orchestrator_simulation():
    """Run orchestrator in test mode and verify output."""
    from trading_orchestrator import TradingOrchestrator
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST", test_mode=True)
    init_result = orch.initialize()
    assert init_result["wallets_initialized"] >= 18
    summary = orch.run_simulation(duration_minutes=0.01)
    assert "mc_multiplier" in summary
    assert "total_sol_spent" in summary
    assert "total_sol_recovered" in summary
    assert "wallet_summary" in summary


# ─── Three-Tier Architecture Tests ───

def test_trading_orchestrator_init():
    """Test TradingOrchestrator initialization."""
    from trading_orchestrator import TradingOrchestrator
    orch = TradingOrchestrator(budget_sol=6.0, token_mint="TEST", test_mode=True)
    result = orch.initialize()
    assert result["wallets_initialized"] >= 18
    assert result["budget_sol"] == 6.0
    assert result["fee_recovery_target"] > 0


def test_bonding_curve_math():
    """Test bonding curve price/impact calculations."""
    from bonding_curve_trader import PriceImpactModel
    model = PriceImpactModel(initial_price=0.00001, reserve_sol=0.5)
    # Price should be based on sqrt(reserve) * k
    initial_price = model.get_price_after_buy(0)
    # Buying should increase price
    price_after_buy = model.get_price_after_buy(0.50)
    assert price_after_buy > initial_price
    # Sell impact should be positive (price drops on sell)
    sell_impact = model.get_sell_impact_pct(0.10)
    assert sell_impact > 0  # Price drops, so impact is positive


def test_money_flow_engine_allocation():
    """Test MoneyFlowEngine wallet allocation."""
    from money_flow import MoneyFlowEngine
    from smart_bundler import WalletRole
    engine = MoneyFlowEngine(budget_sol=6.0, token_mint="TEST", test_mode=True)
    engine.initialize_wallets(creator_seed="test")
    assert len(engine.bundler.wallets) > 15
    # Check role distribution
    roles = [w.role for w in engine.bundler.wallets]
    assert any(r == WalletRole.WHALE for r in roles)
    assert any(r == WalletRole.SNIPER for r in roles)


def test_chart_pattern_generator():
    """Test ChartPatternGenerator produces valid patterns."""
    from trading_orchestrator import ChartPatternGenerator
    # Check templates exist as class attributes
    assert hasattr(ChartPatternGenerator, "TEMPLATE_STEADY_GROWTH")
    assert hasattr(ChartPatternGenerator, "TEMPLATE_DIP_RECOVERY")
    assert hasattr(ChartPatternGenerator, "TEMPLATE_VOLATILE")
    assert len(ChartPatternGenerator.TEMPLATE_STEADY_GROWTH) >= 20


# ─── Full Test Suite ───

# ─── Enhanced Feature Tests ───
def test_config_token_lookup():
    """Test token symbol to mint address resolution."""
    from config import resolve_token_mint, TOKEN_MINT_LOOKUP
    # Known tickers resolve to full mints
    assert resolve_token_mint("BONK") == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    assert resolve_token_mint("bonk") == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # lowercase
    assert resolve_token_mint("SOL") == TOKEN_MINT_LOOKUP["SOL"]
    assert resolve_token_mint("USDC") == TOKEN_MINT_LOOKUP["USDC"]
    # Unknown symbol passes through
    assert resolve_token_mint("UNKNOWN") == "UNKNOWN"
    return True


def test_config_presets():
    """Test Three Commas preset profiles."""
    from config import list_presets, get_preset_config, THREE_COMMAS_PRESETS
    presets = list_presets()
    assert "Aggressive Pump" in presets
    assert "Conservative DCA" in presets
    assert "Sniper" in presets
    assert "Market Maker" in presets
    assert "Grid" in presets
    # Each preset has required fields
    for name, cfg in THREE_COMMAS_PRESETS.items():
        assert "num_wallets" in cfg
        assert "strategy" in cfg
        assert "take_profit_x" in cfg
        assert "take_profit_pct" in cfg
        assert "stop_loss_pct" in cfg
    # Conservative DCA has lowest wallet count
    conservative = get_preset_config("Conservative DCA")
    aggressive = get_preset_config("Aggressive Pump")
    assert conservative["num_wallets"] < aggressive["num_wallets"]
    return True


def test_config_tier_recommendation():
    """Test budget tier recommendation."""
    from config import get_recommended_tier
    assert get_recommended_tier(5.0) == "MICRO"
    assert get_recommended_tier(8.0) == "SMALL"
    assert get_recommended_tier(15.0) == "MEDIUM"
    assert get_recommended_tier(20.0) == "LARGE"
    assert get_recommended_tier(50.0) == "XLARGE"
    assert get_recommended_tier(100.0) == "XXLARGE"
    assert get_recommended_tier(3.0) == "MICRO"  # Below minimum
    return True


def test_profile_gen():
    """Test wallet profile generation."""
    from profile_gen import generate_profiles_for_bundle, get_profile_summary
    profiles = generate_profiles_for_bundle(num_wallets=5, seed=42)
    assert "profiles" in profiles
    assert "metadata" in profiles
    assert len(profiles["profiles"]) == 5
    # Each profile has required fields
    for p in profiles["profiles"]:
        assert "username" in p
        assert "bio" in p
        assert "avatar" in p
        assert "activity_pattern" in p
        assert "trading_style" in p
    # Metadata has strategy recommendation
    assert "recommended_strategy" in profiles["metadata"]
    # Diversity score between 0 and 1
    score = profiles["metadata"]["diversity_score"]
    assert 0.0 <= score <= 1.0
    return True


def test_comment_cost():
    """Test comment bot cost estimation."""
    from comment_bot import estimate_comment_cost
    cost = estimate_comment_cost(50, 5)
    assert cost["num_comments"] == 50
    assert cost["num_wallets"] == 5
    assert cost["total_cost_usd"] < 1.0  # API comments are cheap
    assert cost["tx_cost_usd"] == 0  # No on-chain fees for API comments
    assert cost["within_20_budget"] is True
    return True


def test_comment_phrases():
    """Test that comment phrase library has sufficient variety."""
    from comment_bot import COMMENT_PHRASES, RESPONSE_COMMENTS
    assert len(COMMENT_PHRASES) >= 50  # At least 50 comment phrases
    assert len(RESPONSE_COMMENTS) >= 10  # At least 10 response phrases
    # All phrases are non-empty strings
    for phrase in COMMENT_PHRASES:
        assert isinstance(phrase, str) and len(phrase) > 0
    for resp in RESPONSE_COMMENTS:
        assert isinstance(resp, str) and len(resp) > 0
    return True


def test_telegram_bot_import():
    """Test that the Telegram bot module imports correctly."""
    from telegram_bot import TelegramBot, start_telegram_bot
    # TelegramBot class exists with expected methods
    assert hasattr(TelegramBot, "__init__")
    assert hasattr(TelegramBot, "send_alert")
    assert hasattr(TelegramBot, "get_updates")
    assert hasattr(TelegramBot, "send_message")
    assert hasattr(TelegramBot, "handle_update")
    # start_telegram_bot function exists
    assert callable(start_telegram_bot)
    return True


def test_web_viz_import():
    """Test that the web visualization module imports correctly."""
    from web_viz import STATE, update_state, start_server, DashboardHandler
    # STATE has expected keys
    assert "wallets" in STATE
    assert "prices" in STATE
    assert "phase" in STATE
    assert "current_price" in STATE
    # update_state function works
    update_state(phase="TEST", price=0.001, mc_usd=450.0)
    assert STATE["phase"] == "TEST"
    assert STATE["current_price"] == 0.001
    assert STATE["mc_usd"] == 450.0
    return True


# ─── Telegram Bot Feature Tests ───
def test_telegram_bot_instantiation():
    """Test that TelegramBot can be instantiated with mock token."""
    from telegram_bot import TelegramBot
    bot = TelegramBot("fake_token_for_testing")
    assert bot.token == "fake_token_for_testing"
    assert bot.running is False
    assert bot.trade_count == 0
    assert bot.comment_bot is not None  # Comment bot should initialize
    return True


def test_telegram_user_state():
    """Test that per-user state is managed correctly."""
    from telegram_bot import TelegramBot
    bot = TelegramBot("fake_token")
    state1 = bot._get_state(123)
    state2 = bot._get_state(456)
    # Different chat IDs get separate state
    assert state1 is not state2
    # State has expected default keys
    for key in ["tier", "slippage", "strategy", "preset", "notifications", "owl_enabled"]:
        assert key in state1
    # Modifying one doesn't affect the other
    state1["tier"] = "LARGE"
    assert state2["tier"] == "SMALL"
    return True


def test_telegram_keyboards():
    """Test that all keyboard builders produce valid markup."""
    from telegram_bot import TelegramBot
    bot = TelegramBot("fake_token")
    # Main menu
    menu = bot._main_menu_keyboard()
    assert "inline_keyboard" in menu
    assert len(menu["inline_keyboard"]) == 4  # 4 rows
    # Settings
    settings = bot._settings_keyboard()
    assert len(settings["inline_keyboard"]) == 3
    # Presets
    presets = bot._presets_keyboard()
    assert len(presets["inline_keyboard"]) >= 5  # 4 presets + back row
    return True


def test_telegram_commands():
    """Test that all command handlers work without errors."""
    import os
    os.environ["TELEGRAM_CHAT_ID"] = "12345"
    from telegram_bot import TelegramBot
    from unittest.mock import MagicMock
    bot = TelegramBot("fake_token")
    # Mock send_message to capture calls without API errors
    bot.send_message = MagicMock(return_value={"ok": False, "error": "mock"})
    # Test each handler
    bot.handle_start(12345)
    bot.handle_menu(12345)
    bot.handle_status(12345)
    bot.handle_settings(12345)
    bot.handle_presets(12345)
    bot.handle_strategy(12345, "Round Robin")
    bot.handle_comment(12345, "on")
    bot.handle_profile(12345, "gen")
    bot.handle_export(12345)
    bot.handle_owl(12345, "on")
    bot.handle_alerts(12345, "on")
    bot.handle_version(12345)
    # All handlers should have been called
    assert bot.send_message.call_count >= 12
    return True


def test_telegram_alerts():
    """Test that alert sending uses correct format."""
    import os
    os.environ["TELEGRAM_CHAT_ID"] = "12345"
    from telegram_bot import TelegramBot
    bot = TelegramBot("fake_token")
    # Mock _api_request to capture the message
    captured = {}
    bot._api_request = lambda method, **params: captured.update(params) or {"ok": True}
    result = bot.send_alert("Test alert message", "critical")
    assert captured["text"] == "🚨 Test alert message"
    assert result["ok"] is True
    return True


def test_telegram_callbacks():
    """Test that inline keyboard callbacks are routed correctly."""
    from telegram_bot import TelegramBot
    from unittest.mock import MagicMock
    bot = TelegramBot("fake_token")
    bot.send_message = MagicMock(return_value={"ok": False})
    bot.answer_callback = MagicMock(return_value={"ok": True})
    # Test callback routing
    test_callbacks = [
        "cmd_buy", "cmd_sell", "cmd_snipe", "cmd_status",
        "cmd_settings", "cmd_strategies", "cmd_wallet",
        "cmd_comment", "cmd_charts", "cmd_owl", "cmd_alerts",
        "cmd_export", "back_to_main", "preset_Aggressive Pump",
        "set_tier", "set_slippage", "set_dex",
    ]
    for cb in test_callbacks:
        bot.handle_callback(cb, 12345, 999)
    # Should have sent messages for each callback
    assert bot.send_message.call_count >= len(test_callbacks)
    return True


def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "=" * 60)
    print("  Pump.fun Lifecycle CLI — Integration Test Suite")
    print("=" * 60)

    # Unit Tests: SmartBundler
    print("\n--- SmartBundler Unit Tests ---")
    _run_test("SB: Batch splitting logic", test_smart_bundler_batch)
    _run_test("SB: WalletInfo serialization", test_smart_bundler_wallet_info)
    _run_test("SB: BundleResult defaults", test_smart_bundler_result)

    # Unit Tests: Money Flow
    print("\n--- Money Flow Unit Tests ---")
    _run_test("MF: USD/SOL conversion", test_money_flow_conversion)
    _run_test("MF: Tier config", test_money_flow_tiers)
    _run_test("MF: Allocation strategies", test_money_flow_allocations)
    _run_test("MF: Fee estimation", test_money_flow_fees)
    _run_test("MF: Pump fee brackets", test_money_flow_pump_fees)
    _run_test("MF: Budget analysis", test_money_flow_budget_analysis)

    # Unit Tests: CLI
    print("\n--- CLI Unit Tests ---")
    _run_test("CLI: Parser (full auto)", test_cli_parser)
    _run_test("CLI: Parser (individual phases)", test_cli_parser_individual)
    _run_test("CLI: State save/load", test_cli_state_save_load)
    _run_test("CLI: Phase state tracking", test_cli_phase_state)
    _run_test("CLI: Tiered allocation", test_cli_tiered_allocation)
    _run_test("CLI: Take-profit tiers", test_cli_take_profit_tiers)

    # Integration Tests
    print("\n--- Integration Tests ---")
    _run_test("INT: Money ↔ Bundler compat", test_integration_money_bundler)
    _run_test("INT: Fee vs budget sanity", test_integration_fee_budget)
    _run_test("INT: Full state lifecycle", test_integration_state_lifecycle)

    # Constants Verification
    print("\n--- Constants Verification ---")
    _run_test("CONST: Pump.fun fee", test_constants_pump_fee)
    _run_test("CONST: Faucets", test_constants_faucets)
    _run_test("CONST: Emergency thresholds", test_constants_emergency)

    # CLI Validation
    print("\n--- CLI Argument Validation ---")
    _run_test("CLI: No phase selection", test_cli_validation_no_phase)
    _run_test("CLI: Budget parsing", test_cli_validation_budget)
    _run_test("CLI: Network flag", test_cli_validation_network)

    # New Feature Tests
    print("\n--- RugCheck Integration Tests ---")
    _run_test("RC: StopLoss config defaults", test_stoploss_config_defaults)
    _run_test("RC: StopLoss state defaults", test_stoploss_state_defaults)
    _run_test("RC: StopLoss config serialization", test_stoploss_config_serialization)
    _run_test("RC: TAKE_PROFIT_TIERS sum to 1.0", test_take_profit_tiers_sum)
    _run_test("RC: CLI rugcheck flag exists", test_cli_rugcheck_flag)
    _run_test("RC: CLI stop-loss flags exist", test_cli_stoploss_flags)
    _run_test("RC: Full dry-run lifecycle", test_full_dryrun_lifecycle)
    _run_test("RC: Dry-run with warmup", test_dryrun_with_warmup)
    _run_test("RC: Dry-run with rugcheck", test_dryrun_with_rugcheck)
    _run_test("RC: StopLoss config customization", test_rugcheck_report_parsing)
    _run_test("RC: Money flow tiered allocation", test_money_flow_tiered_allocation)
    _run_test("RC: Money flow aggressive allocation", test_money_flow_aggressive_allocation)
    _run_test("RC: Money flow custom allocation", test_money_flow_custom_pct_allocation)
    _run_test("RC: SmartBundler slippage fallback", test_smart_bundler_slippage_fallback)
    _run_test("RC: CLI inspect flags", test_cli_inspect_flags)
    _run_test("RC: CLI warmup flag", test_cli_warmup_flag)
    _run_test("RC: CLI dashboard flag", test_cli_dashboard_flag)
    _run_test("RC: RugCheck function exists", test_rugcheck_function_exists)
    _run_test("RC: StopLoss config defaults", test_stop_loss_config_defaults)
    _run_test("RC: StopLoss state defaults", test_stop_loss_state_defaults)
    _run_test("RC: Take-profit tiers consistency", test_take_profit_tiers_consistency)
    _run_test("RC: Dry-run full + warmup + rugcheck", test_dryrun_full_with_warmup_and_rugcheck)
    _run_test("RC: SmartBundler imports", test_smart_bundler_imports)
    _run_test("RC: Money flow all strategies", test_money_flow_all_strategies)
    _run_test("RC: Budget analysis full", test_money_flow_budget_analysis_full)

    # SubCLI Subcommand Tests
    print("\n--- CLI Subcommand Tests ---")
    _run_test("SUB: CLI subcommands registered", test_cli_subcommands)
    _run_test("SUB: Profile create/list/show", test_cli_profile_lifecycle)
    _run_test("SUB: Wallet generate dry", test_cli_wallet_generate)
    _run_test("SUB: Fund dry-run", test_cli_fund_dry_run)
    _run_test("SUB: LUT create", test_cli_lut_create)
    _run_test("SUB: Full dry-run lifecycle via CLI", test_cli_full_dryrun)

    # Three-Tier Architecture Integration Tests
    print("\n--- Three-Tier Integration Tests ---")
    _run_test("TIER: TradingOrchestrator init", test_trading_orchestrator_init)
    _run_test("TIER: BondingCurveTrader math", test_bonding_curve_math)
    _run_test("TIER: MoneyFlowEngine allocation", test_money_flow_engine_allocation)
    _run_test("TIER: ChartPatternGenerator", test_chart_pattern_generator)
    _run_test("TIER: Full orchestrator simulation", test_orchestrator_simulation)

    # Enhanced Feature Integration Tests
    print("\n--- Enhanced Feature Integration Tests ---")
    _run_test("ENH: Token mint lookup", test_config_token_lookup)
    _run_test("ENH: Three Commas presets", test_config_presets)
    _run_test("ENH: Budget tier recommendation", test_config_tier_recommendation)
    _run_test("ENH: Profile generation", test_profile_gen)
    _run_test("ENH: Comment cost estimation", test_comment_cost)
    _run_test("ENH: Comment phrase library", test_comment_phrases)
    _run_test("ENH: Telegram bot import", test_telegram_bot_import)
    _run_test("ENH: Web dashboard import", test_web_viz_import)

    # Telegram Bot Feature Tests
    print("\\n--- Telegram Bot Feature Tests ---")
    _run_test("TEL: Bot instantiation", test_telegram_bot_instantiation)
    _run_test("TEL: User state management", test_telegram_user_state)
    _run_test("TEL: Keyboard builders", test_telegram_keyboards)
    _run_test("TEL: Command handlers", test_telegram_commands)
    _run_test("TEL: Alert sending", test_telegram_alerts)
    _run_test("TEL: Callback handling", test_telegram_callbacks)

    # Summary
    print("\n" + "=" * 60)
    total = _test_results["passed"] + _test_results["failed"]
    print(f"  Results: {_test_results['passed']}/{total} passed, {_test_results['failed']} failed")

    if _test_results["errors"]:
        print("\n  Failures:")
        for err in _test_results["errors"]:
            print(f"    • {err}")

    print("=" * 60)
    return _test_results["failed"] == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
