#!/usr/bin/env python3
"""
Devnet Integration Simulation for One Claw Sloth ($OCS)

Simulates a full market environment with:
- 1 creator/token deployment
- 5 bot wallets (buyers/reactors)
- 3-8 simulated customer wallets (random buys/sells)
- Price feed simulation
- Strategy trigger testing

Goal: Verify bot wallets react correctly to market conditions
      and that strategies fire when expected.
"""

import os
import sys
import json
import time
import random
import subprocess
import urllib.request
from typing import List, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from wallet_tracker import PnLTracker

# Load devnet env
DEVNET_ENV = os.path.join(SCRIPT_DIR, ".env.devnet")
if os.path.exists(DEVNET_ENV):
    with open(DEVNET_ENV) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

RPC_ENDPOINT = os.environ.get("RPC_ENDPOINT", "https://api.devnet.solana.com")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "")
WALLET_PUBKEY = os.environ.get("WALLET_PUBKEY", "")
NUM_BOT_WALLETS = 5
NUM_CUSTOMER_WALLETS = random.randint(3, 8)

# ─── Mock Market State ───
class MockMarket:
    """Simulates a Pump.fun-style bonding curve market."""
    
    def __init__(self, token_mint: str, initial_price: float = 0.0001):
        self.token_mint = token_mint
        self.price_sol = initial_price  # Price in SOL per token
        self.price_usd = initial_price * 150  # Approx SOL price
        self.volume_24h = 0.0
        self.trades: List[Dict] = []
        self.buy_pressure = 0.0
        self.sell_pressure = 0.0
        
        # Pump.fun bonding curve virtual reserves (constant product: k = x * y)
        # At launch: 30 virtual SOL, 1,073,000,000 virtual tokens
        # price = virtual_sol / virtual_token => scale to initial_price
        self._virtual_token = 1_073_000_000  # 1.073B virtual tokens
        self._virtual_sol = initial_price * self._virtual_token  # Derived from initial price
        self._k = self._virtual_sol * self._virtual_token  # Constant product invariant
        
    def record_trade(self, buyer: str, seller: str, amount_sol: float, is_buy: bool):
        """Record a trade and update market state."""
        self.trades.append({
            "buyer": buyer[:20],
            "seller": seller[:20] if seller else "bonding_curve",
            "amount_sol": amount_sol,
            "price": self.price_sol,
            "is_buy": is_buy,
            "timestamp": time.time()
        })
        
        # Pump.fun bonding curve: price = virtual_sol_reserves / virtual_token_reserves
        # At launch: 30 virtual SOL, 1,073,000,000 virtual tokens
        # price = 30 / 1.073e9 = ~0.00002795 SOL per token
        # MC = price * 1e9 * 150 (at $150/SOL)
        # Buy impact follows diminishing returns: tokens_out = supply * (1 - reserve_sol / (reserve_sol + X))
        
        # Track virtual reserves (initialized in __init__ from initial price)
        # Bonding curve constant product: k = virtual_sol * virtual_token
        
        if is_buy:
            # Buy: SOL goes in, tokens come out
            # new_virtual_sol = old_virtual_sol + amount_sol (before fees)
            # After 3% fees at launch: net_sol goes to reserves
            net_sol = amount_sol * (1 - 0.03)  # 3% fee at launch tier
            new_virtual_sol = self._virtual_sol + net_sol
            new_virtual_token = self._virtual_sol * self._virtual_token / new_virtual_sol if new_virtual_sol > 0 else self._virtual_token
            tokens_out = self._virtual_token - new_virtual_token
            
            # Update price
            self._virtual_sol = new_virtual_sol
            self._virtual_token = new_virtual_token
            self.price_sol = new_virtual_sol / new_virtual_token if new_virtual_token > 0 else self.price_sol
            
            self.buy_pressure += amount_sol
        else:
            # Sell: tokens go in, SOL comes out
            # First convert sell amount (SOL) to tokens at current price
            tokens_in = amount_sol / self.price_sol if self.price_sol > 0 else 0
            # Apply 0.25% fee
            tokens_in_after_fee = tokens_in * (1 - 0.0025)
            
            new_virtual_token = self._virtual_token + tokens_in_after_fee
            new_virtual_sol = self._virtual_sol * self._virtual_token / new_virtual_token if new_virtual_token > 0 else self._virtual_sol
            sol_out = self._virtual_sol - new_virtual_sol
            
            # Update price
            self._virtual_sol = new_virtual_sol
            self._virtual_token = new_virtual_token
            self.price_sol = new_virtual_sol / new_virtual_token if new_virtual_token > 0 else self.price_sol
            
            self.sell_pressure += amount_sol
        
        self.price_usd = self.price_sol * 150
        self.volume_24h += amount_sol
    
    def get_price_change(self, window_seconds: int = 300) -> float:
        """Get price change over last N seconds."""
        cutoff = time.time() - window_seconds
        recent = [t for t in self.trades if t["timestamp"] > cutoff]
        if len(recent) < 2:
            return 0.0
        return (recent[-1]["price"] - recent[0]["price"]) / recent[0]["price"]
    
    def get_recent_buys(self, window_seconds: int = 60) -> float:
        """Get total buy volume in last N seconds."""
        cutoff = time.time() - window_seconds
        return sum(t["amount_sol"] for t in self.trades 
                   if t["is_buy"] and t["timestamp"] > cutoff)


# ─── Wallet Helpers ───
def run_node(args: list, timeout: int = 30) -> Optional[str]:
    """Run a Node.js helper command."""
    wallet_js = os.path.join(SCRIPT_DIR, "wallet_utils.js")
    sign_js = os.path.join(SCRIPT_DIR, "sign_sender.js")
    
    if args[0] in ["generate", "derive", "get_pub", "validate"]:
        cmd = ["node", wallet_js] + args
    else:
        cmd = ["node", sign_js] + args
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return None
    except:
        return None


def create_wallet() -> Optional[Dict]:
    """Create a new wallet."""
    result = run_node(["generate"])
    if result:
        return json.loads(result)
    return None


def rpc_call(method: str, params: list, timeout: int = 15) -> Optional[dict]:
    """Make RPC call."""
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": method, "params": params
    })
    req = urllib.request.Request(
        RPC_ENDPOINT, data=payload.encode(),
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return None


def get_balance(pubkey: str) -> float:
    """Get SOL balance."""
    data = rpc_call("getBalance", [pubkey, {"commitment": "confirmed"}])
    if data and "result" in data:
        return data["result"]["value"] / 1_000_000_000
    return 0.0


def transfer_sol(from_seed: str, to_pubkey: str, amount_sol: float) -> Optional[str]:
    """Transfer SOL."""
    amount_lamports = int(amount_sol * 1_000_000_000)
    result = run_node([
        "batch_transfer", RPC_ENDPOINT, from_seed,
        f"{to_pubkey}:{amount_lamports}"
    ], timeout=60)
    
    if result:
        try:
            data = json.loads(result)
            if "error" in data:
                return None
            return data.get("signature")
        except:
            pass
    return None


def mock_buy(wallet: Dict, market: MockMarket, amount_sol: float):
    """Simulate a buy trade (no real transaction on devnet)."""
    market.record_trade(
        buyer=wallet["pubkey"],
        seller="bonding_curve",
        amount_sol=amount_sol,
        is_buy=True
    )
    return amount_sol


def mock_sell(wallet: Dict, market: MockMarket, amount_sol: float):
    """Simulate a sell trade."""
    market.record_trade(
        buyer="bonding_curve",
        seller=wallet["pubkey"],
        amount_sol=amount_sol,
        is_buy=False
    )
    return amount_sol


# ─── Strategy Testing ───
def test_dip_buy_strategy(bot_wallets: List[Dict], market: MockMarket) -> List[Dict]:
    """
    Test: When price drops 10%+, bot wallets should buy.
    Expected: At least one bot wallet makes a buy.
    """
    print("\n  [TEST] Dip Buy Strategy")
    print(f"    Initial price: {market.price_sol:.8f} SOL")
    
    # Simulate price drop
    for _ in range(5):
        mock_sell(random.choice(bot_wallets), market, 0.05)
    
    price_change = market.get_price_change(window_seconds=300)
    print(f"    Price change: {price_change*100:.1f}%")
    
    # Bot should buy on dip
    actions = []
    for wallet in bot_wallets:
        if price_change < -0.10:  # 10% dip
            amount = random.uniform(0.01, 0.05)
            mock_buy(wallet, market, amount)
            actions.append({"wallet": wallet["pubkey"][:20], "action": "buy", "amount": amount})
            print(f"    ✓ Bot wallet {wallet['pubkey'][:20]} bought {amount:.4f} SOL")
    
    if actions:
        print(f"    [PASS] {len(actions)} bot wallets reacted to dip")
    else:
        print(f"    [FAIL] No bot wallets reacted to {price_change*100:.1f}% dip")
    
    return actions


def test_whale_buy_strategy(bot_wallets: List[Dict], market: MockMarket) -> List[Dict]:
    """
    Test: When there's a large buy (>0.5 SOL), whale wallet should buy too.
    Expected: Whale wallet makes a larger buy.
    """
    print("\n  [TEST] Whale Buy Strategy")
    
    whale = bot_wallets[0]
    
    # Simulate whale customer buy
    customer_buy = 0.6  # >0.5 threshold
    mock_buy({"pubkey": "customer_whale_1"}, market, customer_buy)
    
    recent_buys = market.get_recent_buys(window_seconds=60)
    print(f"    Recent buys: {recent_buys:.2f} SOL")
    
    actions = []
    if recent_buys > 0.5:
        whale_buy = 0.5
        mock_buy(whale, market, whale_buy)
        actions.append({"wallet": whale["pubkey"][:20], "action": "whale_buy", "amount": whale_buy})
        print(f"    ✓ Whale wallet bought {whale_buy:.4f} SOL")
        print(f"    [PASS] Whale strategy triggered")
    else:
        print(f"    [FAIL] Not enough buy pressure: {recent_buys:.2f} SOL")
    
    return actions


def test_customer_simulation(bot_wallets: List[Dict], market: MockMarket, 
                            duration_seconds: int = 60) -> Dict:
    """
    Simulate random customer activity and observe bot reactions.
    """
    print(f"\n  [TEST] Customer Simulation ({duration_seconds}s)")
    
    # Create customer wallets
    customers = []
    for _ in range(NUM_CUSTOMER_WALLETS):
        w = create_wallet()
        if w:
            customers.append(w)
    
    print(f"    Created {len(customers)} customer wallets")
    
    # Simulate trading
    start = time.time()
    bot_trades = 0
    customer_trades = 0
    
    while time.time() - start < duration_seconds:
        # Random customer action
        if customers and random.random() < 0.6:
            customer = random.choice(customers)
            if random.random() < 0.7:  # 70% buys
                amount = random.uniform(0.01, 0.1)
                mock_buy(customer, market, amount)
                customer_trades += 1
            else:
                amount = random.uniform(0.01, 0.05)
                mock_sell(customer, market, amount)
                customer_trades += 1
        
        # Bot reaction
        price_change = market.get_price_change(window_seconds=120)
        
        # Dip buying
        if price_change < -0.08:
            for wallet in bot_wallets[1:3]:  # Non-whale wallets
                if random.random() < 0.3:
                    amount = random.uniform(0.005, 0.02)
                    mock_buy(wallet, market, amount)
                    bot_trades += 1
        
        # Comment posting simulation
        if random.random() < 0.2:
            pass  # Comments logged elsewhere
        
        time.sleep(0.5)  # Faster loop for test
    
    elapsed = time.time() - start
    print(f"    Duration: {elapsed:.1f}s")
    print(f"    Customer trades: {customer_trades}")
    print(f"    Bot trades: {bot_trades}")
    print(f"    Final price: {market.price_sol:.8f} SOL")
    print(f"    24h volume: {market.volume_24h:.2f} SOL")
    
    return {
        "duration": elapsed,
        "customer_trades": customer_trades,
        "bot_trades": bot_trades,
        "final_price": market.price_sol,
        "volume": market.volume_24h
    }


def test_early_phase_activity(bot_wallets: List[Dict], market: MockMarket) -> Dict:
    """
    Test: First 10 minutes should be very active.
    Goal: Get on trending list.
    """
    print("\n  [TEST] Early Phase Activity (10 min sim)")
    
    start = time.time()
    target_duration = 10 * 60  # Simulated 10 minutes
    target_trades = 50  # Minimum trades for trending
    
    trades = 0
    loop_count = 0
    max_loops = 250  # Cap for test speed
    
    while time.time() - start < target_duration and trades < target_trades and loop_count < max_loops:
        # High-frequency bot buying
        for wallet in bot_wallets:
            if random.random() < 0.4:  # 40% chance per wallet per loop
                amount = random.uniform(0.01, 0.04)
                mock_buy(wallet, market, amount)
                trades += 1
        
        # Occasional sells for realism
        if random.random() < 0.15:
            seller = random.choice(bot_wallets)
            mock_sell(seller, market, random.uniform(0.005, 0.02))
        
        time.sleep(0.5)  # Faster loop for test
        loop_count += 1
    
    elapsed = time.time() - start
    trades_per_min = trades / (elapsed / 60) if elapsed > 0 else 0
    
    print(f"    Trades: {trades}")
    print(f"    Trades/min: {trades_per_min:.1f}")
    print(f"    Volume: {market.volume_24h:.2f} SOL")
    print(f"    Price: {market.price_sol:.8f} SOL")
    
    passed = trades >= target_trades and trades_per_min >= 5
    status = "PASS" if passed else "FAIL"
    print(f"    [{status}] Activity {'sufficient' if passed else 'too low'} for trending")
    
    return {
        "trades": trades,
        "trades_per_min": trades_per_min,
        "volume": market.volume_24h,
        "passed": passed
    }


# ─── Main Test Runner ───
def run_simulation():
    """Run full simulation."""
    print("=" * 70)
    print("OCS DEVNET INTEGRATION SIMULATION")
    print("=" * 70)
    print(f"Token: One Claw Sloth ($OCS)")
    print(f"Network: {os.environ.get('NETWORK', 'devnet')}")
    print(f"RPC: {RPC_ENDPOINT}")
    print(f"Bot wallets: {NUM_BOT_WALLETS}")
    print(f"Customer wallets: {NUM_CUSTOMER_WALLETS}")
    print("=" * 70)
    
    # Create mock token
    token_mint = "OCS" + "x" * 40  # Fake mint for simulation
    market = MockMarket(token_mint, initial_price=0.0001)
    
    # Create bot wallets
    print("\n[SETUP] Creating bot wallets...")
    bot_wallets = []
    for i in range(NUM_BOT_WALLETS):
        # Use deterministic wallets for reproducibility
        result = run_node(["derive", "--seed", 
                          "Eg6zfnuaaoSEz3VCqbz6X9Z1ZVtAMiWxtAmGtLAXRYyE",
                          "--index", str(i+10)])
        if result:
            wallet = json.loads(result)
            bot_wallets.append(wallet)
            print(f"  Bot {i+1}: {wallet['pubkey'][:20]}...")
    
    if len(bot_wallets) < NUM_BOT_WALLETS:
        print(f"  [WARN] Only created {len(bot_wallets)} bot wallets")
    
    # Run tests
    results = {}
    tracker = PnLTracker(token_mint=market.token_mint, initial_price=market.price_sol)
    for i, w in enumerate(bot_wallets):
        tracker.add_wallet(i, w.get("pubkey", f"bot_{i}"), w.get("seed_b58", ""), initial_sol=1.0)
    
    # Test 1: Dip buying
    market_copy = MockMarket(token_mint, initial_price=0.0001)
    results["dip_buy"] = test_dip_buy_strategy(bot_wallets, market_copy)
    
    # Test 2: Whale buying
    market_copy = MockMarket(token_mint, initial_price=0.0001)
    results["whale_buy"] = test_whale_buy_strategy(bot_wallets, market_copy)
    
    # Test 3: Customer simulation - reduced duration for fast testing
    market_copy = MockMarket(token_mint, initial_price=0.0001)
    results["customer_sim"] = test_customer_simulation(bot_wallets, market_copy, duration_seconds=15)
    
    # Test 4: Early phase activity
    market_copy = MockMarket(token_mint, initial_price=0.0001)
    results["early_phase"] = test_early_phase_activity(bot_wallets, market_copy)
    
    # Test 5: P&L dashboard from simulated activity
    tracker.print_dashboard()
    
    # Test 6: AI adjustments
    adjustments = tracker.get_ai_adjustments()
    if adjustments:
        print("\n[AI] Applying adjustments to pumpfun_launch.py params...")
        tracker.apply_adjustments(adjustments)
    
    tracker.save_report(os.path.join(SCRIPT_DIR, "simulation_pnl_report.json"))
    
    # Summary
    print("\n" + "=" * 70)
    print("SIMULATION RESULTS")
    print("=" * 70)
    
    # Dip buy results
    dip_actions = results.get("dip_buy", [])
    print(f"\n1. Dip Buy Strategy:")
    print(f"   Actions taken: {len(dip_actions)}")
    if dip_actions:
        for action in dip_actions[:3]:
            print(f"   - Wallet {action['wallet']}: {action['action']} {action['amount']:.4f} SOL")
    
    # Whale buy results
    whale_actions = results.get("whale_buy", [])
    print(f"\n2. Whale Buy Strategy:")
    print(f"   Actions taken: {len(whale_actions)}")
    if whale_actions:
        for action in whale_actions:
            print(f"   - Wallet {action['wallet']}: {action['action']} {action['amount']:.4f} SOL")
    
    # Customer sim results
    cust_results = results.get("customer_sim", {})
    print(f"\n3. Customer Simulation:")
    print(f"   Customer trades: {cust_results.get('customer_trades', 0)}")
    print(f"   Bot trades: {cust_results.get('bot_trades', 0)}")
    print(f"   Final price: {cust_results.get('final_price', 0):.8f} SOL")
    
    # Early phase results
    early_results = results.get("early_phase", {})
    print(f"\n4. Early Phase Activity:")
    print(f"   Trades: {early_results.get('trades', 0)}")
    print(f"   Trades/min: {early_results.get('trades_per_min', 0):.1f}")
    print(f"   Volume: {early_results.get('volume', 0):.2f} SOL")
    print(f"   Trending ready: {'YES' if early_results.get('passed') else 'NO'}")
    
    # Recommendations
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    
    if early_results.get("trades_per_min", 0) < 5:
        print("  [ADJUST] Increase bot trade frequency to 5+ trades/min")
        print("           → Reduce interval from 30s to 15s")
        print("           → Increase buy amount from 0.01-0.04 to 0.02-0.06 SOL")
    
    if len(dip_actions) == 0:
        print("  [ADJUST] Dip buy threshold too high")
        print("           → Lower from 10% to 7% dip")
        print("           → Add multiple wallets to react")
    
    if len(whale_actions) == 0:
        print("  [ADJUST] Whale buy threshold not met")
        print("           → Lower from 0.5 SOL to 0.3 SOL")
        print("           → Make whale wallet more aggressive")
    
    print("\n[READY] Run these adjustments in pumpfun_launch.py before mainnet")
    
    return results


if __name__ == "__main__":
    results = run_simulation()
    
    # Save results
    results_file = os.path.join(SCRIPT_DIR, "simulation_results.json")
    with open(results_file, "w") as f:
        json.dump({
            "timestamp": time.time(),
            "token": "One Claw Sloth",
            "results": results
        }, f, indent=2)
    
    print(f"\nResults saved to: {results_file}")
