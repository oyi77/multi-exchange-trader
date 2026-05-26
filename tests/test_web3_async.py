"""Tests for AsyncWeb3 wrapper."""

import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from trading_bot.utils.web3_async import AsyncWeb3, AsyncWeb3TimeoutError


class TestEthWaitForTransactionReceipt:
    """Tests for the eth_wait_for_transaction_receipt async method."""

    @pytest.fixture
    def mock_web3(self):
        w3 = MagicMock()
        w3.eth.get_transaction_receipt = MagicMock()
        w3.eth.send_raw_transaction = MagicMock()
        return w3

    @pytest.fixture
    def aw3(self, mock_web3):
        return AsyncWeb3(mock_web3, max_workers=2, timeout=5)

    @pytest.mark.asyncio
    async def test_returns_receipt_when_found(self, aw3, mock_web3):
        """Should return receipt immediately when transaction is confirmed."""
        expected_receipt = {"status": 1, "transactionHash": b"\x01" * 32}
        mock_web3.eth.get_transaction_receipt.return_value = expected_receipt

        result = await aw3.eth_wait_for_transaction_receipt(
            b"\x01" * 32, timeout=10, poll_interval=0.1
        )

        assert result == expected_receipt
        mock_web3.eth.get_transaction_receipt.assert_called_once_with(b"\x01" * 32)

    @pytest.mark.asyncio
    async def test_polls_until_receipt_available(self, aw3, mock_web3):
        """Should poll repeatedly until receipt appears."""
        tx_hash = b"\x02" * 32
        mock_web3.eth.get_transaction_receipt.side_effect = [
            None,
            None,
            {"status": 1, "transactionHash": tx_hash},
        ]

        result = await aw3.eth_wait_for_transaction_receipt(
            tx_hash, timeout=10, poll_interval=0.05
        )

        assert result["status"] == 1
        assert mock_web3.eth.get_transaction_receipt.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_timeout_when_no_receipt(self, aw3, mock_web3):
        """Should raise AsyncWeb3TimeoutError if receipt never appears."""
        mock_web3.eth.get_transaction_receipt.return_value = None

        with pytest.raises(AsyncWeb3TimeoutError, match="timed out after"):
            await aw3.eth_wait_for_transaction_receipt(
                b"\x03" * 32, timeout=1, poll_interval=0.1
            )

    @pytest.mark.asyncio
    async def test_accepts_hex_string_tx_hash(self, aw3, mock_web3):
        """Should accept hex string as well as bytes tx_hash."""
        expected_receipt = {"status": 1}
        mock_web3.eth.get_transaction_receipt.return_value = expected_receipt

        result = await aw3.eth_wait_for_transaction_receipt(
            b"\x04" * 32, timeout=10, poll_interval=0.1
        )

        assert result["status"] == 1
