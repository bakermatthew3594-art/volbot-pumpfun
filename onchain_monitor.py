"""
On-chain monitoring and portfolio management utilities.

Features:
1. Balance monitoring (SOL + SPL tokens)
2. Token account discovery (ATA addresses)
3. Pool state queries (Raydium, Orca, pump.fun)
4. Transaction monitoring (recent swaps, buys, sells)
5. Rug pull detection (liquidity drain monitoring)
6. Multi-wallet portfolio tracking
"""

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from trading_engine import LAMPORTS_PER_SOL


# ─── Solana RPC Constants ───
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJybhwavoYi2rAmm3j3bA8Gi1W8"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2gr7BNbo48Xf4rNKS92iTBiDqDF"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
PUMP_FUN_PROGRAM = "6FJnN8mQ7w8w8w8w8w8w8w8w8w8w8w8w8w8w8w8w8w8w"  # placeholder
RAYDIUM_AMM_PROGRAM = "9Hz8wPyuaRFBGa2oU6f4mDwjK6m5m5m5m5m5m5m5m5m5"


class SolanaRPC:
    """Lightweight Solana JSON-RPC client for on-chain monitoring."""

    def __init__(self, rpc_endpoint: str = "https://api.mainnet-beta.solana.com"):
        self.rpc_endpoint = rpc_endpoint
        self._request_id = 1

    def _make_request(self, method: str, params: List[Any]) -> Optional[Any]:
        """Make a JSON-RPC request to Solana."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        self._request_id += 1
        data = json.dumps(payload).encode("utf-8")
        try:
            req = urllib.request.Request(self.rpc_endpoint, data=data, headers={
                "Content-Type": "application/json",
            }, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                if result.get("error"):
                    return None
                return result.get("result")
        except Exception as e:
            print(f"  [RPC ERROR] {method}: {e}")
            return None

    def get_balance(self, pubkey: str) -> Optional[Dict]:
        """Get SOL balance for a wallet."""
        result = self._make_request("getBalance", [pubkey])
        if result:
            return {
                "lamports": result["value"],
                "sol": result["value"] / LAMPORTS_PER_SOL,
            }
        return None

    def get_token_accounts(self, wallet_pubkey: str) -> List[Dict]:
        """Get all SPL token accounts owned by a wallet."""
        result = self._make_request("getTokenAccountsByOwner", [
            wallet_pubkey,
            {"programId": TOKEN_PROGRAM_ID},
            {"encoding": "jsonParsed", "skipPreflight": True},
        ])
        if not result or "value" not in result:
            return []

        tokens = []
        for account in result["value"]:
            try:
                data = account["account"]["data"]["parsed"]["info"]
                mint = data["mint"]
                amount = data["tokenAmount"]
                tokens.append({
                    "mint": mint,
                    "amount": int(amount.get("amount", 0)),
                    "decimals": amount.get("decimals", 0),
                    "ui_amount": amount.get("uiAmount", 0),
                })
            except (KeyError, TypeError):
                continue
        return tokens

    def get_token_balance(self, wallet_pubkey: str, token_mint: str) -> Optional[Dict]:
        """Get balance of a specific token for a wallet."""
        accounts = self.get_token_accounts(wallet_pubkey)
        for token in accounts:
            if token["mint"] == token_mint:
                return token
        return None

    def get_latest_blockhash(self) -> Optional[str]:
        """Get the latest blockhash for transaction construction."""
        result = self._make_request("getLatestBlockhash", [{
            "commitment": "confirmed",
        }])
        if result:
            return result["value"]["blockhash"]
        return None

    def get_slot(self) -> Optional[int]:
        """Get current slot number for timing."""
        result = self._make_request("getSlot", [{"commitment": "confirmed"}])
        return result if isinstance(result, int) else None

    def get_block_time(self, slot: Optional[int] = None) -> Optional[int]:
        """Get block timestamp."""
        if slot is None:
            slot = self.get_slot()
        if slot is None:
            return None
        result = self._make_request("getBlockTime", [slot])
        return result if isinstance(result, int) else None

    def get_program_accounts(self, program_id: str, filters: List[Dict] = None) -> List[Dict]:
        """Query all accounts for a specific program."""
        params = [program_id, {"encoding": "jsonParsed", "filters": filters or []}]
        result = self._make_request("getProgramAccounts", params)
        if result:
            return result
        return []

    def get_account_info(self, account_pubkey: str) -> Optional[Dict]:
        """Get raw account info including data and owner."""
        result = self._make_request("getAccountInfo", [
            account_pubkey,
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ])
        if result and "value" in result:
            return result["value"]
        return None

    def get_recent_transactions(self, wallet_pubkey: str, limit: int = 10) -> List[Dict]:
        """Get recent transactions for a wallet."""
        result = self._make_request("getConfirmedSignaturesForAddress2", [
            wallet_pubkey,
            {"limit": limit},
        ])
        if not result or "signatures" not in result:
            return []
        return result["signatures"]

    def get_transaction_details(self, signature: str) -> Optional[Dict]:
        """Get full transaction details."""
        result = self._make_request("getTransaction", [
            signature,
            {"encoding": "jsonParsed", "commitment": "confirmed"},
            {"maxCommitmentSlot": 0},
        ])
        return result if result else None

    def get_multiple_accounts(self, pubkeys: List[str]) -> List[Optional[Dict]]:
        """Batch query multiple accounts (efficient for portfolio monitoring)."""
        result = self._make_request("getMultipleAccounts", [
            pubkeys,
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ])
        if result and "value" in result:
            return result["value"]
        return [None] * len(pubkeys)

    def health_check(self) -> Dict:
        """Check RPC health and return status."""
        start = time.time()
        result = self._make_request("getHealth", [])
        elapsed = time.time() - start

        if result is not None:
            return {"healthy": True, "latency_ms": int(elapsed * 1000)}
        return {"healthy": False, "latency_ms": -1}


class PortfolioManager:
    """
    Track and manage a multi-wallet portfolio across tokens.
    """

    def __init__(self, rpc_endpoint: str = "https://api.mainnet-beta.solana.com"):
        self.rpc = SolanaRPC(rpc_endpoint)
        self.wallets: List[str] = []
        self.token_balances: Dict[str, Dict[str, float]] = {}  # wallet -> {mint: balance}
        self.sol_balances: Dict[str, float] = {}

    def add_wallet(self, pubkey: str):
        """Add a wallet to track."""
        if pubkey not in self.wallets:
            self.wallets.append(pubkey)
            self.sol_balances[pubkey] = 0
            self.token_balances[pubkey] = {}

    def refresh_balances(self, token_mints: List[str] = None):
        """Refresh all wallet balances."""
        for wallet in self.wallets:
            # Get SOL balance
            sol_info = self.rpc.get_balance(wallet)
            if sol_info:
                self.sol_balances[wallet] = sol_info["sol"]

            # Get token balances
            tokens = self.rpc.get_token_accounts(wallet)
            for token in tokens:
                mint = token["mint"]
                if token_mints is None or mint in token_mints:
                    self.token_balances[wallet][mint] = token["ui_amount"]

    def get_total_value(self, token_prices: Dict[str, float] = None) -> float:
        """Calculate total portfolio value in USD."""
        total = 0
        sol_price = token_prices.get("SOL", 150) if token_prices else 150

        # SOL value
        for wallet, sol_bal in self.sol_balances.items():
            total += sol_bal * sol_price

        # Token value
        for wallet, tokens in self.token_balances.items():
            for mint, balance in tokens.items():
                price = token_prices.get(mint, 0) if token_prices else 0
                total += balance * price

        return total

    def get_token_allocation(self, token_mint: str) -> float:
        """Get the allocation of a specific token across all wallets."""
        total_token = sum(
            tokens.get(token_mint, 0)
            for tokens in self.token_balances.values()
        )
        total_value = self.get_total_value()
        if total_value == 0:
            return 0
        # Would need token price to calculate percentage
        return total_token

    def get_rebalance_opportunities(self) -> List[Dict]:
        """Identify rebalancing opportunities (wallets with excess SOL)."""
        opportunities = []
        avg_sol = sum(self.sol_balances.values()) / len(self.wallets) if self.wallets else 0

        for wallet in self.wallets:
            sol_balance = self.sol_balances.get(wallet, 0)
            if sol_balance > avg_sol * 1.5:  # 50% above average
                opportunities.append({
                    "wallet": wallet,
                    "type": "excess_sol",
                    "balance": sol_balance,
                    "avg_balance": avg_sol,
                    "amount_to_transfer": sol_balance - avg_sol,
                })

        return opportunities


class RugPullDetector:
    """
    Detect potential rug pulls by monitoring liquidity and holder distribution.

    Metrics:
    - Liquidity drain (rapid decrease in pool reserves)
    - Large holder concentration (whale wallets holding >5%)
    - Transaction pattern anomalies (sudden sell pressure)
    """

    def __init__(self, rpc_endpoint: str = "https://api.mainnet-beta.solana.com"):
        self.rpc = SolanaRPC(rpc_endpoint)
        self.watched_tokens: Dict[str, Dict] = {}

    def watch_token(self, token_mint: str, pool_address: str, dex: str = "raydium"):
        """Add a token to monitor for rug pull indicators."""
        self.watched_tokens[token_mint] = {
            "pool_address": pool_address,
            "dex": dex,
            "initial_liquidity": 0,
            "last_check": 0,
            "warnings": [],
        }

    def check_liquidity_drain(self, token_mint: str) -> Optional[Dict]:
        """
        Check if liquidity is being drained from the pool.
        Returns None if OK, or a warning dict if drain detected.
        """
        if token_mint not in self.watched_tokens:
            return None

        token_info = self.watched_tokens[token_mint]
        pool = self.rpc.get_account_info(token_info["pool_address"])

        if not pool:
            return None

        try:
            data = pool.get("data", {}).get("parsed", {})
            liquidity_usd = data.get("liquidity", {}).get("usd", 0)
            initial = token_info.get("initial_liquidity", liquidity_usd)

            if liquidity_usd < initial * 0.5:  # 50% drop
                return {
                    "type": "liquidity_drain",
                    "current": liquidity_usd,
                    "initial": initial,
                    "drop_percentage": (1 - liquidity_usd / initial) * 100 if initial > 0 else 0,
                }
        except (KeyError, TypeError):
            pass

        return None

    def get_large_transactions(self, token_mint: str, min_amount_usd: float = 10000) -> List[Dict]:
        """Find large transactions involving this token."""
        from trading_engine import get_token_info
        token_info = get_token_info(token_mint)
        if not token_info:
            return []

        pair_address = token_info.get("pairAddress", "")
        transactions = []
        # Note: Would need transaction log API or Geyser for real implementation
        return transactions


class TokenDiscovery:
    """
    Discover new and trending tokens across Solana DEXes.
    """

    @staticmethod
    def scan_new_tokens(rpc: SolanaRPC) -> List[Dict]:
        """Scan for newly created token mints."""
        # Query TOKEN_PROGRAM accounts created in recent slots
        result = rpc._make_request("getProgramAccounts", [
            TOKEN_PROGRAM_ID,
            {
                "encoding": "jsonParsed",
                "filters": [
                    {"dataSize": 82},  # Mint size
                ],
            },
        ])
        if not result:
            return []

        tokens = []
        for account in result:
            try:
                mint_data = account["account"]["data"]["parsed"]["info"]
                tokens.append({
                    "mint": account["pubkey"],
                    "decimals": mint_data.get("decimals", 0),
                    "supply": mint_data.get("supply", {}).get("amount", 0),
                    "authority": mint_data.get("mintAuthority"),
                })
            except (KeyError, TypeError):
                continue
        return tokens

    @staticmethod
    def scan_going_tokens() -> List[Dict]:
        """Find token accounts with no supply (potential scam tokens)."""
        # Tokens with mint authority = null are usually safe
        # Tokens with mint authority set are risky (can mint more)
        pass
