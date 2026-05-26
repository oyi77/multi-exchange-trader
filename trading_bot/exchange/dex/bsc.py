"""
BSC (Binance Smart Chain) DEX provider.

Extends ``EVMDEXProvider`` with BSC-specific defaults and a swap
service that aggregates PancakeSwap, BakerySwap, ApeSwap, and Biswap.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from trading_bot.exchange.dex.base import EVMDEXProvider
from trading_bot.exchange.dex.chain.bsc import BSC, BSCConfig
from trading_bot.exchange.dex.services.swap import DexSwapService, RouteQuote, SwapResult

logger = logging.getLogger(__name__)


class BscDexProvider(EVMDEXProvider):
    """BSC-specific DEX provider with multi-router swap support.

    Wraps the base ``EVMDEXProvider`` with BSC chain defaults
    and adds high-level swap operations via ``DexSwapService``.

    Usage::

        provider = BscDexProvider()
        await provider.connect()
        quote = await provider.get_quote(
            "0xTokenAddress", 0.1 * 10**18, is_buy=True
        )
        result = await provider.swap(
            quote, wallet, private_key, slippage_percent=0.5
        )
    """

    def __init__(
        self,
        chain_config: BSCConfig | None = None,
        **kwargs: Any,
    ) -> None:
        kw = dict(kwargs)
        kw.setdefault("chain_id", 56)
        super().__init__(**kw)

        self._bsc_config = chain_config or BSC
        self._swap_service: DexSwapService | None = None

    # -- lifecycle override ---------------------------------------------------

    async def connect(self, **kwargs: Any) -> bool:
        """Connect using BSC RPC endpoints as defaults."""
        rpc_urls = kwargs.pop("rpc_urls", None) or list(self._bsc_config.rpc_urls)
        ok = await super().connect(rpc_urls=rpc_urls, **kwargs)
        if ok:
            self._swap_service = DexSwapService(self)
        return ok

    async def disconnect(self) -> None:
        self._swap_service = None
        await super().disconnect()

    # -- swap API -------------------------------------------------------------

    async def get_quote(
        self,
        token_address: str,
        amount_in_wei: int,
        *,
        is_buy: bool = True,
        dex_name: str = "pancakeswap",
    ) -> float:
        """Get a token price quote from a specific DEX.

        Args:
            token_address: Target token contract address.
            amount_in_wei: Simulated input (wei).
            is_buy: ``True`` → buy token with BNB; ``False`` → sell.
            dex_name: DEX name (``pancakeswap``, ``bakeryswap``, …).

        Returns:
            Token price in USD, or ``0.0`` on failure.
        """
        if self._swap_service is None:
            raise RuntimeError("Not connected — call connect() first")
        return await self._swap_service.get_price(
            token_address, amount_in_wei, is_buy=is_buy, dex_name=dex_name
        )

    async def find_best_route(
        self,
        token_address: str,
        amount_in_wei: int,
        *,
        is_buy: bool = True,
    ) -> Optional[RouteQuote]:
        """Find the DEX offering the best price for a trade.

        Args:
            token_address: Target token contract.
            amount_in_wei: Input amount (wei).
            is_buy: ``True`` → buying; ``False`` → selling.

        Returns:
            Best ``RouteQuote``, or ``None`` if all DEX routes fail.
        """
        if self._swap_service is None:
            raise RuntimeError("Not connected — call connect() first")
        return await self._swap_service.find_best_route(
            token_address, amount_in_wei, is_buy=is_buy
        )

    async def execute_swap(
        self,
        quote: RouteQuote,
        wallet_address: str,
        private_key: str,
        *,
        slippage_percent: float = 0.5,
    ) -> SwapResult:
        """Execute a swap through the best DEX route.

        Args:
            quote: Route quote from ``find_best_route``.
            wallet_address: Sender address.
            private_key: Sender private key.
            slippage_percent: Max acceptable slippage (default 0.5%).

        Returns:
            ``SwapResult`` with status and tx hash.
        """
        if self._swap_service is None:
            raise RuntimeError("Not connected — call connect() first")
        return await self._swap_service.execute_swap(
            quote, wallet_address, private_key,
            slippage_percent=slippage_percent,
        )

    # -- IExchangeProvider (order routing) ------------------------------------

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a market order routed through the best DEX.

        When *symbol* is a token address (starts with ``0x``) the
        order is executed as a DEX swap.  Otherwise falls back to
        the base class.
        """
        if symbol.startswith("0x"):
            amount_wei = int(amount * 10**18)
            is_buy = side.lower() == "buy"

            quote = await self.find_best_route(symbol, amount_wei, is_buy=is_buy)
            if quote is None:
                return {"id": "", "status": "failed", "error": "no route found"}

            # In production: resolve wallet from params
            wallet = (params or {}).get("wallet", "")
            pk = (params or {}).get("private_key", "")

            result = await self.execute_swap(quote, wallet, pk)
            return {
                "id": result.tx_hash,
                "status": "closed" if result.success else "failed",
                "amount": amount,
                "side": side,
                "symbol": symbol,
                "error": result.error,
            }
        return await super().create_order(symbol, side, order_type, amount, price, params)
