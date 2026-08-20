---
name: solana-volume-bot
description: Auditable Solana DEX bot with 5 trading strategies, $20 budget.
category: devops
---

# Solana Volume Bot Skill

Use when building a lightweight automated trading/volume bot for Solana DEXes.
This skill provides a complete, security-audited bot with deterministic sub-wallet
generation, Jupiter API integration, local-only signing, and 5 trading strategies.
Total cost: ~$18 SOL (within $20 budget).

Enhanced features include 8 advanced strategies, multi-wallet bundle botting with
Jito MEV, 4 money tiers ($5-$20), backtesting engine, and on-chain monitoring.

## TRIGGER CONDITIONS
- Need to automate DEX swaps on Solana (pump.fun, Raydium, Orca, etc.)
- Budget constrained (under $20 for fees + gas)
- Requires full auditability of all code
- Prefer Python orchestrator + minimal Node.js crypto helper

## QUICK START

### 1. Install dependencies (one-time)
```bash
cd /tmp/volume-bot
bash install.sh
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env:
#   PRIVATE_KEY=<base58_seed_from_step_3>
#   TOKEN_MINT=<target_token_mint>
#   RPC_ENDPOINT=https://api.mainnet-beta.solana.com
```

### 3. Generate main wallet
```bash
cd /tmp/volume-bot
node wallet_utils.js generate
# Copy seed_b58 to PRIVATE_KEY in .env
```

### 4. Run demo
```bash
python3 bot.py
# Shows cost estimate and generates sub-wallets without trading
```

### 5. Live trading
```bash
python3 bot.py --live
# Requires SOL in main wallet for gas + buy capital
```

## TRADING STRATEGIES

The bot includes 5 built-in strategies in `strategies.py`:

### 1. Dip Buy Strategy
Buy when price drops below SMA by threshold, sell at take profit or trailing stop.
```python
from strategies import DipBuyStrategy, PriceBuffer
buf = PriceBuffer(50)
buf.add(100.0); buf.add(95.0)  # Simulate price dip
strategy = DipBuyStrategy(buf, dip_threshold=0.03, take_profit_threshold=0.05)
signal, confidence = strategy.signal()
```

### 2. Trailing Stop Loss
Buy on EMA crossover, sells when price drops X% from peak.
```python
from strategies import TrailingStopStrategy
strategy = TrailingStopStrategy(buf, trailing_pct=0.05)
```

### 3. Mean Reversion
Buy when oversold (RSI < 30) near Bollinger Lower Band, sell when overbought.
```python
from strategies import MeanReversionStrategy
strategy = MeanReversionStrategy(buf, rsi_buy=30, rsi_sell=70)
```

### 4. All-Coin Scanner
Discovers trending tokens via DexScreener API + Jupiter price feeds.
```python
from strategies import AllCoinScanner
scanner = AllCoinScanner(min_liquidity_usd=50000)
tokens = scanner.scan_trending()
```

### 5. Position Manager
Risk management with position sizing, stop losses, and daily loss limits.
```python
from strategies import PositionManager
pm = PositionManager(total_capital_usd=100)
size = pm.calculate_size(current_price)  # Max 20% of capital per trade
```

### Strategy Factory
```python
from strategies import create_strategy
strategy = create_strategy("dip_buy", price_buffer, dip_threshold=0.03)
```

## FILES

| File | Purpose | Language | Lines |
|---|---|---|---|
| `bot.py` | Main orchestrator | Python 3 | ~400 |
| `wallet_utils.js` | Key generation via @noble/curves | Node.js | ~100 |
| `sign_sender.js` | Transaction signing + sending + Jito tips | Node.js | ~350 |
| `trading_engine.py` | Jupiter/DexScreener API integration | Python 3 | ~400 |
| `strategies.py` | 5 trading strategies + PositionManager | Python 3 | ~520 |
| `strategies_advanced.py` | 6 advanced strategies + risk mgmt | Python 3 | ~400 |
| `bundle_bot.py` | Multi-wallet bundle bot (Jito MEV) | Python 3 | ~340 |
| `backtest.py` | Backtesting engine + mock data generators | Python 3 | ~390 |
| `config.py` | Money tiers + bundle configs | Python 3 | ~270 |
| `onchain_monitor.py` | RPC monitoring, portfolio tracking | Python 3 | ~270 |
| `install.sh` | Dependency installer (curl+tar) | Bash | ~70 |
| `.env.example` | Configuration template | dotenv | ~40 |
| `RESEARCH.md` | Advanced techniques | Markdown | ~100 |
| `README.md` | Project documentation | Markdown | ~50 |

## SECURITY MODEL

- **No base64 obfuscation** in any source file
- **No hidden withdrawal functions** — funds stay in your wallet
- **No external telemetry** — only calls YOUR configured RPC endpoint + public APIs
- **No hardcoded keys** — all from .env file
- **Local signing only** — private keys passed as CLI args, never over network
- **No base58/base64 encode-then-execute** patterns
- Every file is human-readable and short enough to audit in 30 seconds

### What external APIs are called (all read-only public endpoints):
- Jupiter: `api.jup.ag/swap/v1/quote` (swap quotes)
- Jupiter: `api.jup.ag/swap/v1/swap` (builds unsigned transactions)
- Jupiter: `api.jup.ag/price/v3` (token prices)
- DexScreener: `api.dexscreener.com/latest/dex/search` (trending pairs)
- Solana RPC: YOUR configured endpoint (sendTransaction)

### Security scan results
```
grep -rn -E 'eval\(|Function\(|atob|btoa|require.*http|axios|fetch.*https' .
CLEAN: No suspicious patterns found
```

## COST BREAKDOWN (2026-08-15 prices)

| Item | Cost (SOL) | Cost (USD) |
|---|---|---|
| Wallet creation (5 sub-wallets) | 0.00125 | $0.19 |
| 50 buy + 50 sell tx fees | 0.07 | $10.50 |
| Buy capital (5 x 0.01 SOL) | 0.05 | $7.50 |
| **Total (~$150/SOL)** | **~0.12 SOL** | **~$18** |

Fits in $20 budget. Uses minimal priority fees (250k microlamports).

## ADVANCED FEATURES

### Network Condition Monitoring
Dynamically adjust priority fees based on network congestion:
```python
from trading_engine import get_network_conditions
conditions = get_network_conditions(rpc_url)
# Returns: {congestion: "low/medium/high", recommended_priority_fee: 500000}
```

### DexScreener Integration
Scan for trending tokens with real-time liquidity and volume data:
```python
from strategies import DEXScrenerAPI
pairs = DEXScrenerAPI.search("BONK")  # Returns 30 pairs
```

### All-Coin Auto-Discovery
```python
from strategies import AllCoinScanner
scanner = AllCoinScanner(min_liquidity_usd=50000)
tokens = scanner.scan_trending()
# Returns: [{mint, symbol, price, liquidity_usd, volume_24h, price_change_24h}, ...]
```

### Dynamic Position Sizing
```python
from strategies import PositionManager
pm = PositionManager(total_capital_usd=100)
# Max 20% per trade, 5 concurrent positions max
# Daily loss limit: 10% of capital
```

## DEPENDENCIES

- Python 3.10+ (stdlib only for orchestration/strategy logic)
- Node.js 18+ (for @noble/curves ed25519 signing)
- No npm install — install.sh downloads tarballs via curl

### Node.js packages (all auditable):
- @noble/curves@1.4.2 — ed25519 cryptography (~32KB)
- @noble/hashes@1.8.0 — hash functions (dependency)
- bs58@6.0.0 — base58 encoding (Solana standard)
- base-x@5.0.1 — base conversion (dependency of bs58)

## TROUBLESHOOTING

| Issue | Fix |
|---|---|
| "Cannot find module @noble/curves" | Run install.sh |
| Jupiter returns TOKEN_NOT_TRADABLE | Token has no liquidity; try larger amount |
| Transaction fails: Insufficient funds | Need 0.12+ SOL for full run |
| 403 from DexScreener | Use User-Agent header (already handled in API client) |
| npm install times out | install.sh uses curl+tar instead |

## LEGAL WARNING

Creating artificial trading volume IS MARKET MANIPULATION under CEA §4b.
The CFTC and SEC have fined entities $100K-$1M+ for this practice.
This bot should ONLY be used on tokens you control the treasury for.

## RELATED SKILLS
- `crypto-trading-bot-research` — Legal framework and alternative DEX bots
- `touch-gnome-env` — For Android Termux deployment
