"""
Unit tests for :class:`Registry` and :class:`RegistryError`.

Registration, lookup, duplication guards, and listing are all
exercised without any external dependencies.
"""

from __future__ import annotations

import pytest

from trading_bot.core.registry import Registry, RegistryError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyStrategy:
    pass


class _DummyExchange:
    pass


class _DummyDataProvider:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def reg() -> Registry:
    return Registry()


# ---------------------------------------------------------------------------
# Strategy registration
# ---------------------------------------------------------------------------


class TestRegisterStrategy:
    def test_register_and_get(self, reg: Registry) -> None:
        reg.register_strategy("dummy", _DummyStrategy)
        assert reg.get_strategy("dummy") is _DummyStrategy

    def test_duplicate_raises(self, reg: Registry) -> None:
        reg.register_strategy("dummy", _DummyStrategy)
        with pytest.raises(RegistryError, match="already registered"):
            reg.register_strategy("dummy", _DummyStrategy)

    def test_allow_overwrite_silently_replaces(self, reg: Registry) -> None:
        reg.register_strategy("dummy", _DummyStrategy)
        reg.register_strategy("dummy", int, allow_overwrite=True)
        assert reg.get_strategy("dummy") is int

    def test_missing_raises(self, reg: Registry) -> None:
        with pytest.raises(RegistryError, match="not registered"):
            reg.get_strategy("nonexistent")

    def test_list_empty(self, reg: Registry) -> None:
        assert reg.list_strategies() == []

    def test_list_sorted(self, reg: Registry) -> None:
        reg.register_strategy("z", _DummyStrategy)
        reg.register_strategy("a", _DummyStrategy)
        assert reg.list_strategies() == ["a", "z"]


# ---------------------------------------------------------------------------
# Exchange registration
# ---------------------------------------------------------------------------


class TestRegisterExchange:
    def test_register_and_get(self, reg: Registry) -> None:
        reg.register_exchange("bsc", _DummyExchange)
        assert reg.get_exchange("bsc") is _DummyExchange

    def test_duplicate_raises(self, reg: Registry) -> None:
        reg.register_exchange("bsc", _DummyExchange)
        with pytest.raises(RegistryError):
            reg.register_exchange("bsc", _DummyExchange)

    def test_allow_overwrite(self, reg: Registry) -> None:
        reg.register_exchange("bsc", _DummyExchange)
        reg.register_exchange("bsc", float, allow_overwrite=True)
        assert reg.get_exchange("bsc") is float

    def test_list_exchanges(self, reg: Registry) -> None:
        reg.register_exchange("bsc", _DummyExchange)
        reg.register_exchange("eth", _DummyExchange)
        assert reg.list_exchanges() == ["bsc", "eth"]


# ---------------------------------------------------------------------------
# Data-provider registration
# ---------------------------------------------------------------------------


class TestRegisterDataProvider:
    def test_register_and_get(self, reg: Registry) -> None:
        reg.register_data_provider("birdeye", _DummyDataProvider)
        assert reg.get_data_provider("birdeye") is _DummyDataProvider

    def test_duplicate_raises(self, reg: Registry) -> None:
        reg.register_data_provider("birdeye", _DummyDataProvider)
        with pytest.raises(RegistryError):
            reg.register_data_provider("birdeye", _DummyDataProvider)

    def test_list_providers(self, reg: Registry) -> None:
        reg.register_data_provider("birdeye", _DummyDataProvider)
        reg.register_data_provider("dextools", _DummyDataProvider)
        assert reg.list_data_providers() == ["birdeye", "dextools"]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_singleton_is_registry_instance(self) -> None:
        from trading_bot.core.registry import registry as singleton

        assert isinstance(singleton, Registry)

    def test_singleton_is_persistent(self) -> None:
        from trading_bot.core.registry import registry as s1
        from trading_bot.core.registry import registry as s2

        assert s1 is s2
