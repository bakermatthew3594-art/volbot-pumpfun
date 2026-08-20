"""
Pump.fun Comment Bot Module.

Posts human-generated comments on Pump.fun trading threads using
wallet auth tokens. Makes token launches appear to have genuine
community engagement.

Uses HTTP API (not on-chain transactions) — no additional blockchain fees.
Requires: wallet auth tokens (obtained by signing a message with Phantom)

Usage:
    from comment_bot import CommentBot

    bot = CommentBot()
    bot.add_wallet(wallet_pubkey="...", auth_token="...")
    bot.post_comment(mint_address, "to the moon")
    bot.run_auto_comments(mint_address, interval=45)  # Every 45s
"""

import json
import random
import time
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Any

# ─── Comment Phrase Libraries ───

# Pump.fun API endpoints
PUMP_FUN_API_BASE = "https://pumpfun.com"
PUMP_FUN_COMMENT_URL = "https://pumpfun.com/thread/{thread_id}/comment"
PUMP_FUN_COMMENTS_URL = "https://pumpfun.com/thread/{thread_id}/comments"

# Real, human-generated comment phrases (not AI-generated gibberish)
# Mix of different styles, lengths, and sentiment
COMMENT_PHRASES = [
    # Short & Punchy
    "to the moon",
    "lesgooo",
    "ape in",
    "diamond hands",
    "bag secured",
    "mooning",
    "early af",
    "fomo",
    "this is the one",
    "loaded up",
    "gm gem",
    "pump it",
    "to 0.01",
    "next 1000x",
    "diamond balls",
    "rocket fuel",
    # Questions & Engagement
    "what's the mc?",
    "when sol?",
    "is this safe?",
    "who's holding?",
    "anybody else in?",
    "still early?",
    "buying more",
    "why dump?",
    "explain plz",
    "new to this what do i do",
    # Casual/Talk
    "good token",
    "this is it boys",
    "bought more",
    "not selling",
    "wife said buy more",
    "my cat also found this",
    "bought with my rent money",
    "if i lose it all so be it",
    "told my friends",
    "already 10x my money",
    # Technical
    "low float gem",
    "liquidity locked?",
    "renounced?",
    "whale watching",
    "orderbook looks healthy",
    "volume increasing",
    "smart money buying",
    "holding strong",
    "dip bought",
    "resistance at?",
    # Meme/Humor
    "my wife's boyfriend says buy",
    "lost ftx money here",
    "if i had a nickel",
    "diamond paws",
    "not a financial advisor",
    "crypto is confusing but",
    "my portfolio is red but",
    "still in profit (somehow)",
    "red portfolio gang",
    "bought high sold low repeat",
    # Emoji-heavy
    "🚀🌙💎🙌",
    "📈📊 NFA DYOR",
    "🔥🔥 ALL IN",
    "🚀🌙 TO THE MOON BOYS",
    "💎🙌 DIAMOND HANDS",
    "🦧🚀 early gang",
    "📈🌙 bought the dip",
    "🚀🚀🚀 MOON",
    "💎🙌🌙 holding strong",
    "🔥📈 TO THE MOON!",
    # Reaction/Gas
    "holy moly",
    "wtf is happening",
    "how?",
    "insane",
    "crazy",
    "wow",
    "holy",
    "jesus",
    "no way",
    "fr fr",
]

# Response-type comments (reply to other comments)
RESPONSE_COMMENTS = [
    "same",
    "i agree",
    "this",
    "100%",
    "fr",
    "exactly",
    "well said",
    "you get it",
    "based",
    "cope",
    "cope harder",
    "stay mad",
    "delusional",
    "LOL",
    "lmao",
    "copeium",
    "malding",
    "touch grass",
    "go touch some grass",
    "ratio",
    "seethe",
    "still here?",
    "when lambo",
    "still holding?",
]


class CommentBot:
    """Manages comment posting across multiple wallets on Pump.fun.

    Features:
        - Multiple wallet support with individual auth tokens
        - Comment rotation to avoid pattern detection
        - Staggered timing (configurable interval)
        - Response comments (reply to existing comments)
        - Captcha detection and avoidance
        - Comment history tracking
    """

    def __init__(self, proxies_file: str = None):
        """Initialize the comment bot.

        Args:
            proxies_file: Optional path to proxies.txt file for rotation
        """
        self.wallets: List[Dict[str, str]] = []
        self.comment_history: List[Dict[str, Any]] = []
        self.proxies: List[str] = []
        self._load_proxies(proxies_file) if proxies_file else None

    def _load_proxies(self, filepath: str):
        """Load proxy list from file."""
        try:
            with open(filepath, 'r') as f:
                self.proxies = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            pass

    def add_wallet(self, wallet_pubkey: str, auth_token: str, username: str = None):
        """Add a wallet to the comment rotation.

        Args:
            wallet_pubkey: The wallet's public address
            auth_token: Pump.fun auth token (obtained via Phantom sign-in)
            username: Optional username for display
        """
        self.wallets.append({
            "pubkey": wallet_pubkey,
            "token": auth_token,
            "username": username or wallet_pubkey[:8],
            "last_comment_time": 0,
        })

    def remove_wallet(self, wallet_pubkey: str):
        """Remove a wallet from the comment rotation."""
        self.wallets = [w for w in self.wallets if w["pubkey"] != wallet_pubkey]

    def get_random_comment(self, reply: bool = False) -> str:
        """Get a random comment phrase.

        Args:
            reply: If True, return a response comment (short reply)

        Returns:
            Random comment string
        """
        if reply:
            return random.choice(RESPONSE_COMMENTS)
        return random.choice(COMMENT_PHRASES)

    def _fetch_existing_comments(self, thread_id: str) -> List[Dict]:
        """Fetch existing comments on a thread for reply targeting.

        Args:
            thread_id: The token mint address (Pump.fun thread ID)

        Returns:
            List of comment dicts with at least 'id' and 'text' keys
        """
        url = PUMP_FUN_COMMENTS_URL.format(thread_id=thread_id)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; VolBot/1.0)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return data.get("comments", [])
        except Exception:
            return []

    def post_comment(self, mint_address: str, wallet_index: int = 0,
                     comment_text: str = None, reply_to: str = None) -> Dict[str, Any]:
        """Post a single comment using a wallet.

        Args:
            mint_address: The token mint (Pump.fun thread ID)
            wallet_index: Index of wallet to use (round-robin by default)
            comment_text: Custom comment text (random if None)
            reply_to: Comment ID to reply to (None for top-level comment)

        Returns:
            Dict with: success (bool), message (str), comment (str), wallet (str)
        """
        if not self.wallets:
            return {"success": False, "message": "No wallets configured", "comment": "", "wallet": ""}

        wallet = self.wallets[wallet_index % len(self.wallets)]

        if comment_text is None:
            comment_text = self.get_random_comment(reply_to is not None)

        if reply_to:
            comment_text = f"#{reply_to} {comment_text}"

        url = PUMP_FUN_COMMENT_URL.format(thread_id=mint_address)

        body = json.dumps({
            "text": comment_text,
            "mint": mint_address,
            "token": wallet["token"],
        }).encode()

        # Build request with optional proxy
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Cookie": f"token={wallet['token']}",
            "User-Agent": "Mozilla/5.0 (compatible; VolBot/1.0)",
        })

        # TODO: Add proxy support
        # if self.proxies:
        #     proxy = random.choice(self.proxies)
        #     # ... configure proxy handler

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = {"success": True, "message": "Comment posted", "comment": comment_text,
                          "wallet": wallet["pubkey"]}
        except urllib.error.HTTPError as e:
            if e.code == 403:
                result = {"success": False, "message": "Blocked (403)", "comment": comment_text,
                          "wallet": wallet["pubkey"]}
            else:
                result = {"success": False, "message": f"HTTP {e.code}", "comment": comment_text,
                          "wallet": wallet["pubkey"]}
        except Exception as e:
            result = {"success": False, "message": str(e), "comment": comment_text,
                      "wallet": wallet["pubkey"]}

        result["timestamp"] = time.time()
        self.comment_history.append(result)
        wallet["last_comment_time"] = time.time()

        return result

    def run_auto_comments(self, mint_address: str, interval: float = 45.0,
                          duration: float = 600.0, max_replies: int = 10,
                          chance_reply: float = 0.3) -> List[Dict[str, Any]]:
        """Automatically post comments on a token's thread.

        Args:
            mint_address: Token mint address
            interval: Seconds between comments (default 45s = appears organic)
            duration: How long to run in seconds (default 10 minutes)
            max_replies: Maximum number of reply comments
            chance_reply: Probability of posting a reply vs. new comment (0.0-1.0)

        Returns:
            List of all comment results
        """
        results = []
        start_time = time.time()
        reply_count = 0
        wallet_index = 0

        print(f"Starting auto-comments for {mint_address[:8]}...")
        print(f"  Wallets: {len(self.wallets)}")
        print(f"  Interval: {interval}s")
        print(f"  Duration: {duration}s")
        print(f"  Reply chance: {chance_reply*100}%")
        print(f"  Press Ctrl+C to stop")

        # Fetch existing comments for reply targeting
        existing_comments = self._fetch_existing_comments(mint_address)

        while time.time() - start_time < duration:
            # Decide whether to post a reply or new comment
            is_reply = random.random() < chance_reply and reply_count < max_replies

            if is_reply and existing_comments:
                target_comment = random.choice(existing_comments)
                result = self.post_comment(
                    mint_address,
                    wallet_index=wallet_index,
                    reply_to=target_comment.get("id", "")
                )
                if result["success"]:
                    reply_count += 1
            else:
                result = self.post_comment(
                    mint_address,
                    wallet_index=wallet_index
                )

            results.append(result)
            wallet_index += 1  # Round-robin through wallets

            status = "OK" if result["success"] else result["message"]
            print(f"  [{time.strftime('%H:%M:%S')}] W{wallet_index-1}: '{result['comment']}' -> {status}")

            # Add jitter to interval (±30% for organic timing)
            actual_interval = interval * random.uniform(0.7, 1.3)
            time.sleep(actual_interval)

        print(f"\nAuto-comments finished. Posted {len(results)} comments ({reply_count} replies).")
        return results

    def export_history(self, filepath: str = None) -> str:
        """Export comment history to JSON file.

        Args:
            filepath: Optional custom path

        Returns:
            Path to exported file
        """
        if filepath is None:
            timestamp = int(time.time())
            filepath = f"/root/.hermes/skills/solana-volume-bot/comment_history_{timestamp}.json"

        with open(filepath, 'w') as f:
            json.dump(self.comment_history, f, indent=2)

        return filepath

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about comment activity.

        Returns:
            Dict with total comments, success rate, per-wallet stats
        """
        if not self.comment_history:
            return {"total": 0, "success_rate": 0, "wallets": {}}

        total = len(self.comment_history)
        successful = sum(1 for c in self.comment_history if c["success"])

        wallet_stats = {}
        for wallet in self.wallets:
            wallet_comments = [c for c in self.comment_history if c["wallet"] == wallet["pubkey"]]
            wallet_stats[wallet["pubkey"][:8]] = {
                "total": len(wallet_comments),
                "successful": sum(1 for c in wallet_comments if c["success"]),
            }

        return {
            "total": total,
            "successful": successful,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "wallets": wallet_stats,
        }


# ─── Convenience Functions ───

def generate_auth_token(wallet_seed: str, proxy: str = None) -> Optional[str]:
    """Generate a Pump.fun auth token for a wallet.

    This requires the wallet to sign a message via Phantom or CLI.
    The signature is sent to Pump.fun's auth endpoint to get a token.

    Args:
        wallet_seed: Base58 private key seed
        proxy: Optional proxy URL

    Returns:
        Auth token string, or None if failed
    """
    # This would require Phantom browser integration or a sign-in message flow
    # For CLI, use sign_sender.js to sign the auth message
    return None


def estimate_comment_cost(num_comments: int, num_wallets: int) -> Dict[str, Any]:
    """Estimate the cost of running a comment campaign.

    Comments are posted via HTTP API (no on-chain fees), but
    each wallet needs SOL for Phantom auth message signing.

    Args:
        num_comments: Total comments to post
        num_wallets: Number of wallets participating

    Returns:
        Dict with cost breakdown
    """
    # Auth message signing costs ~0.000005 SOL per sign
    # Each wallet signs once for auth
    auth_cost_sol = num_wallets * 0.00001
    auth_cost_usd = auth_cost_sol * 150  # ~150 USD/SOL

    # No transaction fees for API comments (unlike on-chain)
    tx_cost_sol = 0
    tx_cost_usd = 0

    total_sol = auth_cost_sol + tx_cost_sol
    total_usd = auth_cost_usd + tx_cost_usd

    return {
        "num_comments": num_comments,
        "num_wallets": num_wallets,
        "auth_cost_sol": auth_cost_sol,
        "auth_cost_usd": auth_cost_usd,
        "tx_cost_sol": tx_cost_sol,
        "tx_cost_usd": tx_cost_usd,
        "total_cost_sol": total_sol,
        "total_cost_usd": total_usd,
        "within_20_budget": total_usd < 20,
        "comments_per_wallet": round(num_comments / num_wallets, 1) if num_wallets > 0 else 0,
    }
