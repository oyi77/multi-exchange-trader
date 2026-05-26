"""
EVM DEX base exchange provider.

Chain-agnostic base class for interacting with EVM-compatible
decentralized exchanges via Web3.  Handles RPC connection management
with multi-endpoint failover, transaction building/signing, ERC-20
token introspection, and pending-transaction event listening.

Chain-specific logic (gas oracles, router addresses, etc.) belongs
in ``chain/`` sub-packages — not here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from trading_bot.core.errors import ExchangeConnectionError, ExchangeError
from trading_bot.core.interfaces import IExchangeProvider, IWalletManager

logger = logging.getLogger(__name__)

# Minimal ERC-20 ABI — only the read-only view functions we need.
ERC20_ABI: List[Dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
]


class EVMDEXProvider(IExchangeProvider):
    """Base provider for EVM-compatible DEX interactions.

    Wraps a :class:`web3.Web3` instance with:

    * Multi-RPC failover — automatically rotates through a list of
      RPC endpoints when one becomes unresponsive.
    * Transaction building & signing via an optional
      :class:`IWalletManager`.
    * Pending-transaction event listening for MEV-aware strategies.

    Sub-classes (e.g. ``BscDexProvider``) should override
    chain-specific defaults but **not** the core Web3 plumbing
    defined here.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        wallet_manager: Optional[IWalletManager] = None,
        chain_id: int = 56,
    ) -> None:
        self.wallet_manager = wallet_manager
        self.chain_id = chain_id

        # RPC state
        self.rpc_urls: List[str] = []
        self.w3: Any = None  # web3.Web3 — typed as Any to defer import
        self._fallback_index: int = 0
        self._connected: bool = False

        # Pending-tx listener
        self._pending_filter: Any = None
        self._listener_task: Optional[asyncio.Task[None]] = None
        self._pending_callbacks: List[Callable[[str], Any]] = []

    # ------------------------------------------------------------------
    # IExchangeProvider — lifecycle
    # ------------------------------------------------------------------

    async def connect(
        self, rpc_urls: Optional[List[str]] = None, **kwargs: Any
    ) -> bool:
        """Establish a Web3 connection with RPC failover.

        Args:
            rpc_urls: Ordered list of RPC endpoints to try.
                      The first reachable endpoint wins; remaining
                      URLs are kept as fallbacks.

        Returns:
            ``True`` when connected.

        Raises:
            ExchangeConnectionError: when *all* endpoints fail.
        """
        if rpc_urls:
            self.rpc_urls = list(rpc_urls)
            self._fallback_index = 0

        if not self.rpc_urls:
            raise ExchangeConnectionError(
                "No RPC URLs configured", exchange="evm-dex"
            )

        last_error: Optional[Exception] = None
        for idx in range(len(self.rpc_urls)):
            url = self.rpc_urls[(self._fallback_index + idx) % len(self.rpc_urls)]
            try:
                self.w3 = self._create_web3(url)
                if self.w3.is_connected():
                    self._fallback_index = (
                        (self._fallback_index + idx) % len(self.rpc_urls)
                    )
                    self._connected = True
                    logger.info("Connected to RPC %s (chain %d)", url, self.chain_id)
                    return True
                else:
                    logger.warning("RPC %s not reachable, trying next …", url)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("RPC %s failed: %s", url, exc)

        raise ExchangeConnectionError(
            f"All {len(self.rpc_urls)} RPC endpoints unreachable: {last_error}",
            exchange="evm-dex",
        )

    async def disconnect(self) -> None:
        """Clean up Web3 connection and stop listeners."""
        await self._stop_pending_listener()
        self.w3 = None
        self._connected = False
        logger.info("Disconnected from EVM chain %d", self.chain_id)

    # ------------------------------------------------------------------
    # IExchangeProvider — account
    # ------------------------------------------------------------------

    async def get_balance(self, asset: str = "") -> Dict[str, Any]:
        """Return native-token balance (in wei) for *asset* (an address).

        Args:
            asset: Hex wallet address.  If empty and a wallet manager
                   is attached, its primary address is used.

        Returns:
            ``{"balance": <int>, "decimals": 18, "symbol": "native"}``
        """
        self._require_connection()
        address = asset or await self._resolve_address()
        checksum = self.w3.to_checksum_address(address)
        balance_wei: int = self.w3.eth.get_balance(checksum)
        return {"balance": balance_wei, "decimals": 18, "symbol": "native"}

    # ------------------------------------------------------------------
    # IExchangeProvider — orders (no-ops at base DEX level)
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str = "",
        side: str = "",
        order_type: str = "",
        amount: float = 0.0,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:  # pragma: no cover — delegated to DEX service layer
        raise NotImplementedError(
            "place_order is handled by the DEX service layer, not the base provider"
        )

    async def cancel_order(
        self, order_id: str, symbol: str = ""
    ) -> bool:  # pragma: no cover
        raise NotImplementedError(
            "cancel_order is handled by the DEX service layer"
        )

    async def get_order_book(
        self, symbol: str, depth: int = 20
    ) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError(
            "get_order_book is handled by the DEX service layer"
        )

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError(
            "create_order is handled by the DEX service layer"
        )

    async def get_positions(
        self, symbol: str = ""
    ) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError(
            "get_positions is handled by the DEX service layer"
        )

    # ------------------------------------------------------------------
    # EVM-specific public API
    # ------------------------------------------------------------------

    async def estimate_gas(self, tx: Dict[str, Any]) -> int:
        """Estimate gas units required for *tx*.

        Args:
            tx: Transaction dict (``to``, ``value``, ``data``, …).

        Returns:
            Estimated gas as an integer.
        """
        self._require_connection()
        return int(self.w3.eth.estimate_gas(tx))

    async def send_transaction(self, tx: Dict[str, Any], private_key: str) -> str:
        """Sign and broadcast *tx*, returning the transaction hash.

        The method fills in ``nonce``, ``chainId``, and ``gas`` when
        they are missing from *tx*.

        Args:
            tx: Transaction dict.
            private_key: Hex-encoded private key for signing.

        Returns:
            Hex-encoded transaction hash.
        """
        self._require_connection()
        prepared = await self._prepare_transaction(tx)
        signed = self.w3.eth.account.sign_transaction(prepared, private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.to_hex(tx_hash)

    async def get_token_info(self, address: str) -> Dict[str, Any]:
        """Read ERC-20 token metadata.

        Args:
            address: Token contract address.

        Returns:
            ``{"name": …, "symbol": …, "decimals": …, "address": …}``
        """
        self._require_connection()
        checksum = self.w3.to_checksum_address(address)
        contract = self.w3.eth.contract(address=checksum, abi=ERC20_ABI)

        name: str = contract.functions.name().call()
        symbol: str = contract.functions.symbol().call()
        decimals: int = contract.functions.decimals().call()

        return {
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "address": checksum,
        }

    async def get_token_balance(self, token_address: str, wallet_address: str) -> int:
        """Query the ERC-20 balance of *wallet_address*.

        Returns:
            Raw token balance (before decimal adjustment).
        """
        self._require_connection()
        token_cs = self.w3.to_checksum_address(token_address)
        wallet_cs = self.w3.to_checksum_address(wallet_address)
        contract = self.w3.eth.contract(address=token_cs, abi=ERC20_ABI)
        return int(contract.functions.balanceOf(wallet_cs).call())

    # ------------------------------------------------------------------
    # RPC failover
    # ------------------------------------------------------------------

    async def _try_failover(self) -> bool:
        """Rotate to the next RPC endpoint.

        Returns ``True`` if a working endpoint was found.
        """
        if len(self.rpc_urls) <= 1:
            return False

        original_index = self._fallback_index
        for offset in range(1, len(self.rpc_urls)):
            idx = (original_index + offset) % len(self.rpc_urls)
            url = self.rpc_urls[idx]
            try:
                candidate = self._create_web3(url)
                if candidate.is_connected():
                    self.w3 = candidate
                    self._fallback_index = idx
                    logger.info("Failed over to RPC %s", url)
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ------------------------------------------------------------------
    # Pending-transaction listener
    # ------------------------------------------------------------------

    async def start_pending_listener(
        self, callback: Callable[[str], Any]
    ) -> None:
        """Subscribe to pending transactions and invoke *callback*
        for every new tx hash observed in the mempool.

        Args:
            callback: Invoked with each pending tx hash (hex string).
        """
        self._require_connection()
        self._pending_callbacks.append(callback)
        if self._listener_task is None or self._listener_task.done():
            self._listener_task = asyncio.create_task(self._poll_pending())

    async def _stop_pending_listener(self) -> None:
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        self._listener_task = None
        self._pending_callbacks.clear()

    async def _poll_pending(self) -> None:
        """Background loop that polls for new pending tx hashes."""
        try:
            self._pending_filter = self.w3.eth.filter("pending")
            while True:
                entries = self._pending_filter.get_new_entries()
                for tx_hash in entries:
                    hex_hash = self.w3.to_hex(tx_hash)
                    for cb in self._pending_callbacks:
                        try:
                            result = cb(hex_hash)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:  # noqa: BLE001
                            logger.exception("Pending-tx callback error")
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Pending-tx listener crashed")

    # ------------------------------------------------------------------
    # Transaction helpers
    # ------------------------------------------------------------------

    async def _prepare_transaction(self, tx: Dict[str, Any]) -> Dict[str, Any]:
        """Fill missing fields (nonce, chainId, gas) on *tx*."""
        prepared = dict(tx)

        if "chainId" not in prepared:
            prepared["chainId"] = self.chain_id

        if "nonce" not in prepared:
            from_addr = prepared.get("from") or await self._resolve_address()
            checksum = self.w3.to_checksum_address(from_addr)
            prepared["nonce"] = self.w3.eth.get_transaction_count(checksum)
            prepared["from"] = checksum

        if "gas" not in prepared:
            prepared["gas"] = self.w3.eth.estimate_gas(prepared)

        if "gasPrice" not in prepared and "maxFeePerGas" not in prepared:
            prepared["gasPrice"] = self.w3.eth.gas_price

        return prepared

    async def build_transaction(
        self,
        to: str,
        value: int = 0,
        data: bytes = b"",
    ) -> Dict[str, Any]:
        """Convenience builder for a simple transaction dict."""
        self._require_connection()
        from_addr = await self._resolve_address()
        tx: Dict[str, Any] = {
            "from": self.w3.to_checksum_address(from_addr),
            "to": self.w3.to_checksum_address(to),
            "value": value,
            "data": data,
            "chainId": self.chain_id,
        }
        return await self._prepare_transaction(tx)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_web3(self, rpc_url: str) -> Any:
        """Instantiate a :class:`web3.Web3` for *rpc_url*.

        Supports ``http(s)://`` and ``ws(s)://`` schemes.
        """
        from web3 import Web3
        from web3.providers import HTTPProvider, WebsocketProvider

        if rpc_url.startswith(("ws://", "wss://")):
            provider = WebsocketProvider(rpc_url)
        else:
            provider = HTTPProvider(rpc_url)
        return Web3(provider)

    def _require_connection(self) -> None:
        """Raise if :meth:`connect` has not succeeded yet."""
        if self.w3 is None or not self._connected:
            raise ExchangeConnectionError(
                "Not connected — call connect() first", exchange="evm-dex"
            )

    async def _resolve_address(self) -> str:
        """Return the wallet address, either from the wallet manager
        or raise a clear error.
        """
        if self.wallet_manager is not None:
            return await self.wallet_manager.get_address()
        raise ExchangeError("No wallet manager configured and no address provided")
