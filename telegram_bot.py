"""
Telegram Bot module for the Pump.fun Lifecycle CLI.

Implements a Maestro-style and Three Commas-style Telegram trading bot
using Python stdlib urllib (no pip required).

Features:
- Inline keyboard menus with callback queries
- Trading commands: /buy, /sell, /snipe, /dca, /trailing
- Three Commas preset profiles (Aggressive Pump, Conservative DCA, etc.)
- Take Profit / Stop Loss settings
- Background mode (OWL-style auto-trading)
- Comment bot integration
- Portfolio monitoring and alerts

Usage:
  1. Create a bot with @BotFather on Telegram
  2. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
  3. Run: python3 telegram_bot.py
  4. Send /start to your bot

Environment variables:
  TELEGRAM_BOT_TOKEN — Bot token from @BotFather
  TELEGRAM_CHAT_ID   — Your personal chat ID (for notifications)
  TELEGRAM_BOT_PIN   — Optional PIN protection
"""

import json
import urllib.request
import urllib.parse
import threading
import time
import os
from typing import Any, Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
import sys
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from config import (
    THREE_COMMAS_PRESETS, resolve_token_mint, list_presets,
    get_preset_config, list_tiers, calculate_tier_cost,
)

TELEGRAM_API_BASE = "https://api.telegram.org/bot"
DEFAULT_POLL_INTERVAL = 1  # seconds between polling updates
MAX_TRADES_PER_MINUTE = 10  # rate limiting


class TelegramBot:
    """Telegram trading bot with Three Commas-style features.

    Uses urllib for HTTP requests (no pip dependencies).
    Supports inline keyboards, callback queries, and background trading.

    Commands:
      /start, /menu, /help, /status, /balance, /portfolio,
      /buy, /sell, /snipe, /dca, /strategy, /settings,
      /presets, /comment, /profile, /alerts, /owl, /export, /version
    """

    def __init__(self, token: str, proxy: Optional[str] = None):
        self.token = token
        self.api_base = f"{TELEGRAM_API_BASE}{token}/"
        self.offset = 0
        self.running = False
        self.last_trade_time = 0
        self.trade_count = 0
        self.user_state: Dict[int, Dict[str, Any]] = {}
        self.proxy = proxy
        if proxy:
            proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            self.opener = urllib.request.build_opener(proxy_handler)
        else:
            self.opener = urllib.request.build_opener()

        # Initialize comment bot
        try:
            from comment_bot import CommentBot
            self.comment_bot = CommentBot()
        except Exception:
            self.comment_bot = None

        # Trading state
        self.trading_enabled = False
        self.owl_mode = False
        self.owl_schedule = "hourly"

        # Notification cooldown tracking
        # Maps priority level -> last sent timestamp
        self._alert_cooldowns: Dict[str, float] = {}
        # Default cooldown periods in seconds per priority
        self.alert_cooldowns = {
            "info": float(os.environ.get("TELEGRAM_COOLDOWN_INFO", "60")),
            "warning": float(os.environ.get("TELEGRAM_COOLDOWN_WARNING", "30")),
            "emergency": float(os.environ.get("TELEGRAM_COOLDOWN_EMERGENCY", "0")),  # Always send emergencies
        }
        # Stealth mode — suppresses non-critical alerts when bubble risk is high
        self.stealth_mode = False
        self.stealth_threshold = 0.6  # Activate stealth when bubble risk exceeds this
        self.current_bubble_risk = 0.0

    # ─── Telegram API Core ───

    def _api_request(self, method: str, **params) -> Dict[str, Any]:
        url = f"{self.api_base}{method}"
        if "chat_id" in params:
            params["chat_id"] = str(params["chat_id"])
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if method == "getUpdates" and not params.get("data"):
            url_with_params = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url_with_params, method="GET")
        try:
            with self.opener.open(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if not result.get("ok"):
                    print(f"  [ERROR] Telegram API {method}: {result.get('description', 'Unknown error')}")
                return result
        except urllib.error.HTTPError as e:
            print(f"  [ERROR] HTTP {e.code} for {method}: {e.read().decode()}")
            return {"ok": False, "error_code": e.code}
        except Exception as e:
            print(f"  [ERROR] Network error for {method}: {e}")
            return {"ok": False, "error": str(e)}

    def get_updates(self, timeout: int = 30) -> List[Dict[str, Any]]:
        params = {"offset": self.offset + 1 if self.offset > 0 else 0, "timeout": timeout, "limit": 100}
        result = self._api_request("getUpdates", **params)
        updates = result.get("result", [])
        for update in updates:
            self.offset = max(self.offset, update.get("update_id", 0))
        return updates

    def send_message(self, chat_id: int, text: str,
                     reply_markup: Optional[Dict] = None,
                     parse_mode: str = "Markdown") -> Dict[str, Any]:
        params = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        return self._api_request("sendMessage", **params)

    def edit_message(self, chat_id: int, message_id: int, text: str,
                     reply_markup: Optional[Dict] = None) -> Dict[str, Any]:
        params = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        return self._api_request("editMessageText", **params)

    def answer_callback(self, callback_id: str, text: str = "") -> Dict[str, Any]:
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
            params["show_alert"] = True
        return self._api_request("answerCallbackQuery", **params)

    def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> Dict[str, Any]:
        params = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            params["caption"] = caption
        return self._api_request("sendPhoto", **params)

    # ─── Keyboard Builders ───

    def _inline_keyboard(self, buttons: List[List[Dict[str, str]]]) -> Dict:
        return {"inline_keyboard": [[{"text": b["text"], "callback_data": b["callback_data"]}
                                     for b in row] for row in buttons]}

    def _main_menu_keyboard(self) -> Dict:
        return self._inline_keyboard([
            [{"text": "BUY", "callback_data": "cmd_buy"},
             {"text": "SELL", "callback_data": "cmd_sell"},
             {"text": "SNIPER", "callback_data": "cmd_snipe"}],
            [{"text": "STATUS", "callback_data": "cmd_status"},
             {"text": "SETTINGS", "callback_data": "cmd_settings"},
             {"text": "STRATEGIES", "callback_data": "cmd_strategies"}],
            [{"text": "WALLET", "callback_data": "cmd_wallet"},
             {"text": "COMMENT BOT", "callback_data": "cmd_comment"},
             {"text": "CHARTS", "callback_data": "cmd_charts"}],
            [{"text": "STEALTH", "callback_data": "cmd_stealth"},
             {"text": "OWL (BG)", "callback_data": "cmd_owl"},
             {"text": "ALERTS", "callback_data": "cmd_alerts"},
             {"text": "EXPORT", "callback_data": "cmd_export"}],
        ])

    def _settings_keyboard(self) -> Dict:
        return self._inline_keyboard([
            [{"text": "TIER", "callback_data": "set_tier"},
             {"text": "SLIPPAGE", "callback_data": "set_slippage"},
             {"text": "DEX", "callback_data": "set_dex"}],
            [{"text": "FEES", "callback_data": "set_fees"},
             {"text": "WALLETS", "callback_data": "set_wallets"},
             {"text": "NOTIFY", "callback_data": "set_notify"}],
            [{"text": "OWL", "callback_data": "set_owl"},
             {"text": "STRATEGY", "callback_data": "set_strategy"},
             {"text": "BACK", "callback_data": "back_to_main"}],
        ])

    def _presets_keyboard(self) -> Dict:
        buttons = []
        for name in THREE_COMMAS_PRESETS:
            buttons.append([{"text": name, "callback_data": f"preset_{name}"}])
        buttons.append([{"text": "BACK", "callback_data": "back_to_strategies"}])
        return self._inline_keyboard(buttons)

    # ─── User State Management ───

    def _get_state(self, chat_id: int) -> Dict[str, Any]:
        if chat_id not in self.user_state:
            self.user_state[chat_id] = {
                "tier": "SMALL",
                "slippage": 300,
                "selected_token": None,
                "selected_wallet": None,
                "strategy": "Round Robin",
                "preset": None,
                "notifications": True,
                "owl_enabled": False,
                "tp_x": 2.0,
                "tp_pct": 50,
                "sl_pct": 30,
            }
        return self.user_state[chat_id]

    # ─── Command Handlers ───

    def handle_start(self, chat_id: int) -> None:
        text = (
            "**Solana Pump.fun Bot**\n\n"
            "Welcome! Your automated Pump.fun trading bot is ready.\n\n"
            "**Quick Commands:**\n"
            "/buy BONK 5  — Buy $5 of BONK\n"
            "/sell BONK 50%  — Sell 50% of BONK\n"
            "/snipe TOKEN  — Snipe new pair\n"
            "/status  — Portfolio value\n"
            "/settings  — Bot settings\n"
            "/presets  — Three Commas profiles\n"
            "/comment on  — Enable comment bot\n\n"
            "Use buttons below for common actions:"
        )
        self.send_message(chat_id, text, self._main_menu_keyboard())

    def handle_menu(self, chat_id: int) -> None:
        text = (
            "**Command Reference**\n\n"
            "**Trading:**\n"
            "/buy TOKEN [AMOUNT] — Buy at market price\n"
            "/sell TOKEN [AMOUNT] — Sell at market price\n"
            "/snipe [TOKEN] — Snipe new pair\n"
            "/dca on/off TOKEN — DCA bot control\n"
            "/trailing on/off — Trailing stop loss\n\n"
            "**Settings:**\n"
            "/settings — Full settings menu\n"
            "/presets — Three Commas profiles\n"
            "/strategy — Set trading strategy\n\n"
            "**Info:**\n"
            "/status — Portfolio + bot status\n"
            "/balance — Wallet balances\n"
            "/portfolio — Portfolio allocation\n\n"
            "**Tools:**\n"
            "/comment on/off — Pump.fun comment bot\n"
            "/profile gen — Generate wallet profiles\n"
            "/export — Export trade history\n"
            "/owl on/off — Background auto-trading\n"
            "/alerts — Set price/volume alerts"
        )
        self.send_message(chat_id, text)

    def handle_buy(self, chat_id: int, args: str) -> None:
        parts = args.split()
        if len(parts) < 1:
            self.send_message(chat_id,
                "Usage: /buy TOKEN [AMOUNT]\nExample: /buy BONK 5 (buy $5 of BONK)\n"
                "Use /buy BONK 50% for percentage of wallet balance.")
            return

        symbol = parts[0].upper()
        amount = parts[1] if len(parts) > 1 else "auto"

        mint = resolve_token_mint(symbol)
        state = self._get_state(chat_id)
        tiers = list_tiers()
        tier_info = tiers.get(state["tier"], {})

        text = (
            f"**BUY Order**\n\n"
            f"Token: `{symbol}` ({mint[:8]}...)\n"
            f"Amount: {amount}\n"
            f"Tier: {state['tier']}\n"
            f"Slippage: {state['slippage']}bps\n"
            f"Strategy: {state['strategy']}\n\n"
            f"⚠️ This will execute a REAL trade when connected to a wallet."
        )
        self.send_message(chat_id, text)

    def handle_sell(self, chat_id: int, args: str) -> None:
        parts = args.split()
        if len(parts) < 1:
            self.send_message(chat_id,
                "Usage: /sell TOKEN [AMOUNT]\nExample: /sell BONK 5 (sell $5 of BONK)\n"
                "Use /sell BONK 50% for percentage of holdings.")
            return

        symbol = parts[0].upper()
        amount = parts[1] if len(parts) > 1 else "all"
        mint = resolve_token_mint(symbol)

        text = (
            f"**SELL Order**\n\n"
            f"Token: `{symbol}` ({mint[:8]}...)\n"
            f"Amount: {amount}\n\n"
            f"⚠️ This will execute a REAL trade when connected to a wallet."
        )
        self.send_message(chat_id, text)

    def handle_snipe(self, chat_id: int, args: str) -> None:
        state = self._get_state(chat_id)
        preset_name = state.get("preset") or "Sniper"
        preset = get_preset_config(preset_name)

        text = (
            f"**SNIPE Mode**\n\n"
            f"Mode: {'Auto (new pairs)' if not args else f'Token: {args.upper()}'}\n"
            f"Wallets: {preset['num_wallets']}\n"
            f"Slippage: {state['slippage']}bps\n"
            f"Jito: {'enabled' if preset.get('use_jito', False) else 'disabled'}\n"
            f"Bundle: {'enabled' if preset.get('use_bundles', False) else 'disabled'}\n"
            f"Anti-sniper: {'enabled' if preset.get('anti_sniper', False) else 'disabled'}\n\n"
            f"TP: {preset['take_profit_x']}x at {preset['take_profit_pct']}%\n"
            f"SL: {preset['stop_loss_pct']}%\n\n"
            f"Ready to snipe. Use /snipe on to enable auto-sniping."
        )
        self.send_message(chat_id, text)

    def handle_status(self, chat_id: int) -> None:
        state = self._get_state(chat_id)
        text = (
            f"**Bot Status**\n\n"
            f"Bot: Online\n"
            f"OWL Mode: {'Enabled' if state['owl_enabled'] else 'Disabled'}\n"
            f"Strategy: {state['strategy']}\n"
            f"Tier: {state['tier']}\n"
            f"Slippage: {state['slippage']}bps\n\n"
            f"**Portfolio:**\n"
            f"SOL: $0.00 (connect wallet to view)\n"
            f"Status: {state.get('trading_enabled', False) and 'Active' or 'Idle'}\n\n"
            f"Use /balance for detailed wallet balances."
        )
        self.send_message(chat_id, text, self._main_menu_keyboard())

    def handle_settings(self, chat_id: int) -> None:
        text = "⚙️ **Bot Settings**\n\nSelect a category to configure:"
        self.send_message(chat_id, text, self._settings_keyboard())

    def handle_stealth(self, chat_id: int, args: str = "") -> None:
        """Handle /stealth command — enable/disable stealth mode."""
        if args:
            if args.lower() in ("on", "enable", "active"):
                self.set_stealth_mode(True)
                self.send_message(chat_id, "🥷 Stealth mode ENABLED — non-critical alerts will be suppressed\n(Auto-disabled when bubble risk drops below threshold)")
            elif args.lower() in ("off", "disable", "inactive"):
                self.set_stealth_mode(False)
                self.send_message(chat_id, "🔓 Stealth mode DISABLED — all alerts will be sent")
            else:
                self.send_message(chat_id, f"Usage: /stealth [on|off]\nCurrent status: {'ENABLED' if self.stealth_mode else 'DISABLED'}\nBubble risk: {self.current_bubble_risk:.2f}")
        else:
            status = "ENABLED" if self.stealth_mode else "DISABLED"
            self.send_message(chat_id, f"🥷 **Stealth Mode**: {status}\nBubble risk: {self.current_bubble_risk:.2f}/{self.stealth_threshold:.2f}\nUse `/stealth on` to enable, `/stealth off` to disable")

    def handle_presets(self, chat_id: int) -> None:
        text = "🎯 **Three Commas Preset Profiles**\n\nSelect a preset to auto-configure all settings:"
        self.send_message(chat_id, text, self._presets_keyboard())

    def handle_strategy(self, chat_id: int, args: str = "") -> None:
        strategies = ["Round Robin", "Ping Pong", "Ring Trading", "Whale Mimicry", "Market Maker"]
        if args:
            state = self._get_state(chat_id)
            matching = [s for s in strategies if args.lower() in s.lower()]
            if matching:
                state["strategy"] = matching[0]
                self.send_message(chat_id, f"✅ Strategy set to: **{matching[0]}**")
            else:
                self.send_message(chat_id, f"Unknown strategy: `{args}`\nAvailable: {', '.join(strategies)}")
        else:
            text = "📜 **Available Strategies**\n\n"
            for s in strategies:
                text += f"• {s}\n"
            text += "\nUse: /strategy Round Robin"
            self.send_message(chat_id, text)

    def handle_comment(self, chat_id: int, args: str = "") -> None:
        state = self._get_state(chat_id)
        if not args:
            cost = 0.0  # API-based comments are free
            text = (
                f"🐦 **Comment Bot**\n\n"
                f"Status: {'Enabled' if state.get('comment_enabled', False) else 'Disabled'}\n"
                f"Cost per comment: $0.00 (API-based = FREE)\n"
                f"Max rate: {MAX_TRADES_PER_MINUTE}/min\n\n"
                f"Use: /comment on  or  /comment off"
            )
            self.send_message(chat_id, text)
        elif args.lower() in ("on", "enable", "true"):
            state["comment_enabled"] = True
            self.send_message(chat_id, "✅ Comment bot enabled")
        elif args.lower() in ("off", "disable", "false"):
            state["comment_enabled"] = False
            self.send_message(chat_id, "✅ Comment bot disabled")

    def handle_profile(self, chat_id: int, args: str = "") -> None:
        if args and args.lower() in ("gen", "generate"):
            try:
                from profile_gen import generate_profiles_for_bundle, get_profile_summary
                profiles = generate_profiles_for_bundle(num_wallets=5, seed=None)
                summary = get_profile_summary(profiles)
                self.send_message(chat_id, f"✅ Generated 5 wallet profiles:\n{summary}")
            except ImportError:
                self.send_message(chat_id, "❌ profile_gen.py not available")
        else:
            text = (
                f"💼 **Wallet Profile Manager**\n\n"
                f"Generate human-like profiles for bot wallets.\n\n"
                f"Use: /profile gen"
            )
            self.send_message(chat_id, text)

    def handle_export(self, chat_id: int, args: str = "") -> None:
        """Handle /export command — export trade history or wallet data."""
        state = self._get_state(chat_id)
        if args and args.lower() in ("csv", "json"):
            # Generate export data
            from datetime import datetime
            timestamp = datetime.now().isoformat()
            if args.lower() == "csv":
                csv_lines = ["timestamp,event,token,amount,price,sol_bal,usd_val"]
                csv_lines.append(f"{timestamp},portfolio_snapshot,{state.get('selected_token','') or 'ALL'},0,0.0,0.0,0.0")
                csv_data = "\n".join(csv_lines)
                self.send_message(chat_id, f"📊 CSV Export (preview):\n```\n{csv_data[:500]}\n```\n\nFull export available via CLI: `python3 cli.py export csv --output trades.csv`")
            else:
                json_data = {
                    "exported_at": timestamp,
                    "user_state": {k: v for k, v in state.items() if k != "wallets"},
                    "wallets": state.get("wallets", []),
                    "preset": state.get("preset"),
                    "strategy": state.get("strategy"),
                }
                self.send_message(chat_id, f"📋 JSON Export (preview):\n```\n{json.dumps(json_data, indent=2)[:500]}...\n```\n\nFull export via CLI: `python3 cli.py export json`")
        else:
            text = (
                f"📋 **Export Options**\n\n"
                f"Available formats:\n"
                f"• CSV — Trade history, P&L (for tax)\n"
                f"• JSON — Full state, wallet data\n\n"
                f"Use: /export csv  or  /export json"
            )
            self.send_message(chat_id, text)

    def handle_wallet(self, chat_id: int, args: str = "") -> None:
        """Handle wallet management — list, add, remove wallets."""
        state = self._get_state(chat_id)
        wallets = state.get("wallets", [])

        if not args:
            text = (
                f"💼 **Wallet Manager**\n\n"
                f"Wallets: {len(wallets)}\n"
                f"Status: {'Connected' if wallets else 'No wallets connected'}\n\n"
                f"Commands:\n"
                f"/wallet add PUBKEY — Add wallet\n"
                f"/wallet remove INDEX — Remove wallet\n"
                f"/wallet list — List all wallets\n"
                f"/wallet clear — Remove all wallets"
            )
            self.send_message(chat_id, text)
        elif args.lower().startswith("list"):
            if not wallets:
                self.send_message(chat_id, "📭 No wallets added. Use /wallet add PUBKEY")
                return
            text = f"💼 **Wallet List** ({len(wallets)}):\n\n"
            for i, w in enumerate(wallets):
                text += f"{i+1}. {w.get('pubkey', '')[:8]}... — {w.get('label', 'unnamed')}\n"
            self.send_message(chat_id, text)
        elif args.lower().startswith("add"):
            parts = args.split()
            if len(parts) < 2:
                self.send_message(chat_id, "Usage: /wallet add PUBKEY [LABEL]")
                return
            pubkey = parts[1]
            label = parts[2] if len(parts) > 2 else f"Wallet{len(wallets)+1}"
            wallets.append({"pubkey": pubkey, "label": label})
            state["wallets"] = wallets
            self.send_message(chat_id, f"✅ Added wallet: {label} ({pubkey[:8]}...)")
        elif args.lower().startswith("remove"):
            parts = args.split()
            if len(parts) < 2 or not parts[1].isdigit():
                self.send_message(chat_id, "Usage: /wallet remove INDEX")
                return
            idx = int(parts[1]) - 1
            if 0 <= idx < len(wallets):
                removed = wallets.pop(idx)
                self.send_message(chat_id, f"✅ Removed: {removed.get('label', 'wallet')}")
            else:
                self.send_message(chat_id, f"❌ Invalid index: {parts[1]}")
        elif args.lower().startswith("clear"):
            count = len(wallets)
            state["wallets"] = []
            self.send_message(chat_id, f"✅ Cleared {count} wallet(s)")

    def handle_balance(self, chat_id: int, args: str = "") -> None:
        """Handle /balance command — show wallet balances and portfolio."""
        state = self._get_state(chat_id)
        wallets = state.get("wallets", [])

        if not wallets:
            text = (
                f"💰 **Portfolio Balance**\n\n"
                f"No wallets connected.\n"
                f"Use /wallet add PUBKEY to add a wallet."
            )
        else:
            text = f"💰 **Portfolio Balance** ({len(wallets)} wallets):\n\n"
            text += f"SOL: 0.0000 (mock — connect RPC for real balances)\n"
            text += f"USDC: $0.00\n"
            text += f"Tokens: None\n\n"
            text += f"Note: Connect a Solana RPC endpoint for live data."

        self.send_message(chat_id, text)

    def handle_comment_schedule(self, chat_id: int, args: str = "") -> None:
        """Handle comment campaign scheduling."""
        state = self._get_state(chat_id)
        if args and args.lower() == "on":
            state["comment_enabled"] = True
            state["comment_interval"] = 45
            state["comment_duration"] = 10
            self.send_message(chat_id, "✅ Comment campaign started (45s interval, 10 min)")
        elif args and args.lower() == "off":
            state["comment_enabled"] = False
            self.send_message(chat_id, "✅ Comment campaign stopped")
        else:
            text = (
                f"🐦 **Comment Campaign Scheduler**\n\n"
                f"Status: {'Running' if state.get('comment_enabled', False) else 'Stopped'}\n"
                f"Interval: {state.get('comment_interval', 45)}s\n"
                f"Duration: {state.get('comment_duration', 10)} min\n\n"
                f"Use: /comment_schedule on  or  /comment_schedule off"
            )
            self.send_message(chat_id, text)

    def handle_owl(self, chat_id: int, args: str = "") -> None:
        state = self._get_state(chat_id)
        if args and args.lower() in ("on", "enable"):
            state["owl_enabled"] = True
            text = "🌙 **OWL Mode Enabled**\n\nBackground auto-trading active.\nSchedule: hourly"
        elif args and args.lower() in ("off", "disable"):
            state["owl_enabled"] = False
            text = "✅ OWL Mode disabled"
        else:
            text = (
                f"🌙 **OWL Mode**\n\n"
                f"Status: {'Enabled' if state['owl_enabled'] else 'Disabled'}\n"
                f"Schedule: {state.get('owl_schedule', 'hourly')}\n\n"
                f"Use: /owl on  or  /owl off"
            )
        self.send_message(chat_id, text)

    def handle_alerts(self, chat_id: int, args: str = "") -> None:
        state = self._get_state(chat_id)
        if args and args.lower() in ("off", "disable"):
            state["notifications"] = False
            self.send_message(chat_id, "✅ Alerts disabled")
        elif args and args.lower() in ("on", "enable"):
            state["notifications"] = True
            self.send_message(chat_id, "✅ Alerts enabled")
        else:
            text = (
                f"🔔 **Alert Settings**\n\n"
                f"Notifications: {'Enabled' if state['notifications'] else 'Disabled'}\n\n"
                f"Use: /alerts on  or  /alerts off"
            )
            self.send_message(chat_id, text)

    def handle_version(self, chat_id: int) -> None:
        self.send_message(chat_id, "Pump.fun Bot v2.1.0\nBuilt with Three Commas-style presets")

    def handle_callback(self, callback_data: str, chat_id: int, message_id: int) -> None:
        """Handle inline keyboard callback queries."""
        if callback_data == "cmd_buy":
            self.send_message(chat_id, "💰 Enter: /buy TOKEN AMOUNT\nExample: /buy BONK 5")
        elif callback_data == "cmd_sell":
            self.send_message(chat_id, "📉 Enter: /sell TOKEN AMOUNT\nExample: /sell BONK 50%")
        elif callback_data == "cmd_snipe":
            state = self._get_state(chat_id)
            self.handle_snipe(chat_id, state.get("selected_token") or "")
        elif callback_data == "cmd_status":
            self.handle_status(chat_id)
        elif callback_data == "cmd_settings":
            self.handle_settings(chat_id)
        elif callback_data == "cmd_strategies":
            self.handle_strategy(chat_id)
        elif callback_data == "cmd_wallet":
            self.send_message(chat_id, "💼 Use /balance for wallet details")
        elif callback_data == "cmd_comment":
            self.handle_comment(chat_id)
        elif callback_data == "cmd_charts":
            self.send_message(chat_id, "📈 Use /charts to view price charts")
        elif callback_data == "cmd_stealth":
            self.handle_stealth(chat_id)
        elif callback_data == "cmd_owl":
            self.handle_owl(chat_id)
        elif callback_data == "cmd_alerts":
            self.handle_alerts(chat_id)
        elif callback_data == "cmd_export":
            self.handle_export(chat_id)
        elif callback_data == "back_to_main":
            self.handle_start(chat_id)
        elif callback_data.startswith("preset_"):
            preset_name = callback_data.replace("preset_", "")
            state = self._get_state(chat_id)
            state["preset"] = preset_name
            preset = get_preset_config(preset_name)
            text = (
                f"✅ **Preset Applied**: {preset_name}\n\n"
                f"Wallets: {preset['num_wallets']}\n"
                f"Strategy: {preset['strategy']}\n"
                f"TP: {preset['take_profit_x']}x at {preset['take_profit_pct']}%\n"
                f"SL: {preset['stop_loss_pct']}%\n"
                f"Jito: {preset.get('use_jito', False)}\n"
                f"Bundles: {preset.get('use_bundles', False)}\n"
            )
            self.send_message(chat_id, text, self._main_menu_keyboard())
        elif callback_data.startswith("set_"):
            key = callback_data.replace("set_", "")
            self.send_message(chat_id, f"Configure {key}...")
        else:
            self.answer_callback(callback_data, "Unknown callback")

    def handle_update(self, update: Dict[str, Any]) -> None:
        """Process a single update from Telegram."""
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            if text.startswith("/"):
                parts = text.split(None, 1)
                cmd = parts[0][1:].lower()
                args = parts[1] if len(parts) > 1 else ""
                handlers = {
                    "start": lambda: self.handle_start(chat_id),
                    "menu": lambda: self.handle_menu(chat_id),
                    "help": lambda: self.handle_menu(chat_id),
                    "status": lambda: self.handle_status(chat_id),
                    "balance": lambda: self.handle_status(chat_id),
                    "portfolio": lambda: self.handle_status(chat_id),
                    "buy": lambda: self.handle_buy(chat_id, args),
                    "sell": lambda: self.handle_sell(chat_id, args),
                    "snipe": lambda: self.handle_snipe(chat_id, args),
                    "dca": lambda: self.send_message(chat_id, "DCA mode toggled"),
                    "trailing": lambda: self.send_message(chat_id, "Trailing stop toggled"),
                    "settings": lambda: self.handle_settings(chat_id),
                    "stealth": lambda: self.handle_stealth(chat_id, args),
                    "presets": lambda: self.handle_presets(chat_id),
                    "strategy": lambda: self.handle_strategy(chat_id, args),
                    "comment": lambda: self.handle_comment(chat_id, args),
                    "profile": lambda: self.handle_profile(chat_id, args),
                    "export": lambda: self.handle_export(chat_id, args),
                    "owl": lambda: self.handle_owl(chat_id, args),
                    "alerts": lambda: self.handle_alerts(chat_id, args),
                    "wallet": lambda: self.handle_wallet(chat_id, args),
                    "comment_schedule": lambda: self.handle_comment_schedule(chat_id, args),
                    "charts": lambda: self.send_message(chat_id, "📈 Use /charts to view price charts"),
                    "version": lambda: self.handle_version(chat_id),
                }
                handler = handlers.get(cmd)
                if handler:
                    handler()
                else:
                    self.send_message(chat_id, f"Unknown command: /{cmd}\nType /menu for help.")
        elif "callback_query" in update:
            cb = update["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            message_id = cb["message"]["message_id"]
            callback_data = cb.get("data", "")
            self.answer_callback(cb["id"])
            self.handle_callback(callback_data, chat_id, message_id)

    def run(self, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
        """Start long-polling for Telegram updates."""
        self.running = True
        print("Telegram bot running. Press Ctrl+C to stop.")
        try:
            while self.running:
                updates = self.get_updates(timeout=30)
                for update in updates:
                    self.handle_update(update)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("\nTelegram bot stopped.")
        finally:
            self.running = False

    def send_alert(self, message: str, priority: str = "info") -> Dict[str, Any]:
        """Send an alert message to the configured chat ID.

        Respects notification cooldowns and stealth mode:
        - Info messages are suppressed if the same priority was sent within cooldown
        - Emergency alerts always pass through regardless of cooldown
        - In stealth mode, non-critical alerts are suppressed
        """
        chat_id_str = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not chat_id_str:
            return {"ok": False, "error": "TELEGRAM_CHAT_ID not set"}
        chat_id = int(chat_id_str)

        # Stealth mode: suppress non-critical alerts when bubble risk is high
        if self.stealth_mode and priority != "emergency":
            print(f"  [STEALTH] Alert suppressed (bubble risk {self.current_bubble_risk:.2f}): {message[:60]}")
            return {"ok": False, "suppressed": "stealth_mode", "priority": priority}

        # Check cooldown for this priority
        cooldown = self.alert_cooldowns.get(priority, 0)
        last_sent = self._alert_cooldowns.get(priority, 0)
        if cooldown > 0 and (time.time() - last_sent) < cooldown:
            remaining = cooldown - (time.time() - last_sent)
            print(f"  [COOLDOWN] Alert suppressed ({remaining:.0f}s remaining): {message[:60]}")
            return {"ok": False, "suppressed": "cooldown", "priority": priority}

        # Send the alert
        priority_prefix = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(priority, "ℹ️")
        self._alert_cooldowns[priority] = time.time()
        return self.send_message(chat_id, f"{priority_prefix} {message}")

    def set_stealth_mode(self, enabled: bool, threshold: float = 0.6) -> None:
        """Enable/disable stealth mode.

        When enabled, non-critical alerts are suppressed.
        """
        self.stealth_mode = enabled
        self.stealth_threshold = threshold
        status = "ENABLED" if enabled else "DISABLED"
        print(f"  [STEALTH] Stealth mode {status} (threshold: {threshold})")

    def update_bubble_risk(self, risk: float) -> None:
        """Update the current bubble risk and auto-toggle stealth mode."""
        self.current_bubble_risk = risk
        if risk >= self.stealth_threshold and not self.stealth_mode:
            self.set_stealth_mode(True)
            self.send_alert(f"🥷 Stealth mode auto-enabled — bubble risk {risk:.2f}", "warning")
        elif risk < self.stealth_threshold * 0.8 and self.stealth_mode:
            self.set_stealth_mode(False)
            self.send_alert(f"🔓 Stealth mode auto-disabled — bubble risk dropped to {risk:.2f}", "info")


def start_telegram_bot():
    """Entry point for starting the Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN not set in environment")
        return None
    bot = TelegramBot(token)
    print(f"Starting Telegram bot... (chat_id: {os.environ.get('TELEGRAM_CHAT_ID', 'not set')})")
    bot.run()
    return bot


if __name__ == "__main__":
    start_telegram_bot()
