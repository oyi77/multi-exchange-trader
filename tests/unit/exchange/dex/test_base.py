"""
Unit tests for :class:`EVMDEXProvider`.

Every Web3 interaction is mocked — no real RPC calls are made.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from trading_bot.core.errors import ExchangeConnectionError, ExchangeError
from trading_bot.exchange.dex.base import ERC20_ABI, EVMDEXProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_web3(*, connected: bool = True) -> MagicMock:
    """Build a ``MagicMock`` that behaves like a ``web3.Web3`` instance."""
    w3 = MagicMock()
    w3.is_connected.return_value = connected

    # eth namespace
    w3.eth.get_balance.return_value = 5_000_000_000_000_000_000  # 5 ETH
    w3.eth.estimate_gas.return_value = 21_000
    w3.eth.get_transaction_count.return_value = 42
    w3.eth.gas_price = 5_000_000_000  # 5 gwei

    # account signing
    signed = MagicMock()
    signed.raw_transaction = b"\xde\xad"
    w3.eth.account.sign_transaction.return_value = signed

    # send_raw_transaction returns a bytes tx hash
    w3.eth.send_raw_transaction.return_value = b"\xab" * 32

    # to_hex / to_checksum_address passthroughs
    w3.to_hex.side_effect = lambda x: "0x" + x.hex() if isinstance(x, bytes) else x
    w3.to_checksum_address.side_effect = lambda x: x  # identity for tests

    # contract mock for ERC-20 calls
    token_contract = MagicMock()
    token_contract.functions.name.return_value.call.return_value = "TestToken"
    token_contract.functions.symbol.return_value.call.return_value = "TTK"
    token_contract.functions.decimals.return_value.call.return_value = 18
    token_contract.functions.balanceOf.return_value.call.return_value = (
        1_000_000_000_000_000_000
    )
    w3.eth.contract.return_value = token_contract

    # pending filter
    pending_filter = MagicMock()
    pending_filter.get_new_entries.return_value = []
    w3.eth.filter.return_value = pending_filter

    return w3


@pytest.fixture
def mock_web3() -> MagicMock:
    return _make_mock_web3()


@pytest.fixture
def provider() -> EVMDEXProvider:
    return EVMDEXProvider(chain_id=56)


@pytest.fixture
def connected_provider(mock_web3: MagicMock) -> EVMDEXProvider:
    """Return an :class:`EVMDEXProvider` that is already connected."""
    p = EVMDEXProvider(chain_id=56)
    p.w3 = mock_web3
    p._connected = True
    p.rpc_urls = ["https://rpc1.example.com"]
    return p


# ---------------------------------------------------------------------------
# Test: connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(
        self, provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """connect() should set w3 and return True on a reachable RPC."""
        with patch.object(provider, "_create_web3", return_value=mock_web3):
            result = await provider.connect(["https://rpc1.example.com"])

        assert result is True
        assert provider.w3 is mock_web3
        assert provider._connected is True

    @pytest.mark.asyncio
    async def test_connect_no_urls_raises(self, provider: EVMDEXProvider) -> None:
        """connect() with no URLs must raise ExchangeConnectionError."""
        with pytest.raises(ExchangeConnectionError):
            await provider.connect([])

    @pytest.mark.asyncio
    async def test_connect_all_fail_raises(self, provider: EVMDEXProvider) -> None:
        """connect() must raise when every RPC endpoint is unreachable."""
        bad = _make_mock_web3(connected=False)
        with patch.object(provider, "_create_web3", return_value=bad):
            with pytest.raises(ExchangeConnectionError, match="unreachable"):
                await provider.connect(["https://bad1", "https://bad2"])

    @pytest.mark.asyncio
    async def test_connect_stores_rpc_list(
        self, provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """connect() should persist the full RPC list for later failover."""
        urls = ["https://a", "https://b", "https://c"]
        with patch.object(provider, "_create_web3", return_value=mock_web3):
            await provider.connect(urls)
        assert provider.rpc_urls == urls


# ---------------------------------------------------------------------------
# Test: disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_clears_state(
        self, connected_provider: EVMDEXProvider
    ) -> None:
        """disconnect() should nullify w3 and mark as not-connected."""
        await connected_provider.disconnect()

        assert connected_provider.w3 is None
        assert connected_provider._connected is False


# ---------------------------------------------------------------------------
# Test: get_balance
# ---------------------------------------------------------------------------


class TestGetBalance:
    @pytest.mark.asyncio
    async def test_get_balance_returns_wei(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """get_balance should return the native balance from Web3."""
        result = await connected_provider.get_balance(
            asset="0xAbCdEf0123456789AbCdEf0123456789AbCdEf00"
        )

        assert result["balance"] == 5_000_000_000_000_000_000
        assert result["decimals"] == 18
        assert result["symbol"] == "native"
        mock_web3.eth.get_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_balance_not_connected_raises(
        self, provider: EVMDEXProvider
    ) -> None:
        """get_balance before connect() must raise."""
        with pytest.raises(ExchangeConnectionError):
            await provider.get_balance(asset="0x1234")

    @pytest.mark.asyncio
    async def test_get_balance_uses_wallet_manager(self) -> None:
        """get_balance with empty asset should use wallet_manager address."""
        wm = AsyncMock()
        wm.get_address.return_value = "0xWalletAddr"

        p = EVMDEXProvider(wallet_manager=wm, chain_id=1)
        p.w3 = _make_mock_web3()
        p._connected = True

        result = await p.get_balance(asset="")
        wm.get_address.assert_awaited_once()
        assert result["balance"] == 5_000_000_000_000_000_000


# ---------------------------------------------------------------------------
# Test: estimate_gas
# ---------------------------------------------------------------------------


class TestEstimateGas:
    @pytest.mark.asyncio
    async def test_estimate_gas_returns_int(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """estimate_gas should forward to Web3 and return an int."""
        tx: Dict[str, Any] = {
            "to": "0xRecipient",
            "value": 0,
            "data": b"",
        }
        gas = await connected_provider.estimate_gas(tx)

        assert gas == 21_000
        mock_web3.eth.estimate_gas.assert_called_once_with(tx)

    @pytest.mark.asyncio
    async def test_estimate_gas_not_connected_raises(
        self, provider: EVMDEXProvider
    ) -> None:
        with pytest.raises(ExchangeConnectionError):
            await provider.estimate_gas({"to": "0x0"})


# ---------------------------------------------------------------------------
# Test: send_transaction
# ---------------------------------------------------------------------------


class TestSendTransaction:
    @pytest.mark.asyncio
    async def test_send_transaction_returns_hash(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """send_transaction should sign, broadcast, and return a hex hash."""
        tx: Dict[str, Any] = {
            "from": "0xSenderAddr",
            "to": "0xRecipientAddr",
            "value": 1_000,
            "data": b"",
        }
        tx_hash = await connected_provider.send_transaction(
            tx, private_key="0xdeadbeef"
        )

        assert tx_hash.startswith("0x")
        mock_web3.eth.account.sign_transaction.assert_called_once()
        mock_web3.eth.send_raw_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_transaction_fills_nonce(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """Missing nonce should be auto-populated from the chain."""
        tx: Dict[str, Any] = {
            "from": "0xSenderAddr",
            "to": "0xRecipientAddr",
            "value": 0,
        }
        await connected_provider.send_transaction(tx, private_key="0xkey")

        # sign_transaction receives the prepared tx with nonce filled in
        signed_call = mock_web3.eth.account.sign_transaction.call_args
        prepared_tx = signed_call[0][0]
        assert "nonce" in prepared_tx
        assert prepared_tx["nonce"] == 42

    @pytest.mark.asyncio
    async def test_send_transaction_fills_chain_id(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """Missing chainId should default to the provider's chain_id."""
        tx: Dict[str, Any] = {
            "from": "0xSender",
            "to": "0xRecipient",
            "value": 0,
        }
        await connected_provider.send_transaction(tx, private_key="0xkey")

        signed_call = mock_web3.eth.account.sign_transaction.call_args
        prepared_tx = signed_call[0][0]
        assert prepared_tx["chainId"] == 56


# ---------------------------------------------------------------------------
# Test: get_token_info
# ---------------------------------------------------------------------------


class TestGetTokenInfo:
    @pytest.mark.asyncio
    async def test_get_token_info_returns_metadata(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """get_token_info should read name/symbol/decimals from contract."""
        info = await connected_provider.get_token_info(
            "0xTokenContractAddress"
        )

        assert info["name"] == "TestToken"
        assert info["symbol"] == "TTK"
        assert info["decimals"] == 18
        assert "address" in info
        mock_web3.eth.contract.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_token_info_not_connected_raises(
        self, provider: EVMDEXProvider
    ) -> None:
        with pytest.raises(ExchangeConnectionError):
            await provider.get_token_info("0xToken")


# ---------------------------------------------------------------------------
# Test: connection failover
# ---------------------------------------------------------------------------


class TestConnectionFailover:
    @pytest.mark.asyncio
    async def test_connect_skips_bad_rpcs(self, provider: EVMDEXProvider) -> None:
        """connect() should skip unreachable RPCs and use the first good one."""
        bad = _make_mock_web3(connected=False)
        good = _make_mock_web3(connected=True)

        call_count = 0

        def create_web3_side_effect(url: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return bad if call_count <= 2 else good

        with patch.object(
            provider, "_create_web3", side_effect=create_web3_side_effect
        ):
            result = await provider.connect(
                ["https://bad1", "https://bad2", "https://good"]
            )

        assert result is True
        assert provider.w3 is good
        assert provider._fallback_index == 2

    @pytest.mark.asyncio
    async def test_try_failover_rotates(
        self, connected_provider: EVMDEXProvider
    ) -> None:
        """_try_failover should rotate to the next working endpoint."""
        connected_provider.rpc_urls = [
            "https://primary",
            "https://backup",
        ]
        connected_provider._fallback_index = 0

        backup_web3 = _make_mock_web3(connected=True)
        with patch.object(
            connected_provider, "_create_web3", return_value=backup_web3
        ):
            ok = await connected_provider._try_failover()

        assert ok is True
        assert connected_provider.w3 is backup_web3
        assert connected_provider._fallback_index == 1

    @pytest.mark.asyncio
    async def test_try_failover_single_rpc(
        self, connected_provider: EVMDEXProvider
    ) -> None:
        """Failover with only one RPC should return False."""
        connected_provider.rpc_urls = ["https://only"]
        ok = await connected_provider._try_failover()
        assert ok is False

    @pytest.mark.asyncio
    async def test_connect_exc_skips_to_next(
        self, provider: EVMDEXProvider
    ) -> None:
        """If _create_web3 raises, connect should try the next URL."""
        good = _make_mock_web3(connected=True)

        def side_effect(url: str) -> MagicMock:
            if url == "https://bad":
                raise ConnectionError("socket error")
            return good

        with patch.object(provider, "_create_web3", side_effect=side_effect):
            result = await provider.connect(["https://bad", "https://good"])

        assert result is True
        assert provider.w3 is good


# ---------------------------------------------------------------------------
# Test: pending transaction listener
# ---------------------------------------------------------------------------


class TestPendingListener:
    @pytest.mark.asyncio
    async def test_start_pending_listener_creates_task(
        self, connected_provider: EVMDEXProvider
    ) -> None:
        """start_pending_listener should register the callback and spawn a task."""
        cb = MagicMock()
        await connected_provider.start_pending_listener(cb)

        assert cb in connected_provider._pending_callbacks
        assert connected_provider._listener_task is not None
        # Clean up
        await connected_provider._stop_pending_listener()

    @pytest.mark.asyncio
    async def test_stop_pending_listener_clears(
        self, connected_provider: EVMDEXProvider
    ) -> None:
        """_stop_pending_listener should cancel the task and clear callbacks."""
        cb = MagicMock()
        await connected_provider.start_pending_listener(cb)
        await connected_provider._stop_pending_listener()

        assert connected_provider._listener_task is None
        assert connected_provider._pending_callbacks == []


# ---------------------------------------------------------------------------
# Test: build_transaction helper
# ---------------------------------------------------------------------------


class TestBuildTransaction:
    @pytest.mark.asyncio
    async def test_build_transaction_fills_fields(self) -> None:
        """build_transaction should return a fully populated tx dict."""
        wm = AsyncMock()
        wm.get_address.return_value = "0xMyWallet"

        p = EVMDEXProvider(wallet_manager=wm, chain_id=97)
        p.w3 = _make_mock_web3()
        p._connected = True

        tx = await p.build_transaction(to="0xTarget", value=100)

        assert tx["to"] == "0xTarget"
        assert tx["value"] == 100
        assert tx["chainId"] == 97
        assert "nonce" in tx
        assert "gas" in tx


# ---------------------------------------------------------------------------
# Test: token balance
# ---------------------------------------------------------------------------


class TestGetTokenBalance:
    @pytest.mark.asyncio
    async def test_get_token_balance(
        self, connected_provider: EVMDEXProvider, mock_web3: MagicMock
    ) -> None:
        """get_token_balance should call balanceOf via the ERC-20 ABI."""
        bal = await connected_provider.get_token_balance(
            token_address="0xTokenAddr",
            wallet_address="0xWalletAddr",
        )
        assert bal == 1_000_000_000_000_000_000
        mock_web3.eth.contract.assert_called_once()
