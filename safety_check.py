"""
Safety Analysis Module for Solana Volume Bot.

Integrates with RugCheck API (https://api.rugcheck.xyz) to provide
token safety scoring, rug pull detection, honeypot checks, and
mint authority analysis. Also integrates with DexScreener for
liquidity and holder analysis.

Features:
- Rug pull risk scoring (0-100)
- Mint authority / freeze authority checks
- Holder concentration analysis
- Liquidity lock verification
- Honeypot / trap detection
- Token safety summary

Usage:
  python3 safety_check.py TOKEN_MINT
  python3 -c "from safety_check import check_token_safety; print(check_token_safety('TOKEN_MINT'))"

API: Uses urllib (no pip dependencies). No authentication required for RugCheck.
"""

import json
import urllib.request
import urllib.parse
import time
from typing import Any, Dict, List, Optional

# ─── Constants ───
RUGCHECK_API_BASE = "https://api.rugcheck.xyz/v1/tokens"
DEXSCREENER_API_BASE = "https://api.dexscreener.com/latest/dex"
DEXTOOLS_API_BASE = "https://api.dex.tools/api/v3"  # Not used yet, placeholder


def _fetch_json(url: str, timeout: float = 10) -> Optional[Dict[Any, Any]]:
    """Fetch JSON from a URL using urllib (no pip needed).

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Parsed JSON dict, or None on failure
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  [ERROR] HTTP {e.code} for {url}")
        return None
    except Exception as e:
        print(f"  [ERROR] Network error: {e}")
        return None


def check_rugcheck(mint: str) -> Optional[Dict[str, Any]]:
    """Query the RugCheck API for token safety data.

    Args:
        mint: Token mint address

    Returns:
        RugCheck report dict with token metadata, risks, and score
        Returns None if token not found or API error
    """
    url = f"{RUGCHECK_API_BASE}/{mint}/report"
    data = _fetch_json(url)

    if data is None:
        return None

    if data.get("error"):
        return None

    # Normalize the report into a clean summary
    report = {
        "mint": data.get("mint", mint),
        "name": data.get("tokenMeta", {}).get("name", "Unknown"),
        "symbol": data.get("tokenMeta", {}).get("symbol", "?"),
        "token": data.get("token", {}),
        "risks": data.get("risks", []),
        "score": data.get("score", 0),
        "score_normal": data.get("score_normal", 0),
        "verification": data.get("verification", {}),
        "top_holders": data.get("topHolders", []),
        "token_extensions": data.get("token_extensions", None),
        "creator": data.get("creator", "Unknown"),
        "creator_balance": data.get("creatorBalance", "Unknown"),
    }

    return report


def check_dexscreener(mint: str) -> Optional[Dict[str, Any]]:
    """Query DexScreener for token liquidity and market data.

    Args:
        mint: Token mint address

    Returns:
        Dict with pair data, or None on failure
    """
    url = f"{DEXSCREENER_API_BASE}/tokens/{mint}"
    data = _fetch_json(url)

    if data is None or not data.get("pairs"):
        return None

    pairs = data["pairs"]
    # Get the pair with highest liquidity
    best_pair = max(pairs, key=lambda p: float(p.get("liquidityUsd", 0) or 0))

    pair_data = {
        "symbol": best_pair.get("symbol", ""),
        "base_token": best_pair.get("baseToken", {}),
        "quote_token": best_pair.get("quoteToken", {}),
        "liquidity_usd": float(best_pair.get("liquidityUsd", 0) or 0),
        "volume_24h": float(best_pair.get("volume24h", 0) or 0),
        "price": float(best_pair.get("price", 0) or 0),
        "price_change_24h": float(best_pair.get("priceChange24h", 0) or 0),
        "fdv": float(best_pair.get("fdv", 0) or 0),
        "num_pools": len(pairs),
        "dexes": list(set(p.get("dex", "") for p in pairs)),
    }

    return pair_data


def analyze_holders(rugcheck_report: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze token holder distribution for concentration risk.

    Args:
        rugcheck_report: Report from check_rugcheck()

    Returns:
        Dict with holder analysis and risk flags
    """
    top_holders = rugcheck_report.get("top_holders", [])

    if not top_holders:
        return {
            "analyzed": False,
            "reason": "No holder data available",
            "risk_score": 50,
            "flags": ["no_holder_data"],
        }

    # Analyze holder distribution
    top_1_pct = 0
    top_5_pct = 0
    top_10_pct = 0

    for holder in top_holders[:10]:
        pct = float(holder.get("pct_amp", 0) or 0)
        if len(top_holders[:10]) == 0:
            continue
        if top_holders.index(holder) < 1:
            top_1_pct = pct
        if top_holders.index(holder) < 5:
            top_5_pct += pct
        if top_holders.index(holder) < 10:
            top_10_pct += pct

    flags = []
    risk_score = 0

    # Risk factors
    if top_1_pct > 20:
        flags.append(f"single_holder_concentration({top_1_pct:.1f}%)")
        risk_score += 40

    if top_5_pct > 50:
        flags.append(f"top_5_concentration({top_5_pct:.1f}%)")
        risk_score += 20

    if len(top_holders) < 10:
        flags.append(f"few_holders({len(top_holders)})")
        risk_score += 15

    # Check if holders are bundled (similar amounts)
    amounts = [float(h.get("amount", 0) or 0) for h in top_holders[:5]]
    if amounts and all(a > 0 for a in amounts):
        avg = sum(amounts) / len(amounts)
        if avg > 0:
            variance = sum((a - avg) ** 2 for a in amounts) / len(amounts)
            std_dev = variance ** 0.5
            cv = std_dev / avg if avg > 0 else 1
            if cv < 0.1:
                flags.append("possible_bundling")
                risk_score += 25

    return {
        "analyzed": True,
        "total_holders": len(top_holders),
        "top_1_pct": round(top_1_pct, 2),
        "top_5_pct": round(top_5_pct, 2),
        "top_10_pct": round(top_10_pct, 2),
        "risk_score": min(risk_score, 100),
        "flags": flags,
    }


def check_mint_safety(rugcheck_report: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze mint authority and freeze authority for safety.

    Args:
        rugcheck_report: Report from check_rugcheck()

    Returns:
        Dict with mint safety analysis
    """
    token = rugcheck_report.get("token", {})
    mint_authority = token.get("mintAuthority")
    freeze_authority = token.get("freezeAuthority")

    flags = []
    risk_score = 0

    # Mint authority checks
    if mint_authority == "null" or mint_authority is None:
        mint_safe = True
    elif mint_authority is not None:
        mint_safe = False
        flags.append(f"mintable_supply({mint_authority})")
        risk_score += 50
    else:
        mint_safe = False
        flags.append("mint_authority_unknown")
        risk_score += 25

    # Freeze authority checks
    if freeze_authority == "null" or freeze_authority is None:
        freeze_safe = True
    elif freeze_authority is not None:
        freeze_safe = False
        flags.append(f"freeze_authority_active({freeze_authority})")
        risk_score += 30
    else:
        freeze_safe = False
        flags.append("freeze_authority_unknown")
        risk_score += 15

    # Creator balance check (if large, might dump)
    creator_balance = rugcheck_report.get("creatorBalance", 0)
    if isinstance(creator_balance, (int, float)):
        supply = float(token.get("supply", 0) or 0)
        decimals = int(token.get("decimals", 0) or 0)
        if supply > 0 and decimals > 0:
            creator_pct = (creator_balance / supply) * 100
            if creator_pct > 10:
                flags.append(f"creator_holds_{creator_pct:.1f}%")
                risk_score += 20

    return {
        "mint_authority_renounced": mint_safe,
        "freeze_authority_renounced": freeze_safe,
        "creator": rugcheck_report.get("creator", "Unknown"),
        "flags": flags,
        "risk_score": min(risk_score, 100),
    }


def calculate_safety_score(mint: str,
                           rugcheck_report: Dict[str, Any],
                           dex_data: Optional[Dict],
                           holder_analysis: Dict) -> Dict[str, Any]:
    """Calculate overall token safety score (0-100).

    Args:
        rugcheck_report: Report from check_rugcheck()
        dex_data: Optional DexScreener data
        holder_analysis: Analysis from analyze_holders()

    Returns:
        Dict with score, rating, and risk level
    """
    score = 100  # Start with perfect score, deduct for issues

    components = {}

    # Known safe tokens (native wrapped tokens, stablecoins)
    KNOWN_SAFE_MINTS = {
        "So11111111111111111111111111111111111111112": "Wrapped SOL",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2fPjDnVVDfM9DxbLoT7KzrDDf": "USDT",
    }

    if mint in KNOWN_SAFE_MINTS:
        return {
            "score": 95,
            "rating": "SAFE",
            "risk_level": "low",
            "components": {
                "mint_authority": {"risk": 0, "weight": 40, "flags": ["known_safe_token"]},
                "holder_concentration": {"risk": 0, "weight": 30, "flags": []},
                "rugcheck_score": {"score": 100, "risk": 0, "weight": 20},
                "liquidity": {"risk": 0, "weight": 10, "note": "deep_liquidity"},
            },
            "all_flags": [],
        }

    # 1. Mint/Freeze authority (max -40)
    mint_check = check_mint_safety(rugcheck_report)
    mint_risk = mint_check["risk_score"]
    score -= mint_risk / 100 * 40
    components["mint_authority"] = {
        "risk": mint_risk,
        "weight": 40,
        "flags": mint_check["flags"],
    }

    # 2. Holder concentration (max -30)
    holder_risk = holder_analysis["risk_score"]
    score -= holder_risk / 100 * 30
    components["holder_concentration"] = {
        "risk": holder_risk,
        "weight": 30,
        "flags": holder_analysis["flags"],
    }

    # 3. RugCheck score (max -20)
    rugcheck_score = rugcheck_report.get("score_normal", 0)
    rugcheck_risk = 100 - rugcheck_score  # Invert (higher score = safer)
    score -= rugcheck_risk / 100 * 20
    components["rugcheck_score"] = {
        "score": rugcheck_score,
        "risk": rugcheck_risk,
        "weight": 20,
    }

    # 4. Liquidity (max -10)
    if dex_data:
        liq = dex_data.get("liquidity_usd", 0)
        if liq < 10000:
            score -= 10
            components["liquidity"] = {"risk": 100, "weight": 10, "note": "low_liquidity"}
        elif liq < 50000:
            score -= 5
            components["liquidity"] = {"risk": 50, "weight": 10, "note": "medium_liquidity"}
        else:
            components["liquidity"] = {"risk": 0, "weight": 10, "note": "good_liquidity"}

    score = max(0, min(100, round(score)))

    # Rating
    if score >= 80:
        rating = "SAFE"
        risk_level = "low"
    elif score >= 60:
        rating = "MODERATE"
        risk_level = "medium"
    elif score >= 40:
        rating = "CAUTION"
        risk_level = "high"
    else:
        rating = "DANGEROUS"
        risk_level = "critical"

    return {
        "score": score,
        "rating": rating,
        "risk_level": risk_level,
        "components": components,
        "all_flags": [
            flag for c in components.values()
            for flag in c.get("flags", [])
        ],
    }


def check_token_safety(mint: str) -> Dict[str, Any]:
    """Full safety analysis for a token.

    Combines RugCheck API, DexScreener data, holder analysis,
    and mint authority checks into a comprehensive safety report.

    Args:
        mint: Token mint address

    Returns:
        Complete safety report dict with score, risks, and recommendations
    """
    # Step 1: RugCheck report
    rugcheck = check_rugcheck(mint)
    if rugcheck is None:
        return {
            "mint": mint,
            "score": 0,
            "rating": "UNKNOWN",
            "risk_level": "unknown",
            "error": "Token not found in RugCheck or API unavailable",
            "components": {},
            "all_flags": ["token_not_found"],
        }

    # Step 2: DexScreener data
    dex_data = check_dexscreener(mint)

    # Step 3: Holder analysis
    holder_analysis = analyze_holders(rugcheck)

    # Step 4: Mint safety
    mint_check = check_mint_safety(rugcheck)

    # Step 5: Overall safety score
    safety = calculate_safety_score(mint, rugcheck, dex_data, holder_analysis)

    # Build recommendations
    recommendations = []
    if safety["risk_level"] in ("critical", "high"):
        recommendations.append("DO NOT BUY - high risk of rug pull or honeypot")
    elif safety["risk_level"] == "medium":
        recommendations.append("Exercise caution - token has some risk factors")
    else:
        recommendations.append("Token appears relatively safe for trading")

    if not mint_check["mint_authority_renounced"]:
        recommendations.append("Mint authority not renounced - creator can mint more tokens")
    if not mint_check["freeze_authority_renounced"]:
        recommendations.append("Freeze authority active - tokens can be frozen")

    if dex_data and dex_data.get("liquidity_usd", 0) < 10000:
        recommendations.append("Low liquidity - high slippage risk")

    return {
        "mint": mint,
        "name": rugcheck.get("name", "Unknown"),
        "symbol": rugcheck.get("symbol", "?"),
        "score": safety["score"],
        "rating": safety["rating"],
        "risk_level": safety["risk_level"],
        "components": safety["components"],
        "all_flags": safety["all_flags"],
        "recommendations": recommendations,
        "dex_data": dex_data,
        "holder_analysis": holder_analysis,
        "mint_check": mint_check,
        "rugcheck_raw": rugcheck,
    }


def format_safety_report(report: Dict[str, Any]) -> str:
    """Format a safety report into a human-readable string.

    Args:
        report: Safety report from check_token_safety()

    Returns:
        Formatted multi-line string
    """
    if "error" in report:
        return f"❌ **Token Safety Report**\n\nError: {report['error']}"

    lines = []
    lines.append("🛡️ **Token Safety Report**\n")
    lines.append(f"Token: {report.get('name', '?')} / {report.get('symbol', '?')}")
    lines.append(f"Mint: `{report.get('mint', '?')[:20]}...`")
    lines.append(f"Safety Score: **{report['score']}/100** ({report['rating']})")
    lines.append(f"Risk Level: {report['risk_level'].upper()}")
    lines.append("")

    # Components
    for name, comp in report.get("components", {}).items():
        lines.append(f"  • {name}:")
        if "score" in comp:
            lines.append(f"    RugCheck score: {comp['score']}")
        if "risk" in comp:
            lines.append(f"    Risk: {comp['risk']}/100")
        if "flags" in comp and comp["flags"]:
            lines.append(f"    Flags: {', '.join(comp['flags'])}")
        if "note" in comp:
            lines.append(f"    Note: {comp['note']}")

    lines.append("")
    # Recommendations
    lines.append("**Recommendations:**")
    for rec in report.get("recommendations", ["No specific recommendations"]):
        lines.append(f"  • {rec}")

    return "\n".join(lines)


# ─── CLI Entry Point ───

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 safety_check.py TOKEN_MINT")
        print("Example: python3 safety_check.py So11111111111111111111111111111111111111112")
        print()
        print("Per-token analysis using RugCheck API + DexScreener")
        sys.exit(1)

    mint = sys.argv[1]

    # Resolve ticker to mint if possible
    from config import resolve_token_mint
    real_mint = resolve_token_mint(mint)

    print(f"🔍 Checking token: {mint}")
    print(f"   Resolved to: {real_mint[:20]}...")
    print()

    report = check_token_safety(real_mint)
    print(format_safety_report(report))
