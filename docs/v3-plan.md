# Solana Volume Bot v3.0 — Complete Feature Integration Plan

> **Version:** v3.0 (Telegram + Three Commas Integration)
> **Date:** August 14, 2026
> **Environment:** Ubuntu (proot-distro) / Termux hybrid
> **Network:** Full internet access confirmed (Telegram API, Jupiter API, Solana RPC, DexScreener)
> **CLI first, web viz second** — verified working

---

## Environment Analysis: proot-distro vs Native Termux

### Current State
- Running in **proot-distro Ubuntu** — a userspace container that virtualizes the filesystem
- `proot-distro` is installed but no distros are currently configured
- This is **Termux's proot-distro feature**, not a traditional VM

### Networking
- ✅ **Full network access** — all external APIs reachable
- ✅ **DNS resolution works** (api.telegram.org resolves correctly)
- ✅ **Port binding works** (can listen on any port for webhooks/dashboard)
- ✅ **No proxy or NAT restrictions**

### proot-distro Limitations vs Native Termux
| Capability | proot-distro Ubuntu | Native Termux |
|---|---|---|
| Network access | ✅ Full | ✅ Full |
| File system | Virtualized (proot) | Direct |
| Performance | ~10-20% slower | Native speed |
| Node.js v20+ | ✅ Available | ✅ Available |
| Python 3.13 + Flask | ✅ Available | ✅ Available |
| tmux 3.6 | ✅ Available | ✅ Available |
| Port binding | ✅ Works | ✅ Works |
| Background processes | Limited (no init system) | Full systemd (if configured) |
| Battery usage | Higher (proot overhead) | Lower |

### Recommendation: Hybrid Migration Strategy
1. **Keep using current proot-distro environment** — it works, has all dependencies
2. **Migrate files to organized structure** — create `solana-volume-bot/` subdirectory
3. **Add native Termux setup script** (`setup_termux.sh`) for future migration
4. **Document proot-distro limitations** — no `nohup`/`systemd`, use tmux sessions instead

### Key Insight: proot-distro is NOT a problem
The environment has **full internet access**, **port binding**, **tmux**, **Python 3.13+Flask**, **Node.js**. The only limitation is performance overhead (~10-20%) and lack of native init system. All functionality works.

---

## Telegram Bot Integration — Architecture

### Approach: urllib-based (No pip needed)
Using Python stdlib `urllib.request` to call Telegram Bot API directly:

```python
# Telegram Bot API via urllib (no pip required)
# Base URL: https://api.telegram.org/bot{TOKEN}/METHOD_NAME
# 
# Core methods used:
# - getMe             — verify bot identity
# - getUpdates        — long polling for messages
# - sendMessage       — send text messages with inline keyboards
# - editMessageText   — update menu text with callback queries
# - sendPhoto         — send charts as images
# - sendDocument      — send trade logs/reports
# - setWebhook        — optional webhook mode (requires HTTPS)
# 
# Updates received as JSON with:
# - message: { chat: {id}, text, from: {id, first_name} }
# - callback_query: { id, from: {id}, data }  # button presses
```

### Telegram Bot Commands (Maestro-style)

| Command | Description | Three Commas Equivalent |
|---------|-------------|------------------------|
| `/start` | Welcome + main menu | Main dashboard |
| `/menu` | Show all available commands | Bot settings menu |
| `/buy` | Quick buy with parameters | SmartTrade buy |
| `/sell` | Quick sell with parameters | SmartTrade sell |
| `/snipe` | Snipe new token pair | Deal start |
| `/status` | Portfolio + bot status | Account info |
| `/wallet` | Wallet management | N/A |
| `/strategies` | Available trading strategies | DCA/GRID bots |
| `/settings` | Bot configuration | Bot settings |
| `/help` | Command reference | Help |

### Telegram Bot Settings (Three Commas-style)

```
Settings Categories:
1. Trading Settings
   - Buy amount (% of portfolio or fixed USD)
   - Slippage tolerance (0.1% - 10%)
   - Priority fee / Jito tip (auto/10k-250k lamports)
   - Gas limit override
   
2. Strategy Settings
   - Strategy: [Round Robin | Ping Pong | Ring | Whale | Market Maker | DCA | GRID | TWAP]
   - Wallet count: 3-20
   - Trade size: $0.10-$50 per swap
   - Simulation cycles: 1-100
   - Comment frequency: every N trades
   
3. Sniping Settings
   - Auto-buy on new pairs: [enabled/disabled]
   - Min liquidity: $1K/$5K/$10K/$50K/$100K
   - Max buy tax: 10%/15%/20%/25%/30%
   - Max sell tax: 10%/15%/20%/25%/30%
   - Reject honeypot: [yes/no]
   - Anti-sniper: [enabled/disabled]
   - Jito Sol price protection: [enabled/disabled]
   
4. Take Profit / Stop Loss
   - TP 1: 25% at 2x | 50% at 3x | 100% at 5x (presets)
   - TP custom: [X% at Yx price]
   - SL: 30% / 50% / 70% drop | custom %
   - Trailing TP: [enabled/disabled] | offset: 2%/5%/10%
   - Trailing SL: [enabled/disabled] | offset: 5%/10%/15%
   
5. Liquidity Management
   - Add liquidity: [enabled/disabled]
   - Remove liquidity: [enabled/disabled] at [X%] target
   - LP token reinvestment: [enabled/disabled]
   - DEX selection: [Raydium | Orca | PumpSwap | Meteora]
   
6. Wallet Management
   - Add wallet (private key or generate)
   - Remove wallet
   - Rename wallet
   - Set primary wallet
   - Export wallet (encrypted)
   
7. Background Mode (OWL-style)
   - Auto-trade: [schedule: hourly/daily/weekly]
   - Volume boost: [enabled/disabled]
   - Comment bot: [enabled/disabled]
   - Profile generation: [enabled/disabled]
   - Stealth mode: [enabled/disabled] (randomize all timings)
   
8. Notifications
   - Trade confirmations: [on/off]
   - Price alerts: [on/off] | threshold: [X%]
   - Volume alerts: [on/off] | threshold: [$XK volume]
   - Error alerts: [on/off]
   - Telegram channel: @your_channel
   
9. API Resources
   - Jupiter API key
   - Alchemy API key
   - QuickNode API key
   - DexScreener API key
   - CoinGecko API key
   - Pump.fun auth token
   
10. Security
    - PIN protection: [enabled/disabled]
    - Withdrawal whitelist: [addresses]
    - Auto-logout: [5/15/30/60 minutes]
    - Delete wallet on logout: [yes/no]
    - Require confirmation for trades > $X
```

### Telegram Inline Keyboard Layout
```
Main Menu (3x3 grid):
┌──────────────┬──────────────┬──────────────┐
│ 💰 BUY       │ 📉 SELL       │ 🎯 SNIPER     │
├──────────────┼──────────────┼──────────────┤
│ 📊 STATUS      │ ⚙️ SETTINGS     │ 📜 STRATEGIES │
├──────────────┼──────────────┼──────────────┤
│ 💼 WALLET      │ 🐦 COMMENT BOT  │ 📈 CHARTS     │
└──────────────┴──────────────┴──────────────┘
```

---

## File Organization Plan

### Target Structure
```
/home/matt/solana-volume-bot/          # Canonical project root
├── cli.py                              # CLI entry point (interactive menu)
├── bot.py                              # Main orchestrator
├── config.py                           # Config management
│   ├── telegram_bot.py                # Telegram bot (urllib-based) [NEW]
├── solana/                             # Core modules
│   ├── __init__.py
│   ├── trading_engine.py              # Jupiter API
│   ├── strategies.py                  # 5 trading strategies
│   ├── strategies_advanced.py         # 7 advanced strategies
│   ├── backtest.py                    # Backtesting engine
│   ├── bundle_bot.py                  # Jito MEV bundles
│   ├── liquidity.py                   # DEX liquidity + wash trading
│   ├── onchain_monitor.py             # Portfolio tracking
│   ├── profile_gen.py                 # Wallet profile generation
│   ├── comment_bot.py                 # Pump.fun comment bot
│   ├── web_viz.py                     # Flask + Chart.js dashboard
│   ├── lut_manager.py                 # LUT optimization [NEW]
│   └── wallet_warmup.py              # Wallet warmup system [NEW]
├── node/                               # Node.js crypto helpers
│   ├── wallet_utils.js                # Key generation/derivation
│   └── sign_sender.js                 # Transaction signing
├── assets/                             # Static resources
│   ├── comment_phrases.txt            # 76+ comment phrases
│   ├── profiles.json                  # Profile templates
│   └── telegram_bot_commands.json     # Bot command definitions [NEW]
├── config/                             # Configuration
│   ├── .env.example                   # Environment template
│   ├── default_settings.json          # Default bot settings [NEW]
│   └── three_commas_mappings.json     # TC→our feature mappings [NEW]
├── scripts/                            # Helper scripts
│   ├── start-bot.sh                    # Main launcher
│   ├── volbot                        # Symlink to start-bot.sh
│   ├── launch_dashboard.sh             # tmux 4-pane dashboard
│   ├── setup_termux.sh                # Native Termux setup [NEW]
│   └── setup_proot.sh                 # proot-distro setup [NEW]
├── docs/                               # Documentation
│   ├── volbot.1                       # Man page
│   ├── SKILL.md                       # Skill documentation
│   ├── RESEARCH.md                    # This research document
│   ├── README.md                      # Quick start
│   └── THREE_COMMAS_GUIDE.md          # TC feature integration guide [NEW]
├── __pycache__/
└── node_modules/                      # @noble/curves, bs58, etc.
```

### Migration Steps
1. **Create new directory structure** (`mkdir -p solana/ assets/ config/ scripts/ docs/`)
2. **Move existing files** to appropriate subdirectories
3. **Update import paths** in all Python files (e.g., `from solana.trading_engine import ...`)
4. **Update start-bot.sh** to set correct PYTHONPATH
5. **Update volbot symlink** to point to new path
6. **Update man page** with new structure
7. **Create setup scripts** (`setup_termux.sh`, `setup_proot.sh`)
8. **Test everything still works** after migration

### Why Organize This Way
- **Matches the user's GRS file structure** (single root, subfolders)
- **All files in one place** — no scattered files across Termux
- **Clear separation** of core modules (solana/), crypto helpers (node/), configs (config/), docs (docs/), scripts (scripts/)
- **Easy to back up** — just `cp -r solana-volume-bot/` to another location
- **Easy to git clone** — the entire project is self-contained in one directory

---

## Telegram Bot Features — Mapped to Bot Application

### Existing Bot Variables & Integration Points

| Existing Variable | Telegram Integration | Three Commas Mapping |
|---|---|---|
| `CONFIG['wallet_seed']` | `/wallet add`, `/wallet generate` | Add funds, withdrawal |
| `CONFIG['rpc_endpoint']` | `/settings rpc` | N/A (Solana-specific) |
| `CONFIG['slippage']` | `/settings slippage` | Slippage tolerance |
| `CONFIG['money_tier']` | `/settings tier` | Deal size configuration |
| `MONEY_TIERS` dict | `/settings tier` submenu | Max/Mini/Deal size |
| `BUNDLE_MODES` | `/snipe bundle` | Bundle deal mode |
| `WASH_STRATEGIES` | `/strategies` | Bot strategies (DCA/GRID) |
| `DEXES` dict | `/settings dex` | Exchange selection |
| `JUPITER_API` | Internal use | Swap routing |
| `JITEO_TIP_ACCOUNTS` | `/snipe tip` | Priority fee |

### New Telegram-Specific Features (Three Commas-style)

```
SmartTrade (buy/sell):
  /buy TOKEN AMOUNT — Buy specific amount of token
  /sell TOKEN AMOUNT — Sell specific amount  
  /buy @ MARKET — Buy at market price
  /sell LIMIT X% — Set take profit at X%
  /sell STOP X% — Set stop loss at X%

DCA Bot:
  /dca on TOKEN — Enable DCA for token
  /dca off TOKEN — Disable DCA
  /dca set INTERVAL AMOUNT — Configure DCA interval and amount
  /dca status — Show all active DCA bots

Sniper:
  /snipe TOKEN — Snipe new pair
  /snipe on — Enable auto-sniping
  /snipe off — Disable auto-sniping
  /snipe whitelist ADDY — Add to whitelist
  /snipe blacklist ADDY — Add to blacklist
  /snipe tax-check TOKEN — Check buy/sell tax

Trailing Features:
  /trailing on OFFSET — Enable trailing with % offset
  /trailing off — Disable trailing
  /trailing status — Show trailing state

Background Mode (OWL):
  /owl on — Enable background auto-trading
  /owl off — Disable background mode
  /owl status — Show OWL activity
  /owl schedule — Set schedule (cron-like)

Notifications:
  /notify on — Enable trade notifications
  /notify off — Disable notifications
  /alert price TOKEN X — Alert when price hits X
  /alert volume TOKEN X — Alert when volume hits X

Portfolio:
  /portfolio — Show portfolio value
  /balance — Show wallet balances
  /trades — Show recent trade history
  /export — Export trade history as CSV
```

### Telegram Bot Class Structure
```python
# telegram_bot.py — uses urllib.request (no pip)
class TelegramBot:
    def __init__(self, token, api_base="https://api.telegram.org/bot"):
        self.token = token
        self.api_base = f"{api_base}{token}/"
        self.offset = 0
        
    # --- Core Telegram API methods (urllib) ---
    def api(self, method, **params):  # Generic API call
    def get_updates(self):             # Long polling
    def send_message(self, chat_id, text, reply_markup=None):
    def edit_message(self, chat_id, msg_id, text, reply_markup=None):
    def send_photo(self, chat_id, photo_path, caption=None):
    def answer_callback(self, callback_id, text):
    
    # --- Trading methods ---
    def handle_buy(self, chat_id, args):
    def handle_sell(self, chat_id, args):
    def handle_snipe(self, chat_id, args):
    def handle_dca(self, chat_id, args):
    def handle_trailing(self, chat_id, args):
    
    # --- Settings methods ---
    def show_main_menu(self, chat_id):
    def show_settings(self, chat_id):
    def show_strategies(self, chat_id):
    
    # --- Notification methods ---
    def notify_trade(self, chat_id, trade_data):
    def notify_error(self, chat_id, error_msg):
    def notify_alert(self, chat_id, alert_type, data):
```

---

## Comprehensive TODO List

### Phase 1: Telegram Bot Core (Week 1)
- [ ] Create `telegram_bot.py` with urllib-based API wrapper
- [ ] Implement `getUpdates` long polling
- [ ] Create inline keyboard system (Button -> Callback)
- [ ] Implement basic commands: /start, /menu, /help, /status
- [ ] Wire up to existing bot.py config system
- [ ] Test bot with fake token, verify API calls work
- [ ] Add `TELEGRAM_BOT_TOKEN` to `.env.example`
- [ ] Add "Telegram Bot" option to CLI Menu 10 (Settings)

### Phase 2: Trading Features (Week 2)
- [ ] Implement /buy and /sell commands with parameter parsing
- [ ] Implement /snipe command (new pair detection + snipe)
- [ ] Implement /dca on/off/set (Dollar Cost Averaging bot)
- [ ] Implement trailing buy/sell (/trailing on/off/status)
- [ ] Implement take profit / stop loss settings
- [ ] Wire up to trading_engine.py Jupiter API integration
- [ ] Test trade execution via Telegram (simulated)

### Phase 3: Strategy & Settings (Week 3)
- [ ] Implement /strategies — list all 12 strategies with descriptions
- [ ] Implement strategy parameter editing via Telegram
- [ ] Implement /settings — full settings menu with inline keyboards
- [ ] Implement /wallet — add/remove/rename wallets
- [ ] Implement /portfolio — show balances, value, P&L
- [ ] Implement notification system (trade confirmations, errors, alerts)
- [ ] Add Telegram settings to .env editor
- [ ] Test full bot management via Telegram

### Phase 4: Background Mode + OWL (Week 4)
- [ ] Implement /owl on/off/status (OWL-style background trading)
- [ ] Implement scheduling system (cron-like, every X hours/days)
- [ ] Implement /notify — configure notification preferences
- [ ] Implement /alert price/volume — custom price/volume alerts
- [ ] Add background mode to tmux dashboard
- [ ] Add Telegram bot process to launch_dashboard.sh
- [ ] Test background trading with volume simulation

### Phase 5: File Organization + Migration (Week 1, parallel)
- [ ] Create new directory structure (solana/, node/, assets/, config/, scripts/, docs/)
- [ ] Move all existing files to new locations
- [ ] Update import paths in all Python files
- [ ] Update start-bot.sh, volbot symlink, launch_dashboard.sh
- [ ] Update man page (volbot.1) with new structure
- [ ] Create setup_termux.sh (native Termux setup script)
- [ ] Create setup_proot.sh (proot-distro setup script)
- [ ] Test all imports and commands after migration
- [ ] Update git repository structure

### Phase 6: Three Commas Integration (Week 5)
- [ ] Create config/three_commas_mappings.json
- [ ] Map all Three Commas bot features to our implementation
- [ ] Implement "preset profiles" (aggressive, conservative, sniper, DCA, grid)
- [ ] Add preset selection to Telegram settings
- [ ] Add "TradingView webhook" receiver (parse webhook signals)
- [ ] Document integration in docs/THREE_COMMAS_GUIDE.md
- [ ] Test with webhook simulations

### Phase 7: Advanced Features (Week 6-8)
- [ ] Create LUT Manager module (lut_manager.py)
- [ ] Create Wallet Warmup module (wallet_warmup.py)
- [ ] Add Phantom wallet integration (phantom_bridge.js)
- [ ] Add social sentiment tracker (Twitter/Discord mentions)
- [ ] Add honeypot detection
- [ ] Add new pair sniping with safety checks
- [ ] Add custom strategy builder to CLI
- [ ] Add strategy paper trading mode
- [ ] Full integration testing

### Ongoing: tmux Dashboard Updates
- [ ] Update launch_dashboard.sh with Telegram bot pane
- [ ] Add OWL/background mode pane
- [ ] Add comment bot status pane
- [ ] Add profile generation status pane
- [ ] Make dashboard auto-start all services
- [ ] Add health check for each pane process

---

## Feature Mapping: Research → Bot Implementation

### From cicere/pumpfun-bundler (425⭐)

| Bundler Feature | Our Implementation | File | Status |
|---|---|---|---|
| Profile Generation (20 wallets) | ✅ Complete | profile_gen.py | Done |
| LUT Program (80% gas reduction) | ⬜ Plan | lut_manager.py | New |
| Stealth Launch Mode | ⬜ Implement | cli.py Menu 4 | New |
| Momentum Launch Mode | ⬜ Implement | cli.py Menu 4 | New |
| Anti-Sniper Launch Mode | ⬜ Implement | cli.py Menu 4 | New |
| Wallet Warmup | ⬜ Plan | wallet_warmup.py | New |
| Dynamic Supply Distribution | ⬜ Implement | liquidity.py | New |
| Smart Sell Strategies | ⬜ Plan | strategies.py | New |
| 99.7% Success Rate | ⬜ Target | - | Goal |

### From DangerAcorn/pumpfun-tools (313⭐)

| Bundler Feature | Our Implementation | File | Status |
|---|---|---|---|
| <10ms tx speed | ⬜ Optimize | trading_engine.py | Improvement |
| Zero key storage | ✅ Already done | wallet_utils.js | Done |
| Modular dashboard | ✅ Already done | cli.py | Done |
| Private node support | ⬜ Add | config.py | New |

### From hexnome/pumpfun-raydium-cli-tools (176⭐)

| Bundler Feature | Our Implementation | File | Status |
|---|---|---|---|
| Volume booster (buy+sell in one tx) | ⬜ Plan | bundle_bot.py | New |
| Token creation with metadata | ⬜ Future | - | Future |
| DCA bots | 📋 Plan | strategies_advanced.py | Week 3 |
| Limit orders | 📋 Plan | trading_engine.py | Week 2 |
| PumpSwap AMM support | ⬜ Add | liquidity.py | New |

### From coffellas-cto/Solana-Copy-Trading-Bot (410⭐)

| Bundler Feature | Our Implementation | File | Status |
|---|---|---|---|
| Copy trading | 📋 Plan | telegram_bot.py | Week 2 |
| Shred stream (real-time data) | ⬜ Add | onchain_monitor.py | Improvement |
| Raydium sniper | ✅ Already done | trading_engine.py | Done |
| Jito bundles | ✅ Already done | bundle_bot.py | Done |

### Three Commas Features → Our Bot

| Three Commas Feature | Our Implementation | File | Status |
|---|---|---|---|
| SmartTrade (buy/sell/TP/SL) | 📋 Plan | telegram_bot.py | Week 2 |
| DCA Bots | 📋 Plan | strategies_advanced.py | Week 3 |
| Trailing Buy/Sell | 📋 Plan | strategies.py | Week 2 |
| Stop Loss / Take Profit | ✅ Already done | strategies.py | Done |
| Sniping | ✅ Already done | trading_engine.py | Done |
| Multi-wallet support | ✅ Already done | bundle_bot.py | Done |
| TradingView webhooks | 📋 Plan | telegram_bot.py | Week 6 |
| Preset configurations | 📋 Plan | config/three_commas_mappings.json | Week 6 |
| Bot settings via UI | 📋 Plan | telegram_bot.py | Week 3 |

---

## Execution Priority

### Immediate (Today)
1. Fix the `strategy="SMALL"` display bug in wash trading demo
2. Create file organization plan (move to structured directories)
3. Create `telegram_bot.py` skeleton with urllib API wrapper
4. Add `TELEGRAM_BOT_TOKEN` to `.env.example`

### Short Term (Week 1)
1. Complete Telegram bot core (polling, commands, keyboards)
2. Migrate to organized file structure
3. Add Telegram Bot option to CLI Menu 10
4. Update launch_dashboard.sh with Telegram bot pane

### Medium Term (Week 2-4)
1. Implement all trading commands (/buy, /sell, /snipe, /dca)
2. Implement strategy & settings management
3. Implement background mode (OWL)
4. Add Three Commas preset profiles
5. Create setup scripts (setup_termux.sh, setup_proot.sh)

### Long Term (Week 5-8)
1. LUT Manager module
2. Wallet Warmup system
3. Phantom wallet integration
4. Advanced features (honeypot detection, sentiment, etc.)
5. Full integration testing + verification

---

## Risk Assessment

### High Risk
- **proot-distro networking**: While network access is confirmed, some APIs may have issues with proot's network stack. Mitigation: Test each API endpoint individually.
- **Telegram rate limits**: 30 messages/second limit. Mitigation: Add rate limiting to bot.
- **pump.fun API changes**: The API may change without notice. Mitigation: Add error handling and fallback.

### Medium Risk
- **File migration breaking imports**: Moving files will break existing imports. Mitigation: Update all import paths, test thoroughly.
- **tmux dashboard complexity**: 4-pane layout with multiple services may be fragile. Mitigation: Add process monitoring and auto-restart.

### Low Risk
- **No pip install**: We use urllib for Telegram, stdlib for everything else. No dependency issues.
- **No external telemetry**: All data stays local. No privacy concerns.

---

## Verification Plan

### For Each New Feature:
1. **Syntax check**: `python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"`
2. **Import test**: `python3 -c "import file; print(dir(file))"`
3. **Function test**: Call each public function with test data
4. **CLI integration**: Test through CLI menu system
5. **Telegram test**: Send test messages to bot, verify responses
6. **Security scan**: No `eval()`, no base64 payloads, no hidden addresses
7. **Ad-hoc verification**: Full 30-point check at completion

### Testing Commands
```bash
# Syntax check all Python files
find . -name "*.py" -exec python3 -c "import py_compile; py_compile.compile('{}', doraise=True)" \;

# Security scan
grep -rn "eval\|base64\|Function(" *.py --include="*.py" | grep -v __pycache__ | grep -v "# " | grep -v "No eval"

# Import test
python3 -c "from telegram_bot import TelegramBot; print('OK')"

# CLI test
volbot --test  # runs ad-hoc verification

# Telegram bot test (with real token)
python3 -c "from telegram_bot import TelegramBot; b = TelegramBot('YOUR_TOKEN'); print(b.api('getMe'))"
```
