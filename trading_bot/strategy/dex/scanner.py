"""
Token scanner strategy — identifies trading opportunities from market data.

Uses Birdeye (or other data providers) to scan for trending tokens,
analyze liquidity, volume, and price action, then produces trade
signals compatible with the engine's ``on_tick`` workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trading_bot.strategy.base import Strategy
from trading_bot.core.models import Position, OrderSide, PositionSide

logger = logging.getLogger(__name__)


@dataclass
class ScannerConfig:
    """Configuration for the token scanner strategy.

    Attributes:
        min_volume_24h: Minimum 24 h volume in USD to consider a token.
        min_liquidity_usd: Minimum pool liquidity in USD.
        max_holder_concentration: Max % held by top 10 holders (0-1).
        scan_interval_ticks: How many ``on_tick`` calls between scans.
        max_positions: Maximum concurrent positions.
        buy_amount_usd: USD amount to spend per buy signal.
        slippage: Max acceptable slippage (0-1).
        stop_loss_pct: Stop loss as fraction of entry (e.g. 0.1 = 10%).
        take_profit_pct: Take profit target as fraction of entry.
    """

    min_volume_24h: float = 50_000.0
    min_liquidity_usd: float = 10_000.0
    max_holder_concentration: float = 0.8
    scan_interval_ticks: int = 60
    max_positions: int = 5
    buy_amount_usd: float = 50.0
    slippage: float = 0.005
    stop_loss_pct: float = 0.15
    take_profit_pct: float = 0.5


@dataclass
class TokenSignal:
    """A detected trading opportunity."""

    token_address: str
    symbol: str
    price_usd: float
    volume_24h: float
    liquidity_usd: float
    score: float  # 0-1 confidence score
    reason: str = ""


class TokenScannerStrategy(Strategy):
    """Scans for token opportunities using a data provider.

    The strategy defers data fetching to an ``IDataProvider`` adapter
    (e.g. Birdeye), keeping analysis separate from data sourcing.

    Usage::

        from trading_bot.data.providers.birdeye import BirdeyeProvider
        from trading_bot.strategy.dex.scanner import (
            TokenScannerStrategy, ScannerConfig,
        )

        provider = BirdeyeProvider()
        strategy = TokenScannerStrategy(ScannerConfig(), provider)
    """

    def __init__(
        self,
        config: ScannerConfig | None = None,
        data_provider: Any | None = None,
    ) -> None:
        super().__init__(config or ScannerConfig())
        self._data_provider = data_provider
        self._tick_counter: int = 0
        self._candidates: list[TokenSignal] = []
        self._signals: list[TokenSignal] = []

    # -- data provider injection ---------------------------------------------

    def set_data_provider(self, provider: Any) -> None:
        """Inject or swap the data provider at runtime."""
        self._data_provider = provider

    # -- Strategy ABC ---------------------------------------------------------

    def on_tick(
        self,
        price: float,
        bid: float,
        ask: float,
        positions: List[Position],
        timestamp: int | None = None,
    ) -> dict | None:
        """Periodically scan for new token opportunities.

        This is a polling-based strategy — it scans every N ticks
        rather than on every price change.
        """
        if not self._data_provider:
            return None

        self._tick_counter += 1
        if self._tick_counter < self.config.scan_interval_ticks:
            return None
        self._tick_counter = 0

        # Check position limit
        active = len([p for p in positions if p.side in (PositionSide.LONG.value, PositionSide.SHORT.value)])
        if active >= self.config.max_positions:
            return None

        # Run scan (fire-and-forget async — in tick context we use
        # the cached results from the last background poll).
        # In production, spawn a background task that populates
        # ``self._candidates`` asynchronously.
        return None

    # -- public helpers -------------------------------------------------------

    async def refresh_candidates(self) -> list[TokenSignal]:
        """Fetch and score the latest token candidates (async).

        Call this from a background coroutine rather than from
        ``on_tick`` to avoid blocking the engine.
        """
        if not self._data_provider:
            logger.warning("No data provider configured — cannot scan")
            return []

        try:
            trending = await self._data_provider.get_trending_tokens(
                chain="bsc", limit=50
            )
        except Exception as exc:
            logger.warning("Trending-tokens fetch failed: %s", exc)
            return []

        scored: list[TokenSignal] = []
        for token in trending:
            signal = self._score_token(token)
            if signal is not None:
                scored.append(signal)

        scored.sort(key=lambda s: s.score, reverse=True) if scored else None
        self._candidates = scored

        # Emit signals for top candidates
        self._signals = scored[: min(3, len(scored))]
        return self._signals

    # -- scoring --------------------------------------------------------------

    def _score_token(self, token: dict) -> TokenSignal | None:
        """Score a single token against configured criteria.

        Args:
            token: Token info dict from ``IDataProvider.get_trending_tokens``.

        Returns:
            A ``TokenSignal`` if the token passes filters, else ``None``.
        """
        address = token.get("address", "")
        symbol = token.get("symbol", "?")
        volume = float(token.get("v24hUSD", 0) or 0)
        liquidity = float(token.get("liquidity", 0) or 0)
        price = float(token.get("price", 0) or 0)
        holder_concentration = float(token.get("holderConcentration", 1) or 1)

        # Filters
        if volume < self.config.min_volume_24h:
            return None
        if liquidity < self.config.min_liquidity_usd:
            return None
        if holder_concentration > self.config.max_holder_concentration:
            return None

        # Simple scoring heuristic
        score = min(1.0, (volume / 1_000_000) * 0.5 + (liquidity / 100_000) * 0.3)
        if holder_concentration < 0.5:
            score += 0.2

        return TokenSignal(
            token_address=address,
            symbol=symbol,
            price_usd=price,
            volume_24h=volume,
            liquidity_usd=liquidity,
            score=min(1.0, score),
            reason=f"vol={volume:.0f} liq={liquidity:.0f} score={score:.2f}",
        )

    # -- signal access --------------------------------------------------------

    def pop_signals(self) -> list[TokenSignal]:
        """Retrieve and clear pending trade signals."""
        signals = list(self._signals)
        self._signals.clear()
        return signals
