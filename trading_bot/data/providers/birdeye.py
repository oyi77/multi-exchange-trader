"""
Birdeye data provider — on-chain token market data via Birdeye API.

Implements ``IDataProvider`` to supply token prices, metadata, OHLCV
history, and trending-token lists for BSC and other supported chains.

Requires a Birdeye API key set via the ``BIRDEYE_API_KEY`` environment
variable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from dotenv import load_dotenv

from trading_bot.core.interfaces import IDataProvider

load_dotenv()

logger = logging.getLogger(__name__)

BIRDEYE_BASE = "https://public-api.birdeye.so"
BIRDEYE_DEFI_TOKENLIST = f"{BIRDEYE_BASE}/defi/tokenlist"


class BirdeyeProvider(IDataProvider):
    """Market-data provider backed by the Birdeye public API.

    Args:
        api_key: Birdeye API key.  Falls back to ``BIRDEYE_API_KEY``
                 env var if not provided.
        default_chain: Chain identifier passed in ``x-chain`` header.
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_chain: str = "bsc",
    ) -> None:
        self._api_key = api_key or os.getenv("BIRDEYE_API_KEY", "")
        self._default_chain = default_chain
        self._session: aiohttp.ClientSession | None = None

    # -- lifecycle ------------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Release the HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # -- IDataProvider --------------------------------------------------------

    async def get_token_price(
        self, token: str, vs_currency: str = "usd"
    ) -> float:
        """Get current token price via Birdeye price endpoint.

        Args:
            token: Contract address or token symbol.
            vs_currency: Not used by Birdeye (always USD).

        Returns:
            Current price as a float, or ``0.0`` on failure.
        """
        token = token.strip()
        session = await self._ensure_session()

        # Try price endpoint
        url = f"{BIRDEYE_BASE}/defi/price?address={token}"
        headers = self._headers()

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = data.get("data", {}).get("value")
                    if price is not None:
                        return float(price)
        except Exception as exc:
            logger.debug("Birdeye price fetch failed for %s: %s", token, exc)

        return 0.0

    async def get_token_info(self, token: str) -> Dict[str, Any]:
        """Fetch token metadata from Birdeye.

        Returns:
            Dict with keys ``address``, ``symbol``, ``name``,
            ``decimals``, ``chain``, or an empty dict on failure.
        """
        token = token.strip()
        session = await self._ensure_session()

        url = f"{BIRDEYE_BASE}/defi/token_overview?address={token}"
        headers = self._headers()

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    info = data.get("data", {})
                    return {
                        "address": token,
                        "symbol": info.get("symbol", ""),
                        "name": info.get("name", ""),
                        "decimals": info.get("decimals", 18),
                        "chain": info.get("chain", self._default_chain),
                        "price": info.get("price", 0),
                        "mc": info.get("mc", 0),
                        "v24hUSD": info.get("v24hUSD", 0),
                        "liquidity": info.get("liquidity", 0),
                    }
        except Exception as exc:
            logger.debug("Birdeye token_info failed for %s: %s", token, exc)

        return {}

    async def get_market_data(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch historical price candles from Birdeye.

        Note:
            Birdeye's OHLCV endpoint requires a token address, not
            a trading pair symbol.

        Args:
            symbol: Token contract address.
            timeframe: Candle resolution (``1m``, ``5m``, ``15m``,
                      ``30m``, ``1h``, ``4h``, ``1d``).
            limit: Max candles (Birdeye caps at 1000).

        Returns:
            List of candle dicts (``timestamp``, ``open``, ``high``,
            ``low``, ``close``, ``volume``).
        """
        session = await self._ensure_session()
        url = (
            f"{BIRDEYE_BASE}/defi/history_price?"
            f"address={symbol}&"
            f"type={timeframe}&"
            f"time_from=0&"
            f"time_to={int(datetime.now(timezone.utc).timestamp())}"
        )
        headers = self._headers()

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    candles = []
                    for item in items[:limit]:
                        candles.append({
                            "timestamp": item.get("unixTime", 0),
                            "open": float(item.get("o", 0)),
                            "high": float(item.get("h", 0)),
                            "low": float(item.get("l", 0)),
                            "close": float(item.get("c", 0)),
                            "volume": float(item.get("v", 0)),
                        })
                    return candles
        except Exception as exc:
            logger.debug("Birdeye market-data fetch failed: %s", exc)

        return []

    async def get_trending_tokens(
        self, chain: str = "", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Return trending tokens sorted by 24 h USD volume.

        Args:
            chain: Chain filter (``bsc``, ``eth``, ``polygon``, …).
                   Empty string uses the default chain.
            limit: Max results.

        Returns:
            List of token info dicts.
        """
        chain = chain or self._default_chain
        session = await self._ensure_session()

        url = f"{BIRDEYE_BASE}/defi/tokenlist?sort_by=v24hUSD&sort_type=desc&offset=0&limit={limit}"
        headers = {
            "x-chain": chain,
            "X-API-KEY": self._api_key,
        }

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data.get("success"):
                        return []
                    data_obj = data.get("data") or {}
                    tokens = data_obj.get("tokens", [])
                    result = []
                    for t in tokens[:limit]:
                        result.append({
                            "address": t.get("address", ""),
                            "symbol": t.get("symbol", ""),
                            "name": t.get("name", ""),
                            "price": float(t.get("price", 0) or 0),
                            "v24hUSD": float(t.get("v24hUSD", 0) or 0),
                            "liquidity": float(t.get("liquidity", 0) or 0),
                            "mc": float(t.get("mc", 0) or 0),
                            "holderConcentration": float(
                                t.get("holderConcentration", 1) or 1
                            ),
                            "lastTradeUnixTime": t.get("lastTradeUnixTime", 0),
                            "chain": chain,
                        })
                    return result
                elif resp.status == 429:
                    logger.warning("Birdeye API rate-limited on trending fetch")
                else:
                    logger.warning(
                        "Birdeye trending fetch returned %d", resp.status
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
            logger.warning("Birdeye trending fetch failed: %s", exc)

        return []

    # -- internal helpers -----------------------------------------------------

    def _headers(self) -> dict:
        return {
            "x-chain": self._default_chain,
            "X-API-KEY": self._api_key,
            "accept": "application/json",
        }
