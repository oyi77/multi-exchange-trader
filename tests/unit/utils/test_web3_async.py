"""Tests for AsyncWeb3 wrapper.

TDD tests verifying:
- Wrapped sync calls return correct values via thread pool
- Timeout enforcement raises AsyncWeb3TimeoutError
- Retry logic with exponential backoff on transient failures
- Concurrent calls execute without blocking each other
"""

import asyncio
import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from trading_bot.utils.web3_async import AsyncWeb3, AsyncWeb3TimeoutError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_w3():
    """Create a mock Web3 instance with standard eth methods."""
    w3 = MagicMock()

    # eth.call
    w3.eth.call.return_value = b"\x00" * 32

    # eth.get_balance
    w3.eth.get_balance.return_value = 1_000_000_000_000_000_000  # 1 ETH in wei

    # eth.estimate_gas
    w3.eth.estimate_gas.return_value = 21_000

    # eth.account.sign_transaction + eth.send_raw_transaction
    signed_tx = MagicMock()
    signed_tx.raw_transaction = b"\xf8..."
    w3.eth.account.sign_transaction.return_value = signed_tx
    w3.eth.send_raw_transaction.return_value = b"\xab" * 32  # tx hash

    return w3


@pytest.fixture
def async_w3(mock_w3):
    """AsyncWeb3 with small timeout for test speed."""
    return AsyncWeb3(mock_w3, max_workers=2, timeout=5)


# ---------------------------------------------------------------------------
# test_async_call_returns: wrapped call returns correct value
# ---------------------------------------------------------------------------


class TestAsyncCallReturns:
    """Verify each wrapped method returns the value from the sync Web3 call."""

    @pytest.mark.asyncio
    async def test_eth_call_returns_bytes(self, async_w3, mock_w3):
        result = await async_w3.eth_call(
            to="0x1234567890abcdef1234567890abcdef12345678",
            data="0xdeadbeef",
        )
        assert result == b"\x00" * 32
        mock_w3.eth.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_eth_get_balance_returns_int(self, async_w3, mock_w3):
        result = await async_w3.eth_get_balance(
            "0x1234567890abcdef1234567890abcdef12345678"
        )
        assert result == 1_000_000_000_000_000_000
        mock_w3.eth.get_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_eth_estimate_gas_returns_int(self, async_w3, mock_w3):
        tx = {"to": "0xabc", "value": 0}
        result = await async_w3.eth_estimate_gas(tx)
        assert result == 21_000
        mock_w3.eth.estimate_gas.assert_called_once()

    @pytest.mark.asyncio
    async def test_eth_send_transaction_returns_tx_hash(self, async_w3, mock_w3):
        tx = {"to": "0xabc", "value": 0, "gas": 21_000}
        result = await async_w3.eth_send_transaction(tx, private_key="0xprivkey")
        assert result == b"\xab" * 32
        mock_w3.eth.account.sign_transaction.assert_called_once()
        mock_w3.eth.send_raw_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_contract_call_returns_value(self, async_w3):
        contract = MagicMock()
        fn_mock = MagicMock()
        fn_mock.return_value.call.return_value = 42
        contract.functions.balanceOf = fn_mock

        result = await async_w3.contract_call(
            contract, "balanceOf", "0xowner"
        )
        assert result == 42
        fn_mock.assert_called_once_with("0xowner")

    @pytest.mark.asyncio
    async def test_eth_call_passes_block_parameter(self, async_w3, mock_w3):
        await async_w3.eth_call(
            to="0xabc",
            data="0x1234",
            block_parameter="pending",
        )
        args = mock_w3.eth.call.call_args
        assert args[0][1] == "pending"


# ---------------------------------------------------------------------------
# test_timeout: call exceeding timeout raises AsyncWeb3TimeoutError
# ---------------------------------------------------------------------------


class TestTimeout:
    """Verify that calls exceeding the configured timeout raise properly."""

    @pytest.mark.asyncio
    async def test_timeout_raises(self, mock_w3):
        def slow_call(*args, **kwargs):
            time.sleep(3)
            return b"\x00"

        mock_w3.eth.call.side_effect = slow_call
        aw3 = AsyncWeb3(mock_w3, max_workers=1, timeout=0.5)

        with pytest.raises(AsyncWeb3TimeoutError, match="timed out"):
            await aw3.eth_call(to="0xabc", data="0x1234")

    @pytest.mark.asyncio
    async def test_custom_timeout_respected(self, mock_w3):
        """A call that finishes within a longer custom timeout should succeed."""
        def moderate_call(*args, **kwargs):
            time.sleep(0.3)
            return 500

        mock_w3.eth.get_balance.side_effect = moderate_call
        aw3 = AsyncWeb3(mock_w3, max_workers=1, timeout=2)

        result = await aw3.eth_get_balance("0xabc")
        assert result == 500


# ---------------------------------------------------------------------------
# test_retry_on_failure: retries after temporary failure
# ---------------------------------------------------------------------------


class TestRetryOnFailure:
    """Verify retry logic with exponential backoff on transient errors."""

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self, mock_w3):
        call_count = 0

        def flaky_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("RPC node unreachable")
            return b"\x00" * 32

        mock_w3.eth.call.side_effect = flaky_call
        aw3 = AsyncWeb3(mock_w3, max_workers=1, timeout=10, max_retries=3)

        result = await aw3.eth_call(to="0xabc", data="0x1234")
        assert result == b"\x00" * 32
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self, mock_w3):
        call_count = 0

        def sometimes_slow(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                time.sleep(3)  # will timeout
            return 21_000

        mock_w3.eth.estimate_gas.side_effect = sometimes_slow
        aw3 = AsyncWeb3(mock_w3, max_workers=2, timeout=0.5, max_retries=3)

        result = await aw3.eth_estimate_gas({"to": "0xabc"})
        assert result == 21_000
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self, mock_w3):
        mock_w3.eth.get_balance.side_effect = ConnectionError("dead node")
        aw3 = AsyncWeb3(mock_w3, max_workers=1, timeout=5, max_retries=3)

        with pytest.raises(ConnectionError, match="dead node"):
            await aw3.eth_get_balance("0xabc")

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self, mock_w3):
        mock_w3.eth.call.side_effect = ValueError("invalid argument")
        aw3 = AsyncWeb3(mock_w3, max_workers=1, timeout=5, max_retries=3)

        with pytest.raises(ValueError, match="invalid argument"):
            await aw3.eth_call(to="0xabc", data="0x1234")

        # Should only be called once - no retry on ValueError
        assert mock_w3.eth.call.call_count == 1

    @pytest.mark.asyncio
    async def test_configurable_retry_count(self, mock_w3):
        call_count = 0

        def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("nope")

        mock_w3.eth.call.side_effect = always_fail
        aw3 = AsyncWeb3(mock_w3, max_workers=1, timeout=5, max_retries=5)

        with pytest.raises(ConnectionError):
            await aw3.eth_call(to="0xabc", data="0x1234")

        assert call_count == 5


# ---------------------------------------------------------------------------
# test_concurrent_calls: multiple calls run without blocking
# ---------------------------------------------------------------------------


class TestConcurrentCalls:
    """Verify multiple async calls execute concurrently via the thread pool."""

    @pytest.mark.asyncio
    async def test_concurrent_calls_run_in_parallel(self, mock_w3):
        call_times = []

        def tracked_call(*args, **kwargs):
            start = time.monotonic()
            time.sleep(0.2)
            call_times.append(time.monotonic() - start)
            return b"\x00" * 32

        mock_w3.eth.call.side_effect = tracked_call
        aw3 = AsyncWeb3(mock_w3, max_workers=4, timeout=5)

        t0 = time.monotonic()
        results = await asyncio.gather(
            aw3.eth_call(to="0xa", data="0x1"),
            aw3.eth_call(to="0xb", data="0x2"),
            aw3.eth_call(to="0xc", data="0x3"),
            aw3.eth_call(to="0xd", data="0x4"),
        )
        elapsed = time.monotonic() - t0

        assert len(results) == 4
        assert all(r == b"\x00" * 32 for r in results)
        # 4 calls of 0.2s each should finish in ~0.2-0.5s if parallel,
        # not 0.8s+ if sequential
        assert elapsed < 0.8, f"Calls appear sequential: {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_mixed_method_concurrent_calls(self, mock_w3):
        def slow_balance(*args, **kwargs):
            time.sleep(0.15)
            return 1_000

        def slow_gas(*args, **kwargs):
            time.sleep(0.15)
            return 21_000

        mock_w3.eth.get_balance.side_effect = slow_balance
        mock_w3.eth.estimate_gas.side_effect = slow_gas
        aw3 = AsyncWeb3(mock_w3, max_workers=4, timeout=5)

        t0 = time.monotonic()
        balance, gas = await asyncio.gather(
            aw3.eth_get_balance("0xabc"),
            aw3.eth_estimate_gas({"to": "0xabc"}),
        )
        elapsed = time.monotonic() - t0

        assert balance == 1_000
        assert gas == 21_000
        assert elapsed < 0.5, f"Mixed calls not parallel: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional coverage for shutdown and default config."""

    @pytest.mark.asyncio
    async def test_default_timeout_is_30(self, mock_w3):
        aw3 = AsyncWeb3(mock_w3)
        assert aw3._timeout == 30

    @pytest.mark.asyncio
    async def test_default_max_retries_is_3(self, mock_w3):
        aw3 = AsyncWeb3(mock_w3)
        assert aw3._max_retries == 3

    @pytest.mark.asyncio
    async def test_default_max_workers_is_4(self, mock_w3):
        aw3 = AsyncWeb3(mock_w3)
        assert aw3._executor._max_workers == 4

    @pytest.mark.asyncio
    async def test_shutdown_cleans_executor(self, mock_w3):
        aw3 = AsyncWeb3(mock_w3, max_workers=2)
        aw3.shutdown()
        # After shutdown, the executor should be shut down
        assert aw3._executor._shutdown

    @pytest.mark.asyncio
    async def test_contract_call_missing_function_raises(self, async_w3):
        contract = MagicMock()
        contract.functions = MagicMock(spec=[])  # no attributes
        del contract.functions.nonexistent  # ensure AttributeError

        with pytest.raises(AttributeError):
            await async_w3.contract_call(contract, "nonexistent")
