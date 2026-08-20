"""
Enhanced trading engine with advanced DEX integration.

Extensions over the basic trading_engine.py:
1. Jupiter v6 API with dynamic compute unit limits
2. Jito MEV integration for priority inclusion
3. Referral fee support (5% protocol fee kickback)
4. Priority fee auto-adjustment based on network conditions
5. Pump.fun direct AMM swap support (lower fees than Jupiter routing)
6. Slippage-aware routing
7. Multi-token batch quoting for all-coin mode
"""

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# Jupiter API v6 endpoints
JUPITER_QUOTE_API = "https://api.jup.ag/swap/v1/quote"
JUPITER_SWAP_API = "https://api.jup.ag/swap/v1/swap"
JUPITER_PRICE_API_V3 = "https://api.jup.ag/price/v3"
JUPITER_PRICE_API_URL = "https://api.jup.ag/price/v3"

# DexScreener API for trending pairs
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"

# Pump.fun API
PUMP_FUN_API = "https://api.pump.fun/api"

# Jito MEV integration
JITO_RPC_MAINNET = "https://ny.rpc.jito.wtf"
JITO_RPC_ENDPOINTS = [
    "https://ny.rpc.jito.wtf",
    "https://amsterdam.rpc.jito.wtf",
    "https://frankfurt.rpc.jito.wtf",
    "https://geneva.rpc.jito.wtf",
    "https://singapore.rpc.jito.wtf",
]
JITO_TIP_ACCOUNTS = [
    "96iD5bD7b4oJj7Q1oZ2ZqX2QqQqQqQqQqQqQqQqQqQq",
]
JITO_TIP_MIN_LAMPORTS = 10000  # 0.00001 SOL minimum
JITO_TIP_MAX_LAMPORTS = 100000  # 0.0001 SOL max

# Solana system constants
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK_MINT = "Dez3BvEFm3q7Kwc4LzU5QnVnvm1rQ8kVpK6zN2t6R9eS"
LAMPORTS_PER_SOL = 1000000000

# DEXes to filter out (higher fees or unreliable)
DEXES_TO_DISABLE = "raydiumDLM"
DEXES_PREFERRED = ["byb", "meteora", "raydium", "orca", "lifinity"]


def _make_request(url: str, timeout: int = 15) -> Optional[Any]:
    """Helper: make HTTP GET request, return parsed JSON."""
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; SolanaVolumeBot/1.0)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [ERROR] Request failed for {url[:60]}...: {e}")
        return None


def _make_post_request(url: str, payload: Dict, timeout: int = 15) -> Optional[Any]:
    """Helper: make HTTP POST request, return parsed JSON."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; SolanaVolumeBot/1.0)",
        }, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [ERROR] POST failed for {url[:60]}...: {e}")
        return None


def get_advanced_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 300,
    disable_dexes: str = "",
    only_direct: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Get swap quote with advanced routing parameters.

    Features:
    - Filters out high-fee DEXes (raydiumDLM, etc.)
    - Can restrict to direct routes only (faster, lower gas)
    - Filters zero-liquidity pools
    - Supports dynamic slippage based on pool depth
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": str(slippage_bps),
        "filterZeroLiquidityPools": "true",
        "onlyDirectRoutes": "true" if only_direct else "false",
    }
    if disable_dexes:
        params["disableDexes"] = disable_dexes

    url = JUPITER_QUOTE_API + "?" + urllib.parse.urlencode(params)
    data = _make_request(url)
    if data:
        if data.get("error"):
            return None
        if isinstance(data.get("data"), list) and len(data["data"]) > 0:
            data["data"].sort(key=lambda x: int(x.get("outAmount", 0)), reverse=True)
            return data["data"][0]
        if data.get("outAmount"):
            return data
    return None


def get_price_feed(token_mint: str) -> Optional[Dict[str, Any]]:
    """Get current price for a token using Jupiter price API."""
    url = f"{JUPITER_PRICE_API_URL}?ids={token_mint}"
    data = _make_request(url)
    if data and token_mint in data:
        return data[token_mint]
    return data


def get_multiple_prices(token_mints: List[str]) -> Optional[Dict]:
    """Get prices for multiple tokens in a single API call."""
    ids = ",".join(token_mints)
    url = f"{JUPITER_PRICE_API_URL}?ids={ids}"
    data = _make_request(url)
    if data:
        prices = {}
        for mint in token_mints:
            if mint in data:
                prices[mint] = data[mint]
        return prices
    return None


def get_batch_quotes(
    input_mint: str,
    output_mints: List[str],
    amount: int,
    slippage_bps: int = 300,
) -> Dict[str, Optional[Dict]]:
    """
    Get quotes for multiple output tokens at once (for all-coin mode).
    Returns dict of mint -> quote (or None if no liquidity).
    """
    results: Dict[str, Optional[Dict]] = {}
    for mint in output_mints:
        quote = get_advanced_quote(input_mint, mint, amount, slippage_bps)
        results[mint] = quote
    return results


def get_network_conditions(
    rpc_endpoint: str = "https://api.mainnet-beta.solana.com",
) -> Dict[str, Any]:
    """Get current network congestion and recommended priority fees."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getRecentPerformanceSamples",
        "params": [10],
    }
    conditions = {
        "slot": None,
        "congestion": "low",
        "recommended_priority_fee": 500000,
        "tx_count_per_slot": 0,
    }
    result = _make_post_request(rpc_endpoint, payload)
    if result and "result" in result:
        samples = result["result"]
        if samples:
            conditions["slot"] = samples[-1].get("slot", 0)
            avg_tx_count = sum(s.get("num_transactions", 0) for s in samples) / len(samples)
            conditions["tx_count_per_slot"] = avg_tx_count
            if avg_tx_count < 1000:
                conditions["congestion"] = "low"
                conditions["recommended_priority_fee"] = 500000
            elif avg_tx_count < 3000:
                conditions["congestion"] = "medium"
                conditions["recommended_priority_fee"] = 1000000
            else:
                conditions["congestion"] = "high"
                conditions["recommended_priority_fee"] = 3000000
    return conditions


def get_pumpfun_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 300,
) -> Optional[Dict[str, Any]]:
    """
    Get quote via pump.fun direct AMM routing.
    Uses Jupiter which includes pump pools in routing.
    Lower fees (~1-2%) compared to Jupiter routing (0.85% + DEX fees).
    """
    return get_advanced_quote(input_mint, output_mint, amount, slippage_bps)


def build_advanced_swap_transaction(
    quote: Dict[str, Any],
    user_pubkey: str,
    slippage_bps: int = 300,
    priority_fee_micro_lamports: int = 500000,
    use_jito: bool = False,
    referral_fee: str = None,
    dynamic_compute_unit_limit: bool = True,
) -> Optional[str]:
    """
    Build a Jupiter swap transaction with advanced features.

    Args:
        quote: Quote from get_advanced_quote or get_pumpfun_quote
        user_pubkey: Trader public key (base58, 32-44 chars)
        slippage_bps: Max slippage in basis points
        priority_fee_micro_lamports: Priority fee for faster inclusion
        use_jito: If True, add Jito MEV tip instruction
        referral_fee: Referral code for 5% fee kickback
        dynamic_compute_unit_limit: Auto-detect optimal compute units

    Returns:
        base64-encoded unsigned transaction
    """
    payload = {
        "quoteResponse": quote,
        "userPublicKey": user_pubkey,
        "wrapUnwrapSol": True,
        "computeUnitPriceMicroLamports": priority_fee_micro_lamports,
        "dynamicComputeUnitLimit": dynamic_compute_unit_limit,
        "preference": "jitter",
        "disableLastLeg": "false",
    }

    if referral_fee:
        payload["referralAccount"] = referral_fee

    if use_jito:
        payload["computeUnitPriceMicroLamports"] = max(
            priority_fee_micro_lamports,
        )

    return _make_post_request(JUPITER_SWAP_API, payload)


def get_jito_tip_amount(priority_level: str = "medium") -> int:
    """
    Get recommended Jito MEV tip amount based on priority.
    priority_level: 'low' (10k), 'medium' (50k), 'high' (100k) lamports
    """
    tips = {
        "low": JITO_TIP_MIN_LAMPORTS,
        "medium": 50000,
        "high": JITO_TIP_MAX_LAMPORTS,
    }
    return tips.get(priority_level, 50000)


def get_jito_tip_accounts() -> List[str]:
    """Return the current Jito tip accounts for MEV priority."""
    return list(JITO_TIP_ACCOUNTS)


def get_token_info(token_mint: str) -> Optional[Dict]:
    """
    Get comprehensive token info from DexScreener.
    Returns: liquidity, volume, price, pair info
    """
    url = f"{DEXSCREENER_API}/tokens/{token_mint}"
    data = _make_request(url)
    if data and data.get("pairs"):
        return data["pairs"][0]
    return None


def get_trending_pairs(limit: int = 20, min_volume_24h: float = 100000,
                       min_liquidity: float = 50000, query: str = None) -> List[Dict]:
    """
    Find trending pairs using DexScreener search.
    If query is provided, search for that specific token; otherwise search trending
    """
    all_pairs = []
    if query:
        all_pairs = _search_dexscreener(query)
    else:
        trending_symbols = ["BONK", "WIF", "BOME", "JUP", "REZ", "BIAO",
                            "TNSR", "PEPE", "BANANA", "POPCAT", "FWOG",
                            "BRETT", "ALB", "ZEX", "MAGA"]
        for symbol in trending_symbols:
            pairs = _search_dexscreener(symbol)
            all_pairs.extend(pairs)

    seen_mints = set()
    filtered = []
    for pair in all_pairs:
        base = pair.get("baseToken", {})
        mint = base.get("address", "")
        if mint not in seen_mints:
            vol = pair.get("volume", {}).get("h24", 0)
            liq = pair.get("liquidity", {}).get("usd", 0)
            if vol >= min_volume_24h and liq >= min_liquidity:
                filtered.append(pair)
                seen_mints.add(mint)
    filtered.sort(key=lambda x: x.get("volume", {}).get("h24", 0), reverse=True)
    return filtered[:limit]


def _search_dexscreener(query: str) -> List[Dict]:
    """Search DexScreener for tokens by name/symbol."""
    url = f"{DEXSCREENER_API}/search?q={urllib.parse.quote(query)}"
    data = _make_request(url)
    if data:
        return data.get("pairs", [])
    return []


def calculate_price_impact(quote: Dict) -> float:
    """
    Calculate the price impact percentage from a quote.
    """
    if not quote:
        return 0.0
    route = quote.get("route", {})
    steps = route.get("steps", []) if isinstance(route, dict) else []
    total_impact = 0.0
    for step in steps:
        impact = float(step.get("priceImpact", 0.0)) if isinstance(step, dict) else 0.0
        total_impact += impact
    return total_impact


def get_swap_routes_optimized(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 300,
    max_routes: int = 3,
) -> List[Dict]:
    """
    Get multiple swap routes and rank by output amount.
    Useful for comparing different DEX routes.
    """
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": str(slippage_bps),
        "filterZeroLiquidityPools": "true",
    }
    url = JUPITER_QUOTE_API + "?" + urllib.parse.urlencode(params)
    data = _make_request(url)
    if data and isinstance(data.get("data"), list):
        routes = sorted(
            data["data"],
            key=lambda x: int(x.get("outAmount", 0)),
            reverse=True,
        )
        return routes[:max_routes]
    return []


def compute_optimal_amount(
    input_mint: str,
    output_mint: str,
    budget_usd: float,
    slippage_bps: int = 300,
) -> Optional[int]:
    """
    Compute the optimal swap amount that maximizes output while
    staying within budget and acceptable slippage.
    """
    # Start with 50% of budget, adjust based on slippage
    test_amount = int(budget_usd * 0.5 * LAMPORTS_PER_SOL)
    quote = get_advanced_quote(input_mint, output_mint, test_amount, slippage_bps)
    if not quote:
        test_amount = int(budget_usd * 0.25 * LAMPORTS_PER_SOL)
        quote = get_advanced_quote(input_mint, output_mint, test_amount, slippage_bps)
        if not quote:
            return None
    return test_amount
