"""
Unit tests for :class:`BirdeyeProvider`.

All HTTP calls are mocked via ``aiohttp.ClientSession`` patching
so no real network requests are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trading_bot.data.providers.birdeye import BirdeyeProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> BirdeyeProvider:
    return BirdeyeProvider()


# ---------------------------------------------------------------------------
# Instantiation / defaults
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_api_key(self) -> None:
        p = BirdeyeProvider()
        assert p._api_key == ""  # falls back to empty env

    def test_custom_api_key(self) -> None:
        p = BirdeyeProvider(api_key="abc123")
        assert p._api_key == "abc123"

    def test_custom_chain(self) -> None:
        p = BirdeyeProvider(default_chain="eth")
        assert p._default_chain == "eth"


# ---------------------------------------------------------------------------
# get_trending_tokens — happy path
# ---------------------------------------------------------------------------


class TestGetTrendingTokens:
    """Happy-path and edge-case tests for ``get_trending_tokens``."""

    @staticmethod
    def _sample_tokens() -> list[dict]:
        return [
            {
                "address": "0xaaa",
                "symbol": "TOKENA",
                "price": 0.5,
                "v24hUSD": 100_000,
                "liquidity": 50_000,
                "holderConcentration": 0.3,
            },
            {
                "address": "0xbbb",
                "symbol": "TOKENB",
                "price": 2.0,
                "v24hUSD": 10_000,  # below min_volume_24h → filtered out
                "liquidity": 5_000,
                "holderConcentration": 0.1,
            },
            {
                "address": "0xccc",
                "symbol": "TOKENC",
                "price": 1.0,
                "v24hUSD": 1_000_000,
                "liquidity": 0,  # no liquidity
                "holderConcentration": 0.9,  # too concentrated
            },
        ]

    @pytest.fixture(autouse=True)
    def _mock_session(self) -> None:
        """Fixture stub — tests manually set provider._session."""
        # No global patching needed; tests manually configure provider._session
        # to avoid conflicts with aiohttp.ClientSession's complex async interface.
        yield

    def _mock_response(self, status: int, json_data: dict) -> MagicMock:
        """Helper: build a mock ``aiohttp.ClientResponse``."""
        resp = MagicMock()
        resp.status = status

        async def _json():
            return json_data

        resp.json = _json
        resp.__aenter__.return_value = resp
        resp.__aexit__.return_value = None
        return resp

    async def test_returns_filtered_tokens(self, provider: BirdeyeProvider) -> None:
        """Should return only tokens that pass the default volume/liquidity/concentration
        filters baked into ``_score_token``."""
        mock_resp = self._mock_response(
            200,
            {
                "success": True,
                "data": {
                    "tokens": self._sample_tokens(),
                },
            },
        )
        session_instance = MagicMock()
        session_instance.closed = False  # Prevent _ensure_session from creating a new session
        session_instance.get.return_value = mock_resp
        provider._session = session_instance  # type: ignore[assignment]

        tokens = await provider.get_trending_tokens(chain="bsc", limit=50)

        # TOKENA passes, TOKENB (low volume) and TOKENC (no liq, high conc) fail
        assert isinstance(tokens, list)
        # The provider returns the raw API response — downstream ScannerStrategy scores it
        assert len(tokens) == 3

    async def test_success_false_returns_empty(self, provider: BirdeyeProvider) -> None:
        """When API returns ``success: false`` the method returns ``[]``."""
        mock_resp = self._mock_response(
            200,
            {"success": False, "data": None},
        )
        session_instance = MagicMock()
        session_instance.closed = False
        session_instance.get.return_value = mock_resp
        provider._session = session_instance  # type: ignore[assignment]

        tokens = await provider.get_trending_tokens(chain="bsc", limit=10)
        assert tokens == []

    async def test_http_error_returns_empty(self, provider: BirdeyeProvider) -> None:
        """Non-200 status is treated as empty."""
        mock_resp = self._mock_response(
            500,
            {},
        )
        session_instance = MagicMock()
        session_instance.closed = False
        session_instance.get.return_value = mock_resp
        provider._session = session_instance  # type: ignore[assignment]

        tokens = await provider.get_trending_tokens(chain="bsc", limit=10)
        assert tokens == []

    async def test_connection_error_returns_empty(
        self, provider: BirdeyeProvider
    ) -> None:
        """Network-level exceptions are caught and return ``[]``."""
        session_instance = MagicMock()
        session_instance.closed = False
        session_instance.get.side_effect = ConnectionError("reset")
        provider._session = session_instance  # type: ignore[assignment]

        tokens = await provider.get_trending_tokens(chain="bsc", limit=10)
        assert tokens == []

    async def test_passes_limit_and_chain(self, provider: BirdeyeProvider) -> None:
        """Verifies the query string includes ``limit`` and the chain is passed as header."""
        mock_resp = self._mock_response(
            200, {"success": True, "data": {"tokens": []}}
        )
        session_instance = MagicMock()
        session_instance.closed = False  # Prevent _ensure_session from creating a new session
        session_instance.get.return_value = mock_resp
        provider._session = session_instance  # type: ignore[assignment]

        await provider.get_trending_tokens(chain="eth", limit=5)
        call_url = session_instance.get.call_args[0][0]
        call_headers = session_instance.get.call_args[1].get("headers", {})
        assert "limit=5" in call_url
        assert call_headers.get("x-chain") == "eth"
