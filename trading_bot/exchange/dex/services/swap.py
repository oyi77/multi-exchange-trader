"""
DEX swap service for BSC protocols (PancakeSwap, BakerySwap, ApeSwap, Biswap).

Provides high-level swap execution, price simulation, and best-route
selection across multiple DEX protocols on the same chain.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from trading_bot.exchange.dex.base import EVMDEXProvider
from trading_bot.exchange.dex.chain.bsc import BSC

logger = logging.getLogger(__name__)

# Default router ABI filename (relative to abi_data_folder in
# 1ai-dex-trader layout — adapt as needed for your ABI store).
PANCAKE_ROUTER_ABI = "pancakeswap_router_abi"
BAKERY_ROUTER_ABI = "bakeryswap_router_abi"
APESWAP_ROUTER_ABI = "apeswap_router_abi"
BISWAP_ROUTER_ABI = "biswap_router_abi"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SwapResult:
    """Outcome of a token swap."""

    success: bool
    tx_hash: str = ""
    amount_in: int = 0
    amount_out: int = 0
    price_impact: float = 0.0
    error: str = ""


@dataclass
class RouteQuote:
    """Price quote from a single DEX route."""

    dex_name: str
    router_address: str
    amount_in: int
    amount_out: int
    price: float  # token price in USD


# ---------------------------------------------------------------------------
# Swap service
# ---------------------------------------------------------------------------


class DexSwapService:
    """Aggregated swap service across multiple BSC DEX protocols.

    Typical usage::

        svc = DexSwapService(provider)
        best = await svc.find_best_route(
            token_address="0x...",
            amount_in_wei=Web3.to_wei(0.1, "ether"),
            is_buy=True,
        )
        result = await svc.execute_swap(best, wallet, private_key)
    """

    def __init__(self, provider: EVMDEXProvider) -> None:
        self.provider = provider
        self._routers: dict[str, str] = dict(BSC.router_addresses)

    # -- public API -----------------------------------------------------------

    async def get_price(
        self,
        token_address: str,
        amount_in_wei: int = 1_000_000_000_000_000_000,  # 1 BNB default
        *,
        is_buy: bool = True,
        dex_name: str = "pancakeswap",
    ) -> float:
        """Simulate a swap and return the estimated token price in USD.

        Args:
            token_address: Target token contract address.
            amount_in_wei: Simulated input amount (wei).
            is_buy: ``True`` → buying token with BNB; ``False`` → selling.
            dex_name: Which DEX to query (``pancakeswap``, ``bakeryswap``,
                      ``apeswap``, ``biswap``).

        Returns:
            Token price in USD, or ``0.0`` on failure.
        """
        router_addr = self._routers.get(dex_name)
        if not router_addr:
            logger.warning("Unknown DEX '%s' — skipping price fetch", dex_name)
            return 0.0

        contract = self._get_router_contract(router_addr, dex_name)
        if contract is None:
            return 0.0

        try:
            checksum = self.provider.w3.to_checksum_address(token_address)
            path = (
                [self.provider.w3.to_checksum_address(BSC.wbnb_address), checksum]
                if is_buy
                else [checksum, self.provider.w3.to_checksum_address(BSC.wbnb_address)]
            )
            amounts = contract.functions.getAmountsOut(amount_in_wei, path).call()
            bnb_price = self._bnb_usd_price()
            if is_buy:
                tokens_out = amounts[-1]
                if tokens_out == 0:
                    return 0.0
                return float(bnb_price * amount_in_wei / tokens_out) / 1e18
            else:
                bnb_out = amounts[-1]
                return float(bnb_out * bnb_price) / 1e18 / (amount_in_wei / 1e18)
        except Exception as exc:
            logger.debug("Price fetch on %s failed: %s", dex_name, exc)
            return 0.0

    async def find_best_route(
        self,
        token_address: str,
        amount_in_wei: int,
        *,
        is_buy: bool = True,
    ) -> Optional[RouteQuote]:
        """Find the DEX route offering the best price for a swap.

        Args:
            token_address: Target token contract address.
            amount_in_wei: Input amount (wei) to simulate.
            is_buy: ``True`` → buying token; ``False`` → selling.

        Returns:
            The best ``RouteQuote``, or ``None`` if all routes failed.
        """
        best: Optional[RouteQuote] = None

        for dex_name in self._routers:
            router_addr = self._routers[dex_name]
            contract = self._get_router_contract(router_addr, dex_name)
            if contract is None:
                continue

            try:
                checksum = self.provider.w3.to_checksum_address(token_address)
                path = (
                    [self.provider.w3.to_checksum_address(BSC.wbnb_address), checksum]
                    if is_buy
                    else [checksum, self.provider.w3.to_checksum_address(BSC.wbnb_address)]
                )
                amounts = contract.functions.getAmountsOut(amount_in_wei, path).call()
                amount_out = amounts[-1]

                if amount_out == 0:
                    continue

                bnb_price = self._bnb_usd_price()
                if is_buy:
                    price = float(bnb_price * amount_in_wei / amount_out) / 1e18
                else:
                    price = float(amount_out * bnb_price) / 1e18 / (amount_in_wei / 1e18)

                quote = RouteQuote(
                    dex_name=dex_name,
                    router_address=router_addr,
                    amount_in=amount_in_wei,
                    amount_out=amount_out,
                    price=price,
                )

                if best is None or (
                    is_buy and quote.price < best.price
                ) or (
                    not is_buy and quote.price > best.price
                ):
                    best = quote

            except Exception as exc:
                logger.debug("Route %s failed: %s", dex_name, exc)
                continue

        return best

    async def execute_swap(
        self,
        quote: RouteQuote,
        wallet_address: str,
        private_key: str,
        *,
        slippage_percent: float = 0.5,
        deadline_minutes: int = 10,
    ) -> SwapResult:
        """Execute a swap through the best DEX route.

        Args:
            quote: Route quote from ``find_best_route``.
            wallet_address: Sender address.
            private_key: Sender private key (hex).
            slippage_percent: Max acceptable slippage (e.g. ``0.5`` = 0.5%).
            deadline_minutes: Tx deadline from now.

        Returns:
            ``SwapResult`` with status and tx hash.
        """
        contract = self._get_router_contract(quote.router_address, quote.dex_name)
        if contract is None:
            return SwapResult(success=False, error=f"Router {quote.dex_name} not available")

        try:
            w3 = self.provider.w3
            checksum = w3.to_checksum_address(wallet_address)
            token_checksum = w3.to_checksum_address(
                # Infer token address from path — second element for buy
                self._infer_token(quote)
            )
            deadline = int(time.time()) + deadline_minutes * 60
            min_out = int(quote.amount_out * (1 - slippage_percent / 100))
            path = (
                [w3.to_checksum_address(BSC.wbnb_address), token_checksum]
                if quote.price > 0  # buy scenario
                else [token_checksum, w3.to_checksum_address(BSC.wbnb_address)]
            )

            nonce = w3.eth.get_transaction_count(checksum)
            gas_price = w3.eth.gas_price

            if path[0] == w3.to_checksum_address(BSC.wbnb_address):
                # Buying: swapExactETHForTokens (compatible name)
                txn = contract.functions.swapExactETHForTokens(
                    min_out, path, checksum, deadline
                ).build_transaction({
                    "from": checksum,
                    "value": quote.amount_in,
                    "gasPrice": gas_price,
                    "nonce": nonce,
                })
            else:
                # Selling: swapExactTokensForETH
                txn = contract.functions.swapExactTokensForETH(
                    quote.amount_in, min_out, path, checksum, deadline
                ).build_transaction({
                    "from": checksum,
                    "gasPrice": gas_price,
                    "nonce": nonce,
                })

            # Estimate gas
            estimated = w3.eth.estimate_gas(txn)
            txn["gas"] = int(estimated * 1.2)

            signed = w3.eth.account.sign_transaction(txn, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash_hex = w3.to_hex(tx_hash)

            logger.info("Swap tx sent: %s (dex=%s)", tx_hash_hex, quote.dex_name)
            return SwapResult(success=True, tx_hash=tx_hash_hex, amount_in=quote.amount_in)

        except Exception as exc:
            logger.exception("Swap execution failed on %s", quote.dex_name)
            return SwapResult(success=False, error=str(exc))

    # -- internal helpers -----------------------------------------------------

    def _get_router_contract(self, address: str, dex_name: str) -> Any:
        """Return a Web3 contract instance for the router address.

        Uses a minimal router ABI loaded from the provider's Web3 instance.
        In production, load the full router ABI from your ABI store.
        """
        w3 = self.provider.w3
        if w3 is None:
            return None
        try:
            checksum = w3.to_checksum_address(address)
            # Minimal router ABI — swapExactETHForTokens + getAmountsOut
            abi = [
                {
                    "inputs": [
                        {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
                        {"internalType": "address[]", "name": "path", "type": "address[]"},
                        {"internalType": "address", "name": "to", "type": "address"},
                        {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    ],
                    "name": "swapExactETHForTokens",
                    "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
                    "stateMutability": "payable",
                    "type": "function",
                },
                {
                    "inputs": [
                        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                        {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
                        {"internalType": "address[]", "name": "path", "type": "address[]"},
                        {"internalType": "address", "name": "to", "type": "address"},
                        {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    ],
                    "name": "swapExactTokensForETH",
                    "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
                    "stateMutability": "nonpayable",
                    "type": "function",
                },
                {
                    "inputs": [
                        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                        {"internalType": "address[]", "name": "path", "type": "address[]"},
                    ],
                    "name": "getAmountsOut",
                    "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
                    "stateMutability": "view",
                    "type": "function",
                },
            ]
            return w3.eth.contract(address=checksum, abi=abi)
        except Exception as exc:
            logger.warning("Cannot create router contract for %s: %s", dex_name, exc)
            return None

    def _bnb_usd_price(self) -> float:
        """Return approximate BNB/USD price via a simple oracle.

        Falls back to 600.0 if unavailable.
        """
        try:
            w3 = self.provider.w3
            # Cheap check: PancakeSwap BNB/USDT pool
            # In production, use a price oracle / Chainlink feed.
            pancakeswap = self._get_router_contract(BSC.router_addresses["pancakeswap"], "pancakeswap")
            if pancakeswap is not None:
                usdt = w3.to_checksum_address(BSC.usdt_address)
                wbnb = w3.to_checksum_address(BSC.wbnb_address)
                amounts = pancakeswap.functions.getAmountsOut(
                    w3.to_wei(1, "ether"), [wbnb, usdt]
                ).call()
                return float(w3.from_wei(amounts[-1], "ether"))
        except Exception:
            pass
        return 600.0

    def _infer_token(self, quote: RouteQuote) -> str:
        """Guess the token address based on the route direction."""
        # For buy: token address is not stored in quote directly;
        # the caller provides it explicitly in execute_swap.
        return ""
