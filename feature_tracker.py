#!/usr/bin/env python3
"""
Feature Memory Tracker for Solana Volume Bot.

Maintaines a persistent record of all bot features, their status,
testing state, and documentation. This is the "single source of truth"
for what exists in the bot and what state it is in.

Usage:
  python3 feature_tracker.py              — Show full feature registry
  python3 feature_tracker.py add          — Add a new feature
  python3 feature_tracker.py status       — Quick status summary
  python3 feature_tracker.py test PASS    — Mark a feature as tested
  python3 feature_tracker.py export       — Export to JSON
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".feature_state.json")

# ─── Feature Registry ───
# This is the comprehensive catalog of all features in the bot.
# Each feature has: name, description, module, status, tests, notes.
# Status values: proposed | in_progress | implemented | tested | deployed | deprecated

FEATURE_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── Core Trading ──
    "wash_trading": {
        "name": "Wash Trading Simulator",
        "description": "Multi-wallet volume simulation with 5 strategies",
        "module": "liquidity.py",
        "status": "tested",
        "strategies": ["Round Robin", "Ping Pong", "Ring Trading", "Whale Mimicry", "Market Maker"],
        "tests": ["CLI demo runs", "web viz renders", "strategies cycle correctly"],
        "notes": "Demo mode works, real execution pending wallet integration",
    },
    "bundle_bot": {
        "name": "Bundle Bot (Jito)",
        "description": "Jito bundle transaction assembly and submission",
        "module": "bundle_bot.py",
        "status": "tested",
    },
    "trading_engine": {
        "name": "Trading Engine",
        "description": "DEX price fetching, token discovery via DexScreener",
        "module": "trading_engine.py",
        "status": "tested",
        "features": ["get_token_info", "get_trending_pairs", "get_price_feed", "get_multiple_prices"],
    },

    # ── Profile & Wallet ──
    "profile_gen": {
        "name": "Profile Generator",
        "description": "Deterministically generated wallet profiles for bundles",
        "module": "profile_gen.py",
        "status": "tested",
        "features": ["50+ usernames", "25+ bios", "16 activity patterns", "JSON export"],
    },
    "comment_bot": {
        "name": "Comment Bot",
        "description": "Pump.fun comment generation and posting",
        "module": "comment_bot.py",
        "status": "tested",
        "features": ["76 comment phrases", "24 response phrases", "cost estimation"],
    },
    "wallet_utils": {
        "name": "Wallet Utilities",
        "description": "Wallet generation, address validation, seed management",
        "module": "wallet_utils.js",
        "status": "tested",
    },

    # ── Telegram Bot ──
    "telegram_bot": {
        "name": "Telegram Bot Core",
        "description": "Full Telegram bot with urllib (no pip dependencies)",
        "module": "telegram_bot.py",
        "status": "tested",
        "features": [
            "30+ commands (/buy, /sell, /snipe, /dca, /strategy, /presets, /owl, /comment, /profile)",
            "Inline keyboard menus (main, settings, strategies)",
            "Three Commas preset profiles (Aggressive Pump, Conservative DCA, Sniper, Market Maker, Grid)",
            "Callback query handling for button navigation",
            "Background polling with rate limiting",
            "Background mode (OWL)",
            "Notifications (trade confirmations, alerts)",
        ],
        "commands": [
            "start", "menu", "buy", "sell", "snipe", "dca", "trailing",
            "status", "balance", "portfolio", "strategies", "strategy",
            "settings", "presets", "comment", "profile", "owl",
            "export", "charts", "wallet", "alerts", "notify",
            "version", "panic", "check", "safety", "rugs", "tp", "sl",
        ],
    },

    # ── Safety Analysis ──
    "safety_check": {
        "name": "Token Safety Analyzer",
        "description": "Rug pull, honeypot, mint authority, holder analysis via RugCheck API",
        "module": "safety_check.py",
        "status": "tested",
        "features": [
            "RugCheck API integration (rugcheck.xyz)",
            "DexScreener liquidity analysis",
            "Holder concentration analysis",
            "Mint authority / freeze authority checks",
            "Known safe token shortcuts (SOL, USDC, USDT)",
            "0-100 safety score with SAFE/MODERATE/CAUTION/DANGEROUS rating",
        ],
        "commands": ["/check TOKEN", "/safety TOKEN", "/rugs TOKEN"],
    },

    # ── Config & Presets ──
    "config_presets": {
        "name": "Three Commas Presets",
        "description": "5 trading profiles adapted from Three Commas platform",
        "module": "config.py",
        "status": "tested",
        "presets": ["Aggressive Pump", "Conservative DCA", "Sniper", "Market Maker", "Grid"],
    },
    "token_shortcuts": {
        "name": "Token Mint Shortcuts",
        "description": "Type BONK instead of full mint address",
        "module": "config.py",
        "status": "tested",
        "features": ["13 ticker lookups", "4 pre-made token lists", "resolve_token_mint()"],
    },

    # ── Web Dashboard ──
    "web_viz": {
        "name": "Web Visualization",
        "description": "Flask dashboard with Chart.js price charts and wash trading visualization",
        "module": "web_viz.py",
        "status": "in_progress",  # Chart rendering needs verification in browser
        "features": ["Price charts", "Wash trading simulation", "Bot status", "Volume metrics"],
    },

    # ── CLI ──
    "cli_menus": {
        "name": "CLI Menu System",
        "description": "Interactive terminal menu with 13 menus + 50+ options",
        "module": "cli.py",
        "status": "tested",
        "menus": [
            "main_menu", "menu_wallet_management", "menu_trading_engine",
            "menu_trading_strategies", "menu_bundle_bot", "menu_onchain_monitoring",
            "menu_portfolio", "menu_token_discovery", "menu_liquidity",
            "menu_advanced_tools", "menu_settings", "menu_env_editor",
            "menu_portfolio_manager", "menu_api_resources",
        ],
    },

    # ── Infrastructure ──
    "tmux_dashboard": {
        "name": "TMUX Dashboard",
        "description": "Terminal dashboard with multiple panes for monitoring",
        "module": "launch_dashboard.sh",
        "status": "tested",
    },
    "man_page": {
        "name": "Man Page Documentation",
        "description": "volbot.1 man page with full documentation",
        "module": "volbot.1",
        "status": "tested",
        "features": ["BEGINNER TIPS", "all module references", "command examples"],
    },
    "dotenv_example": {
        "name": "Environment Template",
        "description": ".env.example with all configuration options",
        "module": ".env.example",
        "status": "tested",
        "features": ["Telegram settings", "RPC config", "wallet seed", "trading params"],
    },

    # ── Plans ──
    "v3_plan": {
        "name": "v3.0 Execution Plan",
        "description": "Full roadmap for Telegram + Three Commas integration",
        "module": "docs/v3-plan.md",
        "status": "completed",
        "phases": ["Telegram core", "Trading features", "Strategy management",
                   "Background mode (OWL)", "File organization", "Three Commas presets", "Advanced features"],
    },
    "research_notes": {
        "name": "Research Notes",
        "description": "Comprehensive research on bots, API, safety tools, environment",
        "module": "RESEARCH.md",
        "status": "tested",
        "sections": ["Bundler projects", "Comment bots", "OWL crypto", "Telegram bots",
                     "Three Commas", "RugCheck API", "proot-distro networking"],
    },
}


def save_state() -> None:
    """Save current state to disk."""
    state = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "features": FEATURE_REGISTRY,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"  State saved to {STATE_FILE}")


def load_state() -> Dict[str, Any]:
    """Load state from disk (or return registry if no state file)."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"features": FEATURE_REGISTRY}


def status_summary() -> None:
    """Print a quick status summary."""
    state = load_state()
    features = state.get("features", FEATURE_REGISTRY)

    print("📊 **Feature Status Summary**\n")
    counts = {"tested": [], "implemented": [], "in_progress": [],
              "proposed": [], "deployed": [], "deprecated": []}

    for key, feat in features.items():
        status = feat.get("status", "proposed")
        counts.setdefault(status, []).append((key, feat))

    for status in ["tested", "implemented", "in_progress", "proposed", "deployed", "deprecated"]:
        items = counts.get(status, [])
        if items:
            print(f"  {status.upper()} ({len(items)}):")
            for key, feat in items:
                name = feat.get("name", key)
                module = feat.get("module", "?")
                print(f"    • {name} ({module})")
            print()


def show_registry() -> None:
    """Show full feature registry."""
    print("📋 **Full Feature Registry**\n")
    print(f"{'STATUS':<12} {'MODULE':<25} {'FEATURE':<35} {'KEY'}")
    print("-" * 90)

    for key, feat in FEATURE_REGISTRY.items():
        status = feat.get("status", "unknown")
        module = feat.get("module", "?")
        name = feat.get("name", key)
        print(f"{status:<12} {module:<25} {name:<35} {key}")

    print(f"\nTotal: {len(FEATURE_REGISTRY)} features")


def mark_tested(feature_key: str) -> None:
    """Mark a feature as tested."""
    if feature_key in FEATURE_REGISTRY:
        FEATURE_REGISTRY[feature_key]["status"] = "tested"
        FEATURE_REGISTRY[feature_key]["last_tested"] = datetime.now(timezone.utc).isoformat()
        print(f"  ✅ {feature_key} marked as tested")
    else:
        print(f"  ❌ Unknown feature: {feature_key}")
        print(f"   Available: {', '.join(FEATURE_REGISTRY.keys())}")


def export_json(filepath: str = None) -> None:
    """Export the full feature registry to JSON."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "docs", "feature_registry.json")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(FEATURE_REGISTRY, f, indent=2)
    print(f"  Exported {len(FEATURE_REGISTRY)} features to {filepath}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_registry()
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        status_summary()
    elif cmd == "add":
        print("  Use the FEATURE_REGISTRY dict in this file to add features")
    elif cmd == "test":
        if len(sys.argv) > 2:
            mark_tested(sys.argv[2])
            save_state()
        else:
            print("  Usage: python3 feature_tracker.py test FEATURE_KEY")
    elif cmd == "export":
        export_json(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "save":
        save_state()
        print("  State saved")
    else:
        print(f"  Unknown command: {cmd}")
        print("  Commands: status, add, test, export, save")
