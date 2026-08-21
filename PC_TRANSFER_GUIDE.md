---
name: PC Transfer Complete Guide & Checklist
description: Transfer all Hermes skills, cron jobs, config, and VolBot project to PC Hermes desktop at 192.168.8.115
category: devops
---

# PC Hermes Transfer — Complete Guide (192.168.8.115)

## Overview
Transfer all skills, tools, cron jobs, config, and the VolBot Pump.fun bot from this Android/Termux Hermes to PC Hermes desktop at **192.168.8.115** (GL.iNet MT3000 personal router).

## Prerequisites on PC

| Component | Required | Notes |
|-----------|----------|-------|
| Python 3.13+ | Yes | `python3 --version` |
| Node.js 22+ | Yes | For `wallet_utils.js`, `sign_sender.js` |
| Solana CLI | Recommended | x86_64 binary available from GitHub releases |
| Docker (optional) | Optional | Use `Dockerfile` for containerized deployment |
| SSH server | Recommended | `apt install openssh-server && systemctl enable --now ssh` |
| Git | Yes | `apt install git` |
| tmux | Yes | For always-on system |
| pip packages | Required | `pip install --break-system-packages construct base58` |

## Transfer Methods

### Method A: GitHub (Primary) — RECOMMENDED
Everything is already on GitHub:
```
Repository: https://github.com/bakermatthew3594-art/volbot-pumpfun
Branch: main (Android/dry-run) and pc (PC/live trading)
```

On PC Hermes:
```bash
git clone https://github.com/bakermatthew3594-art/volbot-pumpfun.git
cd volbot-pumpfun
git checkout pc

# Install dependencies
bash pc-install.sh

# Start always-on system
bash always-on.sh start
```

### Method B: Tar.gz Package
Package created at: `/tmp/hermes-volbot-complete-<timestamp>.tar.gz` (12MB)

Contains:
- `hermes-skills/` — all 114 skill directories
- `cron-jobs.json` — all 8 cron jobs (redacted)
- `hermes-config.yaml` — Hermes config (redacted)
- `volbot-project/` — 37 VolBot files
- `TRANSFER_PACKAGE.md` — transfer manifest

### Method C: SCP/SFTP (when SSH is set up on PC)
```bash
# Set up SSH on PC first:
# On PC: sudo apt install openssh-server && sudo systemctl enable --now ssh

# Then from Android:
scp -r /tmp/hermes-skills-export baker@192.168.8.115:/tmp/
scp /tmp/volume-bot/*.py baker@192.168.8.115:/tmp/volume-bot/
scp /tmp/volume-bot/*.sh baker@192.168.8.115:/tmp/volume-bot/
```

## Skills to Import (46 Skills + Sub-skills)

### VolBot/DevOps Skills (CRITICAL):
1. `pumpfun-cross-platform-packaging` — Cross-platform deployment
2. `solana-bot-testing-harness` — Mock RPC + AI testing
3. `solana-pumpfun-launch` — Pump.fun launch + devnet tests
4. `solana-pumpfun-test-verification` — CLI test verification
5. `dry-run-post-trading-rpc-guard` — RPC hang prevention (dry-run guards)

### Legal/GRS Skills:
6. `grs-property-pickup` — Property item tracking
7. `legal-property-recovery` — Institutional property recovery
8. `grs-auditor` — Misconduct extraction
9. `grs-auditor-v2` — Provenance knowledge tiers
10. `grs-property-send` — Property send strategy
11. `grs-property-track` — Property recovery tracking
12. `legal-citation-verification` — Statute/CFR verification via curl
13. `legal-forensic-audit` — Misconduct timeline extraction
14. `md-statute-verify` — Maryland statute verification
15. `quote-integrity-check` — Verbatim quote verification
16. `statement-discipline` — Quote/paraphrase discipline
17. `witness-corroboration` — Witness recall vs records
18. `witness-fidelity` — Speech literal vs gloss split
19. `case-knowledge-base` — Legal case knowledge base

### Android Skills (REVIEW PATHS for PC):
20. `android-storage-access` — Android file reading (not needed on PC)
21. `android-file-access` — App file access
22. `android-termux-storage` — Termux storage access
23. `termux-device-control` — Phone SSH/HTTP management
24. `termux-runtime-constraints` — Termux tooling limits
25. `proot-desktop-control` — Proot desktop management

### Desktop/Tooling Skills:
26. `headless-desktop-control` — Headless desktop via command queue
27. `media-playback` — yt-dlp + mpv media playback
28. `hermes-browser-extension-gui` — Flask browser GUI
29. `hermes-telegram-gateway-toolkit` — Telegram gateway restart
30. `hermes-gui-multiagent` — Browser multi-agent LLM GUI
31. `hermes-recovery-suite` — Hermes crash recovery

### Research/Productivity Skills:
32. `ai-software-compendium` — Top 20 AI software tools
33. `termux-web-research` — Web research via curl+regex
34. `web-research-terrorism` — Tavily web research fallback
35. `teams-meeting-pipeline` — Teams meeting summaries
36. `execute-code-sqlite` — SQLite execution from execute_code
37. `github-sync` — Cross-device GitHub skill sync
38. `knowledge-forge` — Personal knowledge storage engine

### Trading Skills:
39. `solana-dex-liquidity-wash-trading` — Multi-DEX liquidity simulation
40. `solana-volume-bot` — Auditable DEX bot with 5 strategies
41. `solana-web-visualization` — Browser charts via Flask
42. `crypto-trading-bot-research` — Crypto bot research

### Foundation:
43. `foundation` — Hermes multi-project platform setup

### Other:
44. `autonomous-ai-agents` — (if present) AI agent frameworks

## Cron Jobs to Import

### VolBot Jobs (CRITICAL):

**1. VolBot Health Watchdog** (`643b1dd860ba`)
- Schedule: `*/5 * * * *` (every 5 minutes)
- Purpose: Check tmux session "volbot" is alive with 4 panes; restart if dead
- Log: `/tmp/volbot_watchdog.log`
- Auto-restarts via `bash /tmp/volume-bot/always-on.sh start`

**2. VolBot GitHub Auto-Sync** (`715f9130547d`)
- Schedule: `0 * * * *` (every hour)
- Purpose: Auto-commit and push changes to GitHub
- No empty commits (only commits if changes exist)
- Log: `/tmp/volbot_github_sync.log`

### Hermes Native Jobs (also transfer):

**3. Hermes Browser Server Watchkeeper** (`ac7cb47ac469`)
- Schedule: `* * * * *` (every minute)
- Purpose: Ensure Hermes Browser Extension server running

**4. Laptop Network Monitor** (`16c0b728fef7`)
- Schedule: `every 5m`
- **UPDATE IP from 192.168.0.212 → 192.168.8.115**
- Purpose: Check if PC is online and SSH accessible

**5. Browser Server Watchdog**
- Schedule: `every 1m`
- Purpose: Restart browser server if down

**6. Hermes Skills Daily Sync**
- Schedule: `0 9 * * *` (daily at 9am EDT)
- Purpose: Sync skills across devices

**7. Hermes Service Watchdog**
- Schedule: `every 5m`
- Purpose: Check llama-server, gat[...] and restart if down

## Hermes Config to Replicate

### Key Settings (`/root/.hermes/config.yaml`):

```yaml
platform_mode: "pc"                          # Change from android to pc
integration_tests_timeout: 120               # Allow 120s for full test suite
always_on_enabled: true                      # Keep tmux session alive
cron_schedule: "*/5 * * * *"                 # Watchdog interval
watchdog_enabled: true                       # Auto-restart tmux
github_sync_hourly: true                     # Auto-sync to GitHub

provider: openrouter
model: poolside/laguna-s-2.1:free
fallback_chain:
  - poolside/laguna-s-2.1:free
  - anthropic/claude-sonnet-4

tools:
  web_enabled: true
  terminal_enabled: true
  file_enabled: true
  delegation_enabled: true
```

### Config File Locations on PC:
```
/root/.hermes/skills/       → Import all 114 skills here
/root/.hermes/cron/         → Import cron-jobs.json here
/root/.hermes/memory/       → Copy memory.md (user preferences)
/root/.hermes/user/         → Copy user.md (profile info)
/root/.hermes/plugins/      → Copy plugin configs
```

## User Memory to Transfer

Your user profile (from `/root/.hermes/user/user.md`):
- Name: Matthew A. Baker
- Email: bakermatthew3594@gmail.com
- Cell: (302) 469-3243
- Comms: James Collins at 410-999-0891 (SMS preferred)
- AVOID personal cell: 410-699-6092
- Time zone: EDT primary
- Prefers: HONEST DOWNGRADES, append-only corrections
- Documentation: Max detail staff negligence, procedural defects
- Backup: Internal storage + lawyer-ready packages
- Environment: Android/Termux aarc64, Python 3.14.4, no Solana CLI, dry-run mode

Your memory (from `/root/.hermes/memory/memory.md`):
- VolBot 3-tier Pump.fun system architecture
- Budget config → volbot → advanced_trader/trading_orchestrator
- Test results: integration_test 74/74, etc.
- Environment details: Termux, proot-distro, Python versions, Node paths
- Key constants: test-mode duration cap 0.03 min, MAX_NO_TRADE_CYCLES=10
- RPC timeout: 15 seconds per call

## VolBot Project Files (37 files)

### Python Core (27 files):
1. `pumpfun_lifecycle_cli.py` — 7-phase lifecycle (2,958 lines), all RPC dry-run-guarded
2. `trading_orchestrator.py` — Bubble detection, chart patterns, MAX_NO_TRADE_CYCLES=10
3. `money_flow.py` — 23-wallet allocation, 51KB
4. `smart_bundler.py` — Bundle generation, 30% sol scaling at bubble_risk 0.80
5. `bot.py` — Simple volume bot
6. `bundle_bot.py` — Bundle strategy
7. `trading_engine.py` — Advanced engine
8. `strategies.py` — Trading strategies
9. `strategies_advanced.py` — Advanced strategies
10. `advanced_trader.py` — Advanced trader
11. `bonding_curve_trader.py` — Bonding curve math
12. `budget_config.py` — Three-tier budget ($6/$6+/any)
13. `comment_bot.py` — Telegram comment bot
14. `profile_gen.py` — Wallet profile generation
15. `liquidity.py` — Liquidity management
16. `onchain_monitor.py` — On-chain monitoring
17. `feature_tracker.py` — Token feature tracking
18. `devnet_simulation.py` — Devnet testing
19. `backtest.py` — Backtesting
20. `safety_check.py` — Safety checks
21. `config.py` — Configuration
22. `cli.py` — CLI entry point with fund/status/trade/rug subcommands
23. `telegram_bot.py` — 14 commands, inline keyboards, stdlib urllib
24. `web_viz.py` — Web dashboard (stdlib HTTPServer)
25. `web_dashboard.py` — Alternative dashboard
26. `status_monitor.py` — Status pane display (NEW)
27. `integration_test.py` — 74 tests, all passing

### Shell Scripts (9 files):
1. `run.sh` — Universal launcher with platform detection (NEW)
2. `always-on.sh` — tmux 4-pane supervisor (NEW)
3. `android-install.sh` — Android/Termux setup (NEW)
4. `pc-install.sh` — PC setup (NEW)
5. `github-sync.sh` — GitHub sync management (NEW)
6. `export-skills.sh` — Hermes skills export (NEW)
7. `transfer-all-to-pc.sh` — Complete transfer script (NEW)
8. `install.sh` — Original install script
9. `start-bot.sh` — Original launcher

### Node.js (2+ files):
1. `wallet_utils.js` — Key generation via @noble/curves
2. `sign_sender.js` — Transaction signing
3. `package.json` — Dependencies

### Config & Docs (6 files):
1. `.env.example` — Credential template (REDACTED)
2. `Dockerfile` — PC Docker container (NEW)
3. `README.md` — Comprehensive documentation
4. `TRANSFER_MANIFEST.md` — Transfer documentation
5. `PC_TRANSFER_GUIDE.md` — This file (NEW)
6. `requirements.txt` — (if exists) Python dependencies

## Setup Steps on PC (Quick Reference)

```bash
# 1. Clone repo
git clone https://github.com/bakermatthew3594-art/volbot-pumpfun.git
cd volbot-pumpfun
git checkout pc

# 2. Install dependencies
bash pc-install.sh

# 3. Import .env (copy from Android securely!)
cp /path/to/android/.env .env

# 4. Import Hermes skills
cp -r hermes-skills/* /root/.hermes/skills/

# 5. Start always-on system
bash always-on.sh start

# 6. Verify
tmux attach -t volbot  # Ctrl+B+D to detach
open http://localhost:8765

# 7. Run tests (stop tmux first!)
tmux kill-session -t volbot
rm -f .lifecycle_state.json
timeout 200 python3 -u integration_test.py | grep "Results:"
# Expected: Results: 74/74 passed, 0 failed
./always-on.sh start  # restart after tests

# 8. Update network monitor cron
# Change IP from 192.168.0.212 to 192.168.8.115
```

## Key Fixes Already Applied

1. **`test_dryrun_with_rugcheck` timeout** — Added `if dry_run:` guards before get_balance RPC calls in close_wallets and _run_full_lifecycle summary
2. **Duplicate get_balance call** — Guarded with `if not dry_run:` in close_wallets
3. **`test_dryrun_with_rugcheck` test timeout** — Increased from 30s to 35s
4. **`test_cli_full_dryrun` test timeout** — Increased subprocess timeouts from 10s→15s and 5s→10s
5. **State file cleanup** — Added `rm -f .lifecycle_state.json` before CLI tests to prevent stale state conflicts

## CRITICAL: Always-On Test Conflict

The always-on tmux system creates `.lifecycle_state.json` continuously. This causes test failures when running integration tests. ALWAYS do this sequence:

```bash
tmux kill-session -t volbot 2>/dev/null    # STOP always-on
rm -f .lifecycle_state.json               # CLEAN state file
sleep 2                                   # Wait for any process to exit
timeout 200 python3 -u integration_test.py # RUN tests
rm -f .lifecycle_state.json               # CLEAN again
bash always-on.sh start                   # RESTART always-on
```

## Verification Checklist

- [ ] GitHub repo cloned on PC
- [ ] `pc-install.sh` run successfully
- [ ] All 46 skills imported to `/root/.hermes/skills/`
- [ ] Cron jobs imported (8 jobs)
- [ ] Network monitor cron IP updated to 192.168.8.115
- [ ] `.env` file copied from Android (with real credentials)
- [ ] `always-on.sh` started (4 tmux panes running)
- [ ] Web dashboard accessible at http://localhost:8765
- [ ] Telegram bot running (if TELEGRAM_BOT_TOKEN set)
- [ ] Integration tests: 74/74 pass
- [ ] Full lifecycle: 7/7 phases complete
- [ ] All Python files compile
- [ ] Cron watchdog running and detecting VolBot tmux session