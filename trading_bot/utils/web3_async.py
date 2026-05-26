"""Async wrapper for synchronous web3.py calls.

Converts blocking Web3 RPC operations into non-blocking async
coroutines using ``asyncio`` + ``concurrent.futures.ThreadPoolExecutor``.

Why ThreadPoolExecutor instead of web3.py's built-in AsyncHTTPProvider?
- ThreadPoolExecutor wraps *any* sync provider (IPC, HTTP, WS) unchanged.
- Web3's async provider has version-specific quirks and limited middleware
  support.  The thread-pool approach is simpler and more reliable for
  production use.

Usage::

    from web3 import Web3
    from trading_bot.utils.web3_async import AsyncWeb3

    w3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    aw3 = AsyncWeb3(w3, max_workers=4, timeout=30)

    balance = await aw3.eth_get_balance("0x...")
    gas = await aw3.eth_estimate_gas({"to": "0x...", "value": 0})
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

# Errors considered transient and eligible for retry.
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    OSError,
    TimeoutError,
)


class AsyncWeb3TimeoutError(Exception):
    """Raised when an RPC call exceeds the configured timeout."""


class AsyncWeb3:
    """Async facade over a synchronous :class:`web3.Web3` instance.

    Every public method off-loads the blocking call to a
    :class:`~concurrent.futures.ThreadPoolExecutor`, applies a per-call
    timeout, and retries on transient network errors with exponential
    back-off.

    Parameters
    ----------
    web3_instance:
        A fully-configured synchronous ``Web3`` object.
    max_workers:
        Thread-pool size (default **4**).
    timeout:
        Seconds before a single RPC attempt is cancelled (default **30**).
    max_retries:
        Total attempts per call, including the first one (default **3**).
    backoff_base:
        Base seconds for exponential back-off between retries (default **2**).
    """

    def __init__(
        self,
        web3_instance: Any,
        max_workers: int = 4,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_base: float = 2.0,
    ) -> None:
        self._w3 = web3_instance
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run *func* in the thread pool with ``self._timeout``.

        Raises :class:`AsyncWeb3TimeoutError` on expiry.
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor,
            lambda: func(*args, **kwargs),
        )
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            raise AsyncWeb3TimeoutError(
                f"Call to {getattr(func, '__qualname__', func)} "
                f"timed out after {self._timeout}s"
            )

    async def _run_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute *func* via :meth:`_run_sync`, retrying on transient errors.

        Non-retryable exceptions propagate immediately.
        """
        last_exc: BaseException | None = None

        for attempt in range(self._max_retries):
            try:
                return await self._run_sync(func, *args, **kwargs)
            except (*_RETRYABLE_EXCEPTIONS, AsyncWeb3TimeoutError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    delay = self._backoff_base ** attempt
                    logger.warning(
                        "Attempt %d/%d failed (%s: %s), retrying in %.1fs",
                        attempt + 1,
                        self._max_retries,
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted – re-raise the original (unwrapped) error.
        # If the root cause was a timeout, raise the underlying timeout;
        # if it was a ConnectionError, raise that so callers see the
        # original exception type.
        raise last_exc  # type: ignore[misc]

    async def eth_wait_for_transaction_receipt(
        self,
        tx_hash: bytes,
        timeout: int = 120,
        poll_interval: float = 2.0,
    ) -> Any:
        """Async ``eth_getTransactionReceipt`` with polling.

        Polls for a transaction receipt until either the transaction is
        confirmed or *timeout* seconds elapse.

        Parameters
        ----------
        tx_hash:
            Transaction hash (32 bytes).
        timeout:
            Max seconds to wait (default **120**).
        poll_interval:
            Seconds between polling attempts (default **2.0**).

        Returns
        -------
        dict
            Transaction receipt (same shape as web3.eth.wait_for_transaction_receipt).
        """
        deadline = asyncio.get_running_loop().time() + timeout

        while asyncio.get_running_loop().time() < deadline:
            receipt = await self._run_sync(
                self._w3.eth.get_transaction_receipt,
                tx_hash,
            )
            if receipt is not None:
                return receipt
            await asyncio.sleep(poll_interval)

        raise AsyncWeb3TimeoutError(
            f"wait_for_transaction_receipt({tx_hash.hex()}) "
            f"timed out after {timeout}s"
        )

    # ------------------------------------------------------------------
    # Public async wrappers
    # ------------------------------------------------------------------

    async def eth_call(
        self,
        to: str,
        data: str,
        block_parameter: str = "latest",
    ) -> bytes:
        """Async ``eth_call``.

        Parameters
        ----------
        to:
            Contract address.
        data:
            ABI-encoded call data.
        block_parameter:
            Block identifier (``"latest"``, ``"pending"``, hex number).

        Returns
        -------
        bytes
            Raw return data from the contract.
        """
        return await self._run_with_retry(
            self._w3.eth.call,
            {"to": to, "data": data},
            block_parameter,
        )

    async def eth_send_transaction(
        self,
        tx: dict[str, Any],
        private_key: str,
    ) -> bytes:
        """Sign and broadcast a transaction.

        Parameters
        ----------
        tx:
            Transaction dict (``to``, ``value``, ``gas``, etc.).
        private_key:
            Hex-encoded private key for signing.

        Returns
        -------
        bytes
            Transaction hash.
        """

        async def _sign_and_send() -> bytes:
            signed = await self._run_sync(
                self._w3.eth.account.sign_transaction,
                tx,
                private_key,
            )
            return await self._run_sync(
                self._w3.eth.send_raw_transaction,
                signed.raw_transaction,
            )

        last_exc: BaseException | None = None
        for attempt in range(self._max_retries):
            try:
                return await _sign_and_send()
            except (*_RETRYABLE_EXCEPTIONS, AsyncWeb3TimeoutError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    delay = self._backoff_base ** attempt
                    logger.warning(
                        "send_transaction attempt %d/%d failed (%s), "
                        "retrying in %.1fs",
                        attempt + 1,
                        self._max_retries,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def eth_get_balance(
        self,
        address: str,
        block_parameter: str = "latest",
    ) -> int:
        """Async ``eth_getBalance``.

        Returns
        -------
        int
            Balance in wei.
        """
        return await self._run_with_retry(
            self._w3.eth.get_balance,
            address,
            block_parameter,
        )

    async def eth_estimate_gas(self, tx: dict[str, Any]) -> int:
        """Async ``eth_estimateGas``.

        Returns
        -------
        int
            Estimated gas units.
        """
        return await self._run_with_retry(
            self._w3.eth.estimate_gas,
            tx,
        )

    async def contract_call(
        self,
        contract: Any,
        function_name: str,
        *args: Any,
    ) -> Any:
        """Call any read-only contract function asynchronously.

        Parameters
        ----------
        contract:
            A ``web3.contract.Contract`` instance.
        function_name:
            Name of the view/pure function (e.g. ``"balanceOf"``).
        *args:
            Positional arguments forwarded to the contract function.

        Returns
        -------
        Any
            Decoded return value.
        """
        fn = getattr(contract.functions, function_name)
        return await self._run_with_retry(
            lambda: fn(*args).call(),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the thread pool.

        Parameters
        ----------
        wait:
            Block until all pending futures finish (default ``True``).
        """
        self._executor.shutdown(wait=wait)
