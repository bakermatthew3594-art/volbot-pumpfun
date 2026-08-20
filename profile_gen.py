"""
Profile Generation Module for Pump.fun Wallets.

Generates human-like wallet profiles (usernames, bios, token holdings,
activity patterns) to make pump.fun launches appear organic.

No external dependencies — uses Python stdlib random + json only.

Usage:
    from profile_gen import generate_profile, generate_profiles_for_bundle

    profile = generate_profile(num_wallets=5)
    # Returns: {"profiles": [...], "metadata": {...}}
"""

import random
import json
import os
import time
from typing import List, Dict, Any

# ─── Data Libraries ───

# Realistic crypto usernames (mix of styles)
WALLETS_USERNAMES = [
    # Trader-style
    "CryptoTrader42", "SolanaApe", "EarlyBuyer", "DiamondHodler",
    "ToTheMoonMan", "BetaTester", "AlphaHunter", "MemeLord", "RLF",
    "SolanaSteve", "FomoBuyer", "DCA_Degenerate", "WAGMI", "NGMI",
    "PaperHands", "DiamondPaws", "FudLover", "MoonBoy", "ShillMaster",
    "WhaleWatcher", "SniperKing", "GweiGang", "Maxi", "Degen",
    # Casual-style
    "CoffeeAndSol", "EarlyBird", "NightOwl", "BullRunner", "MarketMaven",
    "TokenTribune", "CryptoCurious", "SolanaNewbie", "HodlHodl", "Spike",
    # Noob-style
    "FirstBuyEver", "SmallBagHolder", "LearningCrypto", "JustHereForTheFreeSol",
    "AirdropHunter", "GasIsTooHigh", "WhyIsItGoingDown", "BagHoldersClub",
    "HODLer", "RektButHODLing", "StillInProfit", "RedPortfolio",
]

# Bio templates with varied writing styles and emoji usage
PROFILE_BIOS = [
    # Confident trader
    "Early holder | DCA into quality tokens | Not financial advice",
    "Building my Solana portfolio one trade at a time 🚀",
    "GM! Focused on early-stage meme coins and blue chips",
    "HODL mode activated 💎 | Looking for the next 100x",
    "Trading since 2020 | Still here | Still learning",
    # Casual
    "Just here for the vibes and the gains 😎",
    "Crypto enthusiast | Solana native | Let's go!",
    "Not a financial advisor, just a crypto enjoyer",
    "Focusing on quality over quantity | DCA believer",
    "My portfolio is red, but my spirit is green",
    # Noob
    "New to crypto, learning as I go 📚",
    "Bought my first memecoin yesterday, already hooked",
    "Still paper trading mostly, but dabbling in real trades",
    "GM frens! Just started this journey, wish me luck",
    "Crypto is confusing but I'm trying 😅",
    # Technical
    "On-chain data enthusiast | Smart money follower",
    "Tracking wallets smarter than me | Copy trading mode",
    "Watching the order books all day and night",
    "Market structure is king | Price action first",
    "Volume profile + RSI + MACD hodler",
    # Funny/Human
    "If I had a nickel for every rug I bought...",
    "My wife's boyfriend says I'm a genius at this",
    "Lost money on FTX, found it on Solana memes",
    "Told my wife I'm 'researching' when I'm watching charts",
    "Diamond hands but my portfolio says paper",
    # Emoji-heavy
    "🚀🌙💎🙌 GM crypto fam! | BTC maxi | SOL enjoyer",
    "📈📊 Just another degen in the space | NFA",
    "🔄 DCA every dip | 🔍 hunting for gems | 🏹 early entries",
    "🌙🚀 Looking for moons and gains | 💰 building bags",
    "🔥🔥🔥 All in on crypto | 🌊 riding the bull market",
]

# Fake token portfolio mentions (to look established)
TOKEN_MENTIONS = [
    "Also in: BONK, WIF, BOME, JUP, RNDR",
    "Holding: SOL, BONK, WIF, BOME, POPCAT",
    "Bag: BTC, ETH, SOL, BONK, WIF",
    "Portfolio: SOL, JUP, BOME, BONK, WIF, BAN",
    "Also hodling: WIF, BOME, BONK, POPCAT, CATI",
    "SOL, BONK, WIF, BOME, JUP, RNDR, PEPE",
    "Stacking: BTC, ETH, SOL, BONK, WIF, BOME",
    "Degenerate plays: BONK, WIF, BOME, POPCAT",
    "Also long: SOL, BONK, WIF, BOME, TIA, JUP",
    "HODL: BTC, ETH, SOL, BONK, WIF, BOME, PEPE",
]

# Fake trading history mentions
TRADING_HISTORY = [
    "Been trading since 2021 | Still here",
    "Started with memecoins | Now into blue chips too",
    "Lost money on FTX | Made it back on Solana",
    "Trading SOL memes since early 2024",
    "Been through 3 bear markets | Still grinding",
    "Started trading during the 2022 crash",
    "On-chain native | Love the transparency",
    "Used to trade stocks | Now crypto full-time",
    "Been through the grind | Still believe in the tech",
    "Started with $100 | Now managing 5 figures",
]

# Avatar themes/styles
AVATAR_STYLES = [
    "pixel_sunglasses", "pixel_ape", "pixel_robot", "pixel_cat",
    "pixel_astronaut", "pixel_skull", "pixel_fox", "pixel_dragon",
    "pixel_phoenix", "pixel_wolf", "pixel_bear", "pixel_panda",
    "crypto_punk", "pixel_crown", "pixel_laser_eyes", "pixel_diamond",
]

# Activity patterns (when wallets are most active)
ACTIVITY_PATTERNS = [
    "US_East",    # Active 9am-5pm EST, sleeps 11pm-7am
    "US_West",    # Active 6am-2am PST
    "EU_London",  # Active 2am-10am EST
    "Asia_Tokyo", # Active 7pm-3am EST
    "Crypto_Native", # Active 24/7 in bursts
    "Night_Owl",  # Active 10pm-4am EST
]


def generate_username() -> str:
    """Generate a realistic-looking crypto username."""
    return random.choice(WALLETS_USERNAMES)


def generate_bio() -> str:
    """Generate a realistic bio with varied writing style and emoji usage."""
    return random.choice(PROFILE_BIOS)


def generate_token_mention() -> str:
    """Generate a fake token portfolio mention to look established."""
    return random.choice(TOKEN_MENTIONS)


def generate_trading_history() -> str:
    """Generate a fake trading history snippet."""
    return random.choice(TRADING_HISTORY)


def generate_full_bio() -> str:
    """Generate a full profile bio combining multiple elements."""
    parts = []
    # 40% chance to include trading history
    if random.random() < 0.4:
        parts.append(generate_trading_history())
    # Always include main bio
    parts.append(generate_bio())
    # 30% chance to include token portfolio
    if random.random() < 0.3:
        parts.append(generate_token_mention())
    return " | ".join(parts)


def generate_avatar() -> str:
    """Generate an avatar style/theme for the profile."""
    return random.choice(AVATAR_STYLES)


def generate_activity_pattern() -> Dict[str, Any]:
    """Generate an activity pattern for the wallet."""
    pattern = random.choice(ACTIVITY_PATTERNS)
    # Return timing info for staggered trades
    if pattern == "US_East":
        return {"pattern": pattern, "active_hours": [9, 17], "timezone": "EST"}
    elif pattern == "US_West":
        return {"pattern": pattern, "active_hours": [6, 23], "timezone": "PST"}
    elif pattern == "EU_London":
        return {"pattern": pattern, "active_hours": [2, 10], "timezone": "EST"}
    elif pattern == "Asia_Tokyo":
        return {"pattern": pattern, "active_hours": [19, 3], "timezone": "EST"}
    elif pattern == "Crypto_Native":
        return {"pattern": pattern, "active_hours": [0, 23], "timezone": "EST"}
    else:  # Night_Owl
        return {"pattern": pattern, "active_hours": [22, 4], "timezone": "EST"}


def generate_profile(wallet_index: int = 0) -> Dict[str, Any]:
    """Generate a single human-like wallet profile.

    Returns a dict with:
        username, bio, avatar, activity_pattern, trading_style,
        buy_probability, sell_probability, avg_trade_size
    """
    profile = {
        "wallet_index": wallet_index,
        "username": generate_username(),
        "bio": generate_full_bio(),
        "avatar": generate_avatar(),
        "activity_pattern": generate_activity_pattern(),
        "trading_style": random.choice(["aggressive", "moderate", "conservative", "scalper", "swing"]),
        "buy_probability": round(random.uniform(0.3, 0.8), 2),
        "sell_probability": round(random.uniform(0.2, 0.6), 2),
        "avg_trade_size_sol": round(random.uniform(0.01, 0.05), 3),
    }
    return profile


def generate_profiles_for_bundle(
    num_wallets: int = 5,
    seed: int = None
) -> Dict[str, Any]:
    """Generate a set of human-like profiles for a bundle of wallets.

    Args:
        num_wallets: Number of wallet profiles to generate (2-20)
        seed: Optional random seed for reproducibility

    Returns:
        Dict with:
            profiles: List of individual profile dicts
            metadata: Bundle-level info (timestamp, total_wallets, diversity_score)
            strategy: Recommended wash trading strategy based on profile mix
    """
    if seed is not None:
        random.seed(seed)

    profiles = []
    for i in range(num_wallets):
        profiles.append(generate_profile(wallet_index=i))

    # Calculate diversity score
    usernames = set(p["username"] for p in profiles)
    bios = set(p["bio"] for p in profiles)
    styles = set(p["trading_style"] for p in profiles)
    patterns = set(p["activity_pattern"]["pattern"] for p in profiles)

    diversity_score = (
        len(usernames) / num_wallets * 0.3 +
        len(bios) / num_wallets * 0.3 +
        len(styles) / len(["aggressive", "moderate", "conservative", "scalper", "swing"]) * 0.2 +
        len(patterns) / len(ACTIVITY_PATTERNS) * 0.2
    )

    # Recommend strategy based on profile diversity
    if diversity_score > 0.8:
        recommended_strategy = "Round Robin"
        reason = "High diversity — all wallets appear unique, safe for any strategy"
    elif diversity_score > 0.6:
        recommended_strategy = "Ring Trading"
        reason = "Moderate diversity — use circular pattern to maximize natural appearance"
    elif diversity_score > 0.4:
        recommended_strategy = "Market Maker"
        reason = "Low diversity — use MK pattern to simulate liquidity provision"
    else:
        recommended_strategy = "Whale Mimicry"
        reason = "Very low diversity — use whale pattern to mask similarity"

    # Sort profiles by activity pattern for staggered trading
    profiles.sort(key=lambda p: p["activity_pattern"]["active_hours"][0])

    return {
        "profiles": profiles,
        "metadata": {
            "timestamp": time.time(),
            "total_wallets": num_wallets,
            "diversity_score": round(diversity_score, 3),
            "recommended_strategy": recommended_strategy,
            "strategy_reason": reason,
            "seed": seed,
        }
    }


def get_profile_summary(profiles_data: Dict[str, Any]) -> str:
    """Generate a readable summary of generated profiles."""
    profiles = profiles_data["profiles"]
    meta = profiles_data["metadata"]

    lines = [
        f"Generated {meta['total_wallets']} wallet profiles",
        f"Diversity score: {meta['diversity_score']}/1.0",
        f"Recommended strategy: {meta['recommended_strategy']}",
        f"Reason: {meta['strategy_reason']}",
        "",
        "Wallet Profiles:",
    ]

    for p in profiles:
        lines.append(f"  W{p['wallet_index']}: @{p['username']} | "
                      f"Style: {p['trading_style']} | "
                      f"Pattern: {p['activity_pattern']['pattern']}")

    return "\n".join(lines)


def export_profiles(profiles_data: Dict[str, Any], filepath: str = None) -> str:
    """Export profile data to JSON file.

    Args:
        profiles_data: Output from generate_profiles_for_bundle()
        filepath: Optional custom path. Defaults to ~/.hermes/skills/solana-volume-bot/wallet_profiles_<timestamp>.json
    """
    if filepath is None:
        timestamp = int(time.time())
        filepath = f"/root/.hermes/skills/solana-volume-bot/wallet_profiles_{timestamp}.json"

    with open(filepath, 'w') as f:
        json.dump(profiles_data, f, indent=2)

    return filepath
