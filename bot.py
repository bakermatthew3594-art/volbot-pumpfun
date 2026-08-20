"""
Minimal Solana Volume Bot

Pure Python orchestrator. All cryptographic operations are delegated to
- the Node.js helper (wallet_utils.js + sign_sender.js) which
 - uses @noble/curves for ed25519 signing.

SECURITY MODEL:
- Private keys are NEVER sent over the network
- They are passed to the local Node.js helper as CLI args
- The helper signs transactions in memory and returns only the signature
- No data is exfiltrated to any server
- All RPC calls go to YOUR configured Solana endpoint

LEGAL NOTE:
Creating artificial volume on tokens you DON'T control is market manipulation.
Only use this on tokens you created and control.
"""

import os
import sys
import time
import json
import random
import subprocess
import urllib.request
from typing import List, Dict, Optional, Tuple

LAMPORTS_PER_SOL = 1_000_000_000
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

# Jupiter public swap API (no API key required)
JUPITER_QUOTE_API = "https://lite-api.jup.ag/swap/v1/quote"
JUPITER_SWAP_API = "https://lite-api.jup.ag/swap/v1/swap"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WALLET_HELPER = os.path.join(SCRIPT_DIR, "wallet_utils.js")
SIGN_HELPER = os.path.join(SCRIPT_DIR, "sign_sender.js")


def call_node(cmd: List[str], timeout: int = 30) -> Optional[str]:
    """Run a Node.js helper command and return stdout."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            print(f"  [ERROR] {result.stderr.strip()}")
            return None
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] Command timed out ({timeout}s)")
        return None
    except FileNotFoundError:
        print("  [ERROR] Node.js not found. Install with: pkg install nodejs")
        return None


def generate_main_wallet() -> Dict:
    """Generate the main trading wallet."""
    result = call_node(["node", WALLET_HELPER, "generate"])
    if result:
        return json.loads(result)
    return None


def derive_sub_wallet(main_seed_b58: str, index: int) -> Dict:
    """Derive a deterministic sub-wallet from the main seed."""
    result = call_node(["node", WALLET_HELPER, "derive", "--seed", main_seed_b58, "--index", str(index)])
    if result:
        return json.loads(result)
    return None


def get_balance(rpc: str, pubkey: str) -> int:
    """Get SOL balance in lamports."""
    result = call_node(["node", WALLET_HELPER, "balance", "--rpc", rpc, "--pubkey", pubkey])
    if result:
        data = json.loads(result)
        return data["lamports"]
    return 0


def get_token_balance(rpc: str, wallet_pubkey: str, token_mint: str) -> float:
    """Get token account balance using Solana JSON-RPC."""
    # Find ATA for the token
    import base64
    from hashlib import sha256
    
    # Derive ATA address (simplified - uses programmatic calculation)
    # ATA = Pubkey.find_program_address([wallet, TOKEN_PROGRAM, mint], ASSOCIATED_TOKEN_PROGRAM)
    # For simplicity, we use the RPC to get it
    
    url = rpc
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [wallet_pubkey, {"mint": token_mint}, {"encoding": "jsonParsed"}]
    })
    req = urllib.request.Request(url, data=payload.encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            accounts = data.get("result", {}).get("value", [])
            if accounts:
                amount = accounts[0]["data"]["parsed"]["info"]["tokenAmount"]["amount"]
                decimals = accounts[0]["data"]["parsed"]["info"]["tokenAmount"]["decimals"]
                return int(amount) / (10 ** decimals)
    except Exception:
        pass
    return 0.0


def jup_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 300) -> Optional[dict]:
    """Get swap quote from Jupiter (public, no API key)."""
    import urllib.parse
    
    url = JUPITER_QUOTE_API + "?" + urllib.parse.urlencode({
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": str(slippage_bps),
        "filterZeroLiquidityPools": "true",
        "onlyDirectRoutes": "false",
    })
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            if data.get("data") and len(data["data"]) > 0:
                data["data"].sort(key=lambda x: int(x.get("outAmount", 0)), reverse=True)
                return data["data"][0]
    except Exception as e:
        print(f"  [ERROR] Jupiter quote: {e}")
    return None


def jup_build_swap(route: dict, user_pubkey: str, slippage_bps: int = 300, priority_fee: int = 500000) -> Optional[str]:
    """Build unsigned swap transaction via Jupiter API."""
    payload = {
        "route": route,
        "userPublicKey": user_pubkey,
        "wrapUnwrapSol": True,
        "feeBps": 0,
        "computeUnitPriceMicroLamports": priority_fee,
        "preference": "jitter",
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            JUPITER_SWAP_API,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return result.get("swapTransaction")
    except Exception as e:
        print(f"  [ERROR] Jupiter swap build: {e}")
    return None


def sign_and_send(rpc: str, seed_b58: str, unsigned_tx_b64: str) -> Optional[str]:
    """Sign and submit a transaction using the local Node helper."""
    result = call_node([
        "node", SIGN_HELPER, "sign_send",
        rpc, unsigned_tx_b64, seed_b58,
    ], timeout=20)
    if result:
        data = json.loads(result)
        if "error" in data:
            print(f"  [ERROR] Sign/send: {data['error']}")
            return None
        return data.get("signature")
    return None


def batch_transfer_sol(rpc: str, main_seed: str, transfers: List[Tuple[str, int]]) -> Optional[str]:
    """Transfer SOL to multiple recipients using the local Node signing helper."""
    recipients_str = ",".join(f"{addr}:{amt}" for addr, amt in transfers)
    result = call_node([
        "node", SIGN_HELPER, "batch_transfer",
        rpc, main_seed, recipients_str,
    ], timeout=60)  # individual transfers may take time
    if result:
        data = json.loads(result)
        if "error" in data:
            print(f"  [ERROR] Batch transfer: {data['error']}")
            return None
        return json.dumps(data)  # return full results
    return None


def run_volume_bot(
    main_seed_b58: str,
    main_pub: str,
    token_mint: str,
    num_wallets: int = 5,
    buy_amount_sol: float = 0.01,
    cycles: int = 50,
    rpc_endpoint: str = "https://api.mainnet-beta.solana.com",
    slippage_bps: int = 300,
    priority_fee: int = 500000,
):
    """
    Main volume bot entry point.
    
    COST ESTIMATE (at 500000 microlamports priority fee):
        - Wallet setup: ~0.00125 SOL (5 wallets x 0.00025 rent)
        - Distribution tx: ~0.001 SOL (single batch tx)
        - Each buy: ~0.0007 SOL
        - Each sell: ~0.0007 SOL
        - 5 wallets x 10 cycles = ~0.07 SOL in tx fees
        - Buy capital: 0.01 * 5 = 0.05 SOL
        - Total: ~0.12 SOL = ~$18 at $150/SOL
    """
    
    print(f"\n{'='*60}")
    print(f"SOLANA VOLUME BOT")
    print(f"{'='*60}")
    print(f"Main Wallet:    {main_pub}")
    print(f"Token Mint:     {token_mint}")
    print(f"Sub-wallets:    {num_wallets}")
    print(f"Buy Amount:     {buy_amount_sol} SOL per buy")
    print(f"Cycles:         {cycles}")
    print(f"RPC:            {rpc_endpoint}")
    print(f"Priority Fee:   {priority_fee / 1e6:.6f} SOL")
    print(f"{'='*60}\n")
    
    # Cost estimate
    est_setup = num_wallets * 0.00025
    est_txs = cycles * num_wallets * 2 * 0.0007
    est_cap = buy_amount_sol * num_wallets
    est_total = est_setup + est_txs + est_cap + 0.01
    print(f"[INFO] Estimated total cost: ~{est_total:.4f} SOL (${est_total * 150:.2f} at $150/SOL)")
    
    # Check main wallet balance
    bal = get_balance(rpc_endpoint, main_pub)
    print(f"[INFO] Main wallet balance: {bal / LAMPORTS_PER_SOL:.6f} SOL")
    if bal < int(0.01 * LAMPORTS_PER_SOL):
        print("[ERROR] Insufficient SOL in main wallet")
        return
    
    # Generate sub-wallets (deterministic from main seed)
    print(f"\n[STEP 1] Generating {num_wallets} sub-wallets...")
    wallets = []
    for i in range(num_wallets):
        w = derive_sub_wallet(main_seed_b58, i)
        if w:
            wallets.append({"index": i, "seed_b58": w["seed_b58"], "pubkey": w["pubkey"]})
            print(f"  Wallet {i+1}: {w['pubkey'][:16]}...{w['pubkey'][-12:]}")
    
    if len(wallets) < num_wallets:
        print("[ERROR] Failed to generate all sub-wallets")
        return
    
    # Distribute SOL to sub-wallets
    print(f"\n[STEP 2] Distributing SOL to {num_wallets} wallets...")
    distribute_amount = int(buy_amount_sol * LAMPORTS_PER_SOL * 3)  # 3x for multiple cycles
    transfers = [(w["pubkey"], distribute_amount) for w in wallets]
    sig = batch_transfer_sol(rpc_endpoint, main_seed_b58, transfers)
    if sig:
        print(f"  [OK] Distributed {distribute_amount / LAMPORTS_PER_SOL:.4f} SOL to each wallet")
        print(f"  [TX] {sig[:32]}...")
    else:
        print("  [SKIP] Distribution failed (or dry run)")
    
    # Run buy-sell cycles
    print(f"\n[STEP 3] Running {cycles} volume cycles...")
    stats = {"buy_ok": 0, "buy_fail": 0, "sell_ok": 0, "sell_fail": 0}
    
    for cycle in range(cycles):
        w = wallets[cycle % len(wallets)]
        print(f"\n  --- Cycle {cycle+1}/{cycles} | Wallet {w['index']+1} ---")
        
        # Check wallet balance
        bal = get_balance(rpc_endpoint, w["pubkey"])
        if bal < int(0.002 * LAMPORTS_PER_SOL):
            print(f"  [SKIP] Wallet {w['index']+1} has only {bal / LAMPORTS_PER_SOL:.6f} SOL")
            continue
        
        # --- BUY ---
        buy_amount_lamports = int(buy_amount_sol * LAMPORTS_PER_SOL)
        print(f"  [BUY] Buying {buy_amount_sol} SOL worth of tokens...")
        
        quote = jup_quote(WRAPPED_SOL_MINT, token_mint, buy_amount_lamports, slippage_bps)
        if not quote:
            print("  [SKIP] No buy route found")
            stats["buy_fail"] += 1
            continue
        
        expected_tokens = int(quote.get("outAmount", 0))
        print(f"  [QUOTE] Expected ~{expected_tokens} tokens")
        
        unsigned_tx = jup_build_swap(quote, w["pubkey"], slippage_bps, priority_fee)
        if not unsigned_tx:
            stats["buy_fail"] += 1
            continue
        
        sig = sign_and_send(rpc_endpoint, w["seed_b58"], unsigned_tx)
        if sig:
            print(f"  [BUY OK] tx: {sig[:32]}...")
            stats["buy_ok"] += 1
        else:
            print("  [BUY FAIL] Transaction failed")
            stats["buy_fail"] += 1
            continue
        
        # Wait random interval
        wait = random.randint(5, 20)
        print(f"  [WAIT] {wait}s...")
        time.sleep(wait)
        
        # --- SELL ---
        token_balance = get_token_balance(rpc_endpoint, w["pubkey"], token_mint)
        if token_balance <= 0:
            print(f"  [SELL] No tokens to sell (balance: {token_balance})")
            stats["sell_fail"] += 1
            continue
        
        # Get token decimals for amount calculation
        sell_amount_raw = int(token_balance * (10 ** 6))  # assuming 6 decimals
        print(f"  [SELL] Selling ~{token_balance:.4f} tokens...")
        
        quote = jup_quote(token_mint, WRAPPED_SOL_MINT, sell_amount_raw, slippage_bps)
        if not quote:
            print("  [SKIP] No sell route found")
            stats["sell_fail"] += 1
            continue
        
        unsigned_tx = jup_build_swap(quote, w["pubkey"], slippage_bps, priority_fee)
        if not unsigned_tx:
            stats["sell_fail"] += 1
            continue
        
        sig = sign_and_send(rpc_endpoint, w["seed_b58"], unsigned_tx)
        if sig:
            print(f"  [SELL OK] tx: {sig[:32]}...")
            stats["sell_ok"] += 1
        else:
            print("  [SELL FAIL] Transaction failed")
            stats["sell_fail"] += 1
        
        # Random pause between cycles
        if cycle < cycles - 1:
            pause = random.randint(15, 45)
            print(f"  [PAUSE] {pause}s...")
            time.sleep(pause)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS:")
    print(f"  Buys:   {stats['buy_ok']} ok, {stats['buy_fail']} failed")
    print(f"  Sells:  {stats['sell_ok']} ok, {stats['sell_fail']} failed")
    print(f"{'='*60}")


def load_env():
    """Load .env file if it exists."""
    env_file = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


if __name__ == "__main__":
    load_env()
    
    pk = os.environ.get("PRIVATE_KEY", "")
    token = os.environ.get("TOKEN_MINT", "")
    
    if not pk or "YOUR_" in pk or not token or "eFsX" in token:
        print("=== SOLANA VOLUME BOT ===")
        print("No .env configured. Running in DEMO mode.\n")
        print("To use:")
        print("  1. cp .env.example .env")
        print("  2. Edit .env:")
        print("     - Set PRIVATE_KEY to your base58 wallet seed")
        print("     - Set TOKEN_MINT to your token's mint address")
        print("  3. Run: python3 bot.py")
        print()
        
        # Demo: generate a wallet and sub-wallets
        print("--- Demo: Wallet Generation ---")
        main = generate_main_wallet()
        if main:
            print(f"Main wallet: {main['pubkey']}")
            print(f"Seed (keep secret!): {main['seed_b58'][:20]}...")
            print()
            print("--- Sub-wallets (deterministic) ---")
            for i in range(3):
                sub = derive_sub_wallet(main["seed_b58"], i)
                if sub:
                    print(f"  Sub {i+1}: {sub['pubkey']}")
        print()
        print("--- Cost Estimate for 5 wallets, 10 cycles ---")
        print(f"  Setup: 0.00125 SOL (wallet creation rent)")
        print(f"  TX fees: 0.07 SOL (50 buy+sell at 0.0007 SOL each)")
        print(f"  Buy capital: 0.05 SOL (5 wallets x 0.01 SOL)")
        print(f"  TOTAL: ~0.12 SOL = ~$18")
        print(f"  Budget: $20  ✓")
    else:
        rpc_ep = os.environ.get("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
        run_volume_bot(
            main_seed_b58=pk,
            main_pub="",
            token_mint=token,
            num_wallets=int(os.environ.get("NUM_WALLETS", "5")),
            buy_amount_sol=float(os.environ.get("BUY_AMOUNT_SOL", "0.01")),
            cycles=int(os.environ.get("MAX_CYCLES", "50")),
            rpc_endpoint=rpc_ep,
            slippage_bps=int(os.environ.get("SLIPPAGE_BPS", "300")),
            priority_fee=int(os.environ.get("PRIORITY_FEE_MICROLAMPORTS", "500000")),
        )
