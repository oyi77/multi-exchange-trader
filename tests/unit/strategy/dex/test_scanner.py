"""
Unit tests for :class:`TokenScannerStrategy` and :class:`ScannerConfig`.

All data-provider interaction is mocked so tests remain fast and
deterministic.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from trading_bot.core.models import Position, PositionSide, OrderSide
from trading_bot.strategy.dex.scanner import (
    ScannerConfig,
    TokenScannerStrategy,
    TokenSignal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_async(coro) -> Any:
    """Minimal async runner for simple coroutines."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestScannerConfig:
    def test_defaults(self) -> None:
        cfg = ScannerConfig()
        assert cfg.min_volume_24h == 50_000.0
        assert cfg.min_liquidity_usd == 10_000.0
        assert cfg.max_holder_concentration == 0.8
        assert cfg.scan_interval_ticks == 60
        assert cfg.max_positions == 5
        assert cfg.buy_amount_usd == 50.0
        assert cfg.slippage == 0.005
        assert cfg.stop_loss_pct == 0.15
        assert cfg.take_profit_pct == 0.5

    def test_custom(self) -> None:
        cfg = ScannerConfig(
            min_volume_24h=100_000,
            min_liquidity_usd=20_000,
            max_positions=3,
        )
        assert cfg.min_volume_24h == 100_000
        assert cfg.max_positions == 3


# ---------------------------------------------------------------------------
# Strategy — no data provider
# ---------------------------------------------------------------------------


class TestTokenScannerNoProvider:
    @pytest.fixture
    def strategy(self) -> TokenScannerStrategy:
        return TokenScannerStrategy(ScannerConfig())

    def test_on_tick_returns_none_without_provider(
        self, strategy: TokenScannerStrategy
    ) -> None:
        assert strategy.on_tick(100.0, 99.0, 101.0, []) is None

    def test_refresh_candidates_returns_empty_without_provider(
        self, strategy: TokenScannerStrategy
    ) -> None:
        result = _run_async(strategy.refresh_candidates())
        assert result == []

    def test_pop_signals_empty(self, strategy: TokenScannerStrategy) -> None:
        assert strategy.pop_signals() == []


# ---------------------------------------------------------------------------
# Strategy — with mocked data provider
# ---------------------------------------------------------------------------


class TestTokenScannerWithProvider:
    @pytest.fixture
    def mock_provider(self) -> AsyncMock:
        provider = AsyncMock()
        provider.get_trending_tokens = AsyncMock(
            return_value=[
                {
                    "address": "0xabc",
                    "symbol": "MOON",
                    "price": 0.1,
                    "v24hUSD": 500_000,
                    "liquidity": 100_000,
                    "holderConcentration": 0.2,
                },
                {
                    "address": "0xdef",
                    "symbol": "SUN",
                    "price": 0.5,
                    "v24hUSD": 200_000,
                    "liquidity": 50_000,
                    "holderConcentration": 0.6,
                },
            ]
        )
        return provider

    @pytest.fixture
    def strategy(
        self, mock_provider: AsyncMock
    ) -> TokenScannerStrategy:
        return TokenScannerStrategy(ScannerConfig(), mock_provider)

    def test_on_tick_returns_none_before_interval(
        self, strategy: TokenScannerStrategy
    ) -> None:
        """First tick should return None since scan_interval_ticks > 1."""
        assert strategy.on_tick(100.0, 99.0, 101.0, []) is None
        assert strategy._tick_counter == 1

    def test_refresh_candidates_returns_scored_signals(
        self, strategy: TokenScannerStrategy
    ) -> None:
        result = _run_async(strategy.refresh_candidates())
        # MOON (score ~0.81) and SUN (score ~0.3) both pass filters
        assert len(result) == 2
        assert result[0].symbol == "MOON"
        assert result[0].score > 0.7
        assert result[1].symbol == "SUN"

    def test_refresh_candidates_updates_internal_candidates(
        self, strategy: TokenScannerStrategy
    ) -> None:
        _run_async(strategy.refresh_candidates())
        assert len(strategy._candidates) == 2

    def test_pop_signals_after_refresh(
        self, strategy: TokenScannerStrategy
    ) -> None:
        _run_async(strategy.refresh_candidates())
        signals = strategy.pop_signals()
        assert len(signals) <= 3  # max 3 signals
        assert len(strategy._signals) == 0  # cleared

    def test_provider_failure_returns_empty(
        self, strategy: TokenScannerStrategy, mock_provider: AsyncMock
    ) -> None:
        mock_provider.get_trending_tokens.side_effect = ValueError(
            "API error"
        )
        result = _run_async(strategy.refresh_candidates())
        assert result == []

    def test_position_limit_skips_scan(
        self, strategy: TokenScannerStrategy
    ) -> None:
        strategy._tick_counter = ScannerConfig().scan_interval_ticks - 1
        filled_positions = [
            Position(
                id=f"p{i}",
                symbol="TOKEN",
                side=PositionSide.LONG.value,
                entry_price=1.0,
                amount=0.1,
            )
            for i in range(ScannerConfig().max_positions)
        ]
        # one more tick triggers the interval check
        result = strategy.on_tick(1.0, 0.99, 1.01, filled_positions)
        assert result is None


# ---------------------------------------------------------------------------
# Scoring (unit)
# ---------------------------------------------------------------------------


class TestScoring:
    @pytest.fixture
    def strategy(self) -> TokenScannerStrategy:
        return TokenScannerStrategy(ScannerConfig())

    def _score(
        self, strategy: TokenScannerStrategy, overrides: dict
    ) -> TokenSignal | None:
        token: dict = {
            "address": "0xtoken",
            "symbol": "T",
            "price": 1.0,
            "v24hUSD": 100_000,
            "liquidity": 50_000,
            "holderConcentration": 0.5,
        }
        token.update(overrides)
        return strategy._score_token(token)

    def test_passes_all_filters(
        self, strategy: TokenScannerStrategy
    ) -> None:
        signal = self._score(strategy, {})
        assert signal is not None
        assert signal.symbol == "T"
        assert signal.score > 0

    def test_low_volume_fails(
        self, strategy: TokenScannerStrategy
    ) -> None:
        signal = self._score(strategy, {"v24hUSD": 1_000})
        assert signal is None

    def test_low_liquidity_fails(
        self, strategy: TokenScannerStrategy
    ) -> None:
        signal = self._score(strategy, {"liquidity": 100})
        assert signal is None

    def test_high_concentration_fails(
        self, strategy: TokenScannerStrategy
    ) -> None:
        signal = self._score(strategy, {"holderConcentration": 0.95})
        assert signal is None

    def test_score_bounded_below_one(
        self, strategy: TokenScannerStrategy
    ) -> None:
        signal = self._score(
            strategy, {"v24hUSD": 10_000_000, "liquidity": 5_000_000}
        )
        assert signal is not None
        assert signal.score <= 1.0

    def test_low_concentration_bonus(
        self, strategy: TokenScannerStrategy
    ) -> None:
        low_conc = self._score(strategy, {"holderConcentration": 0.3})
        high_conc = self._score(strategy, {"holderConcentration": 0.7})
        assert low_conc is not None and high_conc is not None
        assert low_conc.score > high_conc.score
