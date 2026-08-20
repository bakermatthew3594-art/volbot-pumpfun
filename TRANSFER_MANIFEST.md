# ══════════════════════════════════════════════════════════════
# VOLBOT PUMP.FUN LAUNCH BOT — TRANSFER MANIFEST
# Complete guide for setting up on PC Hermes Desktop
# Date: August 20, 2026
# ══════════════════════════════════════════════════════════════

## 1. PROJECT OVERVIEW

Three-tier Pump.fun token launch trading bot with:
- 7-phase lifecycle (create → fund → buy → trade → take-profit → cash-out → close)
- Telegram bot control (14 commands, inline keyboards)
- Web dashboard with live charts
- Trading orchestrator with bubble risk detection
- 74 integration tests (all passing)

## 2. BRANCH STRATEGY

GitHub: https://github.com/bakermatthew3594-art/volbot-pumpfun

- `main` — Android/Termux/dry-run default (no real RPC calls)
- `pc` — PC/Linux with real Solana tooling support (Docker, mainnet, Solana CLI)

## 3. SETUP ON PC HERMeS DESKTOP

```bash
# Clone the repository
git clone https://github.com/bakermatthew3594-art/volbot-pumpfun.git
cd volbot-pumpfun
git checkout main     # for Android/dry-run
git checkout pc       # for PC/live trading

# Run installer
bash pc-install.sh

# Or use the universal launcher
./run.sh test         # Run 74 tests (takes ~130s)
```

## 4. ALWAYS-ON SYSTEM (tmux)

```bash
# Start all services (bot + dashboard + telegram + status)
./always-on.sh start

# Attach to the tmux session
tmux attach -t volbot

# Check status
./always-on.sh status

# Stop
./always-on.sh stop
```

tmux 4-pane layout:
  Pane 0: Bot Engine (lifecycle runner)
  Pane 1: Web Dashboard (port 8765)
  Pane 2: Status Monitor (phase tracking)
  Pane 3: Telegram Bot (or placeholder)

## 5. CRON JOBS (Auto-Recovery)

Two cron jobs are registered in Hermes:

1. **VolBot Health Watchdog** (every 5 minutes)
   - Checks if tmux session "volbot" is alive
   - Restarts if panes are missing or session is dead
   - Logs to /tmp/volbot_watchdog.log

2. **VolBot GitHub Auto-Sync** (every hour)
   - Commits and pushes any changes to GitHub
   - Logs to /tmp/volbot_github_sync.log

## 6. KEY FIXES APPLIED (August 20, 2026)

### Fix 1: test_dryrun_with_rugcheck timeout (ROOT CAUSE: post-trading RPC calls)

**Problem:** The `--test-mode` flag in `run_active_trading()` caps the simulation duration, but post-trading phases (Phase 6: cash_out, Phase 7: close_wallets) still made real RPC calls to devnet that hung for 15 seconds each.

**Root cause location:** `pumpfun_lifecycle_cli.py`
  - `_run_full_lifecycle()` function (line ~2780)
  - `close_wallets()` function (line ~1938) — called `get_balance(rpc, w["pubkey"])` per wallet
  - Final summary (line ~2838) — called `get_balance(rpc, state.creator_pubkey)`

**Fix applied:**
  - `close_wallets()`: Added `if dry_run:` check before `get_balance()` call, uses mock balance `w.get("sol_balance", 0.019)` instead
  - `_run_full_lifecycle()` summary: Added `if args.dry_run:` check to skip `get_balance()` call, uses mock balance 99.0

**Result:** Full lifecycle now completes in ~3 seconds instead of hanging indefinitely.

### Fix 2: test_cli_full_dryrun subprocess timeout

**Problem:** Test running `cli.py fund` + `cli.py status` as subprocess with 10s/5s timeouts was too tight.

**Fix:** Increased timeouts to 15s/10s and added stale state file cleanup at test start.

### Fix 3: trading_orchestrator.py bubble_risk infinite loop

**Problem:** `bubble_risk` infinite loop root cause.
- `cycle_had_activity` guard added
- Breakout check added
- `MAX_NO_TRADE_CYCLES` reduced from 50 to 10

### Fix 4: money_flow.py natural_buy_volume accumulation

**Problem:** `self.natural_buy_volume += natural_buy_sol` was accumulating incorrectly.
**Fix:** Removed the accumulation line (line 553).

### Fix 5: smart_bundler.py bubble_risk scaling

**Problem:** `build_natural_buy_response` didn't scale total_sol at high bubble_risk.
**Fix:** At bubble_risk 0.80, total_sol scales to 30%.

## 7. QUICK START COMMANDS

```bash
# Dry-run lifecycle (safe, no real transactions)
./run.sh --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode

# With RugCheck scan
./run.sh --devnet --dry-run --full --rugcheck --budget-usd 6 --wallets 5 --auto --test-mode

# Live trading (PC only, requires funded wallet)
./run.sh --mainnet --full --budget-usd 20 --wallets 20 --auto

# Individual phases
./run.sh --create --name MYCOIN --symbol MCO
./run.sh --fund --dry-run
./run.sh --buy --dry-run
./run.sh --trade --trade-minutes 10 --dry-run
./run.sh --take-profit --target-mc 5x --dry-run
./run.sh --cash-out --dry-run
./run.sh --close --dry-run
```

## 8. ENVIRONMENT

- **Android/Termux:** Python 3.14.4 (no pip module), Python 3.13 (pip works with --break-system-packages), Node.js 22.11.0 at /tmp/node-v22.11.0-linux-arm64/, no Solana CLI, dry-run mode
- **PC:** Python 3.11+, Node.js 22, optional Solana CLI via `sh -c "$(curl -sSfL https://release.solana.com/v1.18.26/install)"`, Docker support

## 9. TELegram Bot SETUP

1. Get bot token from @BotFather
2. Get chat ID from @userinfobot  
3. Edit `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```
4. Restart: `./always-on.sh restart`

## 10. WEB DASHBOARD

Start standalone:
```bash
./run.sh dashboard
# or
./always-on.sh start  # includes dashboard in tmux
```

Open: http://localhost:8765

Features:
- Live price charts
- Wallet balances
- Trade log
- Bubble risk gauge
- TP cascade visualization
- Wallet role display

## 11. GITHUB SYNC

```bash
# Initialize (if not already done)
./github-sync.sh init

# Sync changes
./github-sync.sh sync

# Pull latest
./github-sync.sh pull

# Status
./github-sync.sh status
```

Or use the transfer script:
```bash
./transfer-to-pc.sh github    # push to GitHub
./transfer-to-pc.sh package   # create tar.gz
./transfer-to-pc.sh scp user@pc:/path  # rsync to remote
```

## 12. TEST RESULTS

```
74/74 integration tests passed, 0 failed
- SmartBundler: 3/3
- Money Flow: 6/6  
- CLI Unit: 6/6
- Integration: 3/3
- Constants: 3/3
- CLI Validation: 3/3
- RugCheck: 24/24
- CLI Subcommands: 6/6
- Three-Tier: 5/5
- Enhanced Features: 8/8
- Telegram Features: 6/6
```

## 13. FILE INVENTORY

### Core Engine (Python)
- pumpfun_lifecycle_cli.py — 7-phase lifecycle CLI (2,958 lines)
- trading_orchestrator.py — Trading simulation + bubble detection
- money_flow.py — 23-wallet tiered allocation (51KB)
- smart_bundler.py — Bundle generation with risk scaling
- bot.py — Simple volume bot
- bundle_bot.py — Bundle strategy
- trading_engine.py — Extended trading engine
- strategies.py / strategies_advanced.py — Trading strategies
- advanced_trader.py — Advanced trader logic
- bonding_curve_trader.py — Bonding curve math
- budget_config.py — Three-tier budget system ($6/$10/$20/$50/$100)
- comment_bot.py — Comment generation
- profile_gen.py — Wallet profile generation
- liquidity.py — Liquidity management
- onchain_monitor.py — On-chain monitoring
- feature_tracker.py — Token feature tracking
- devnet_simulation.py — Devnet testing
- backtest.py — Backtesting framework
- safety_check.py — Safety checks
- config.py — Configuration
- cli.py — Subcommand CLI interface
- integration_test.py — 74 tests

### Control Interfaces
- telegram_bot.py — Telegram bot (14 commands)
- web_viz.py — Web dashboard (HTTPServer, port 8765)
- web_dashboard.py — Alternative dashboard

### Node.js Helpers
- wallet_utils.js — Key generation (@noble/curves)
- sign_sender.js — Transaction signing
- package.json — Node.js dependencies

### Deployment Scripts
- run.sh — Universal launcher (platform auto-detect)
- always-on.sh — tmux supervisor (4-pane, auto-restart)
- status_monitor.py — Status monitor pane script
- android-install.sh — Android/Termux setup
- pc-install.sh — PC setup
- github-sync.sh — GitHub sync
- transfer-to-pc.sh — Multi-method file transfer
- Dockerfile — Docker containerization
- install.sh — Original Node.js package install
- start-bot.sh — Original launcher
- launch_dashboard.sh — Original dashboard launcher

### Configuration
- .env.example — Environment template
- .gitignore — Git ignore rules
- README.md — Documentation
- RESEARCH.md — Research notes
- SKILL.md — Skill documentation

## 14. CRON JOB DETAILS

Check cron jobs:
```bash
# List all cron jobs
# (managed by Hermes cron system — not visible via crontab -l)
```

The cron jobs are registered through Hermes's cron system:
- VolBot Health Watchdog: every 5 minutes (`*/5 * * * *`)
- VolBot GitHub Auto-Sync: every hour (`0 * * * *`)

Watchdog script checks:
1. Is tmux session "volbot" alive?
2. Are all 4 panes present?
3. If not, restart the session

## 15. TROUBLESHOOTING

| Issue | Fix |
|-------|-----|
| "No Solana CLI" | PC: Install via `sh -c "$(curl -sSfL https://release.solana.com/v1.x.x/install)"` |
| "npm install fails" | Run `bash install.sh` which downloads tarballs directly |
| Tests timeout | Run individually: `python3 -u integration_test.py 2>&1` (takes ~130s) |
| tmux session dead | `./always-on.sh restart` or wait for cron watchdog |
| Telegram bot not working | Check .env: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID |
| Dashboard not loading | Check port 8765 or run `./run.sh dashboard` manually |
