# VolBot Pump.fun Launch Trading Bot

A complete, self-contained trading bot for Pump.fun token launches with Telegram control,
web dashboard, and always-on tmux deployment. Zero runtime dependencies — just Python 3
and Node.js.

## Quick Start

```bash
# Android (Termux/proot-distro Ubuntu)
bash android-install.sh

# PC (Linux/macOS/WSL)
bash pc-install.sh

# Start always-on system (bot + dashboard + telegram + status monitor)
./always-on.sh start

# Run a single dry-run lifecycle test
./run.sh --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode

# Run integration tests (74 tests)
./run.sh test
```

## Architecture

### Three-Tier Budget System
- **Tier 1 (5 wallets, $6)**: Minimal budget, dry-run default
- **Tier 2 (20+ wallets, $6)**: Aggressive wallet distribution
- **Tier 3 (any budget)**: Customizable

### Bot Engine (7 Phases)
1. **Create** — Deploy token on Pump.fun (devnet mock in dry-run)
2. **Fund** — Distribute SOL to bot wallets from creator wallet
3. **Buy** — Initial buy sequence across all wallets
4. **Trade** — Active trading with bubble risk detection, natural buy response, stop-loss
5. **Take-Profit** — Cascade profit-taking as MC multiplies (2x → 3x → 5x → 10x)
6. **Cash-Out** — Convert all tokens back to SOL
7. **Close** — Sweep all SOL back to creator wallet

### Always-On System (tmux 4-pane layout)
```
┌──────────────┬──────────────┐
│  Bot Engine  │  Web Dashbrd │
├──────────────┼──────────────┤
│  Telegram    │  Status      │
│  Bot         │  Monitor     │
└──────────────┴──────────────┘
```
- **Pane 0 (Bot Engine):** Runs the trading lifecycle
- **Pane 1 (Web Dashboard):** Live charts, wallet balances, trade log
- **Pane 2 (Status Monitor):** Phase tracking and health checks
- **Pane 3 (Telegram Bot):** Command interface (optional)

### Watchdog
A cron job runs every 5 minutes to verify the tmux session is alive and restart it if needed.

## Platform-Specific Notes

### Android (Termux/proot-distro)
- Uses `--dry-run` mode by default (no real Solana RPC calls)
- Node.js 22 available at `/tmp/node-v22.11.0-linux-arm64/`
- Python 3.13 with `construct` and `base58` packages installed

### PC (Linux/macOS/WSL)
- Supports real Solana RPC calls and live trading
- Solana CLI can be installed separately for mainnet deployment
- Docker support via Dockerfile

## Files

### Core Bot
- `pumpfun_lifecycle_cli.py` — Full 7-phase lifecycle CLI
- `trading_orchestrator.py` — Trading simulation with bubble detection
- `money_flow.py` — Money flow tracking and wallet allocation
- `smart_bundler.py` — Bundle generation with bubble risk scaling

### Control Interfaces
- `telegram_bot.py` — Telegram bot (14 commands, inline keyboards)
- `web_viz.py` — Web dashboard with live charts
- `web_dashboard.py` — Alternative dashboard

### Node.js Helpers
- `wallet_utils.js` — Wallet key generation
- `sign_sender.js` — Transaction signing

### Deployment
- `run.sh` — Universal launcher (auto-detects platform)
- `always-on.sh` — tmux always-on supervisor
- `android-install.sh` — Android/Termux setup
- `pc-install.sh` — PC setup
- `github-sync.sh` — GitHub backup and sync
- `status_monitor.py` — Status monitor pane script

### Testing
- `integration_test.py` — 74 tests (all passing)
- `smart_bundler.py` — 6/6 tests
- `money_flow.py` — 4/4 tests
- `trading_orchestrator.py` — 4/4 tests

## CLI Usage

```bash
# Dry run (no real transactions) — safe for testing
./run.sh --devnet --dry-run --full --budget-usd 6 --wallets 5 --auto --test-mode

# Mainnet live trading (requires funded wallet)
./run.sh --mainnet --full --budget-usd 20 --wallets 20 --auto

# Individual phases
./run.sh --create --name MYCOIN --symbol MCO
./run.sh --fund
./run.sh --buy
./run.sh --trade --trade-minutes 10
./run.sh --take-profit --target-mc 10x
./run.sh --cash-out
./run.sh --close
```

## Telegram Commands
- `/trade` — Start trading session
- `/status` — Show current status
- `/wallet` — Wallet operations
- `/rug` — RugCheck scan
- `/charts` — View price chart
- `/tp` — Take-profit configuration
- `/sl` — Set stop-loss
- `/balance` — Check wallet balances
- `/help` — Show all commands

## Security

- No base64 obfuscation
- No hidden withdrawal functions
- No external telemetry/analytics
- No hardcoded private keys
- Local signing only — private keys never leave your machine
- All RPC calls go to YOUR configured endpoint

## Legal

Creating artificial trading volume is market manipulation. This tool is provided for
research purposes only. Use only on tokens you control.
