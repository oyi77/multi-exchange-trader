"""
Unit tests for :class:`TokenSniperStrategy` and :class:`SniperConfig`.
"""

from __future__ import annotations

import pytest

from trading_bot.core.models import Position, PositionSide, OrderSide
from trading_bot.strategy.dex.sniper import (
    SniperConfig,
    SnipeTarget,
    TokenSniperStrategy,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestSniperConfig:
    def test_defaults(self) -> None:
        cfg = SniperConfig()
        assert cfg.buy_amount_usd == 30.0
        assert cfg.slippage == 0.01
        assert cfg.max_concurrent == 3
        assert cfg.cooldown_seconds == 5.0
        assert cfg.stop_loss_pct == 0.2
        assert cfg.take_profit_pct == 0.3
        assert cfg.min_liquidity_usd == 1_000.0
        assert cfg.max_snipes_per_pair == 1

    def test_custom(self) -> None:
        cfg = SniperConfig(
            buy_amount_usd=100.0, max_concurrent=1, cooldown_seconds=1.0
        )
        assert cfg.buy_amount_usd == 100.0
        assert cfg.max_concurrent == 1
        assert cfg.cooldown_seconds == 1.0


# ---------------------------------------------------------------------------
# SnipeTarget
# ---------------------------------------------------------------------------


class TestSnipeTarget:
    def test_creation(self) -> None:
        import time

        t = SnipeTarget(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="MOON",
            dex_name="PancakeSwap",
            detected_at=time.time(),
        )
        assert t.pair_address == "0xpair"
        assert t.token_symbol == "MOON"


# ---------------------------------------------------------------------------
# Strategy — target queue
# ---------------------------------------------------------------------------


class TestTargetQueue:
    @pytest.fixture
    def strategy(self) -> TokenSniperStrategy:
        return TokenSniperStrategy(SniperConfig())

    @staticmethod
    def _make_target(
        pair: str = "0xpair", symbol: str = "MOON"
    ) -> SnipeTarget:
        return SnipeTarget(
            pair_address=pair,
            token_address="0xtoken",
            token_symbol=symbol,
            dex_name="PancakeSwap",
            detected_at=1000.0,
        )

    def test_add_target_queues(self, strategy: TokenSniperStrategy) -> None:
        t = self._make_target()
        strategy.add_target(t)
        assert strategy.targets_remaining() == 1

    def test_add_duplicate_pair_skips(
        self, strategy: TokenSniperStrategy
    ) -> None:
        t1 = self._make_target(pair="0xpair")
        t2 = self._make_target(pair="0xpair")
        strategy.add_target(t1)
        strategy.add_target(t2)
        assert strategy.targets_remaining() == 1

    def test_already_sniped_pair_skips(
        self, strategy: TokenSniperStrategy
    ) -> None:
        t = self._make_target(pair="0xsnipped")
        strategy._sniped_pairs.add("0xsnipped")
        strategy.add_target(t)
        assert strategy.targets_remaining() == 0


# ---------------------------------------------------------------------------
# Strategy — on_tick
# ---------------------------------------------------------------------------


class TestOnTick:
    @pytest.fixture
    def strategy(self) -> TokenSniperStrategy:
        """Config with no cooldown so tests don't wait."""
        return TokenSniperStrategy(SniperConfig(cooldown_seconds=0.0))

    @staticmethod
    def _make_target(
        pair: str = "0xpair", symbol: str = "MOON"
    ) -> SnipeTarget:
        return SnipeTarget(
            pair_address=pair,
            token_address="0xtoken",
            token_symbol=symbol,
            dex_name="PancakeSwap",
            detected_at=1000.0,
        )

    def test_no_targets_returns_none(
        self, strategy: TokenSniperStrategy
    ) -> None:
        assert strategy.on_tick(1.0, 0.99, 1.01, []) is None

    def test_snipe_emits_action(
        self, strategy: TokenSniperStrategy
    ) -> None:
        strategy.add_target(self._make_target())
        action = strategy.on_tick(1.0, 0.99, 1.01, [])
        assert action is not None
        assert action["action"] == "open"
        assert action["side"] == OrderSide.BUY
        assert action["amount"] == SniperConfig().buy_amount_usd
        assert action["token_address"] == "0xtoken"
        assert action["pair_address"] == "0xpair"

    def test_snipe_tracks_sniped_pair(
        self, strategy: TokenSniperStrategy
    ) -> None:
        strategy.add_target(self._make_target(pair="0xp1"))
        strategy.on_tick(1.0, 0.99, 1.01, [])
        assert "0xp1" in strategy._sniped_pairs
        assert strategy.targets_remaining() == 0

    def test_position_limit_skips(
        self, strategy: TokenSniperStrategy
    ) -> None:
        strategy.add_target(self._make_target())
        filled = [
            Position(
                id=f"p{i}",
                symbol="T",
                side=PositionSide.LONG.value,
                entry_price=1.0,
                amount=0.1,
            )
            for i in range(SniperConfig().max_concurrent)
        ]
        action = strategy.on_tick(1.0, 0.99, 1.01, filled)
        assert action is None

    def test_cooldown_respected(self) -> None:
        """With a non-zero cooldown the second call should return None."""
        strategy = TokenSniperStrategy(
            SniperConfig(cooldown_seconds=999.0)
        )
        strategy.add_target(self._make_target())
        # First tick — should fire
        action = strategy.on_tick(1.0, 0.99, 1.01, [])
        assert action is not None
        # Second tick — cooldown active
        action2 = strategy.on_tick(1.0, 0.99, 1.01, [])
        assert action2 is None

    def test_sl_tp_calculated_from_price(
        self, strategy: TokenSniperStrategy
    ) -> None:
        strategy.add_target(self._make_target())
        action = strategy.on_tick(100.0, 99.0, 101.0, [])
        assert action is not None
        expected_sl = 99.0 * (1 - SniperConfig().stop_loss_pct)
        expected_tp = 101.0 * (1 + SniperConfig().take_profit_pct)
        assert action["sl"] == pytest.approx(expected_sl)
        assert action["tp"] == pytest.approx(expected_tp)
