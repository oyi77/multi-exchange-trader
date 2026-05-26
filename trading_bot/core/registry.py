"""
Plugin registry for auto-discovery of strategies, exchanges, and data providers.

Provides a ``Registry`` singleton that components register with at import
time, so the engine can discover them without hard-coded import lists.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Type

from trading_bot.core.interfaces import (
    IExchangeProvider,
    IDataProvider,
    IStrategy,
)

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    """Raised on registration conflicts or lookup failures."""


class Registry:
    """Thread-safe (single-threaded) plugin registry.

    Usage::

        from trading_bot.core.registry import registry

        registry.register_strategy("MyStrategy", MyStrategy)
        registry.register_exchange("bsc_dex", BscDexProvider)
        registry.register_data_provider("birdeye", BirdeyeProvider)

        cls = registry.get_strategy("MyStrategy")
        exchange_cls = registry.get_exchange("bsc_dex")
    """

    def __init__(self) -> None:
        self._strategies: dict[str, Type[Any]] = {}
        self._exchanges: dict[str, Type[IExchangeProvider]] = {}
        self._data_providers: dict[str, Type[IDataProvider]] = {}

    # -- strategies -----------------------------------------------------------

    def register_strategy(
        self, name: str, cls: Type[Any], *, allow_overwrite: bool = False
    ) -> None:
        """Register *cls* as a strategy under *name*.

        Args:
            name: Strategy identifier (e.g. ``"scanner"``, ``"sniper"``).
            cls: Strategy class (should implement ``IStrategy`` or ``Strategy``).
            allow_overwrite: Silently replace an existing entry.

        Raises:
            RegistryError: If *name* is already registered and
                ``allow_overwrite`` is ``False``.
        """
        self._register(self._strategies, name, cls, allow_overwrite)

    def get_strategy(self, name: str) -> Type[Any]:
        """Look up a strategy by name.

        Raises:
            RegistryError: If *name* is not registered.
        """
        return self._get(self._strategies, name)

    def list_strategies(self) -> list[str]:
        """Return sorted list of registered strategy names."""
        return sorted(self._strategies)

    # -- exchanges ------------------------------------------------------------

    def register_exchange(
        self, name: str, cls: Type[IExchangeProvider], *, allow_overwrite: bool = False
    ) -> None:
        """Register *cls* as an exchange provider under *name*."""
        self._register(self._exchanges, name, cls, allow_overwrite)

    def get_exchange(self, name: str) -> Type[IExchangeProvider]:
        """Look up an exchange provider by name."""
        return self._get(self._exchanges, name)

    def list_exchanges(self) -> list[str]:
        """Return sorted list of registered exchange names."""
        return sorted(self._exchanges)

    # -- data providers -------------------------------------------------------

    def register_data_provider(
        self, name: str, cls: Type[IDataProvider], *, allow_overwrite: bool = False
    ) -> None:
        """Register *cls* as a data provider under *name*."""
        self._register(self._data_providers, name, cls, allow_overwrite)

    def get_data_provider(self, name: str) -> Type[IDataProvider]:
        """Look up a data provider by name."""
        return self._get(self._data_providers, name)

    def list_data_providers(self) -> list[str]:
        """Return sorted list of registered data-provider names."""
        return sorted(self._data_providers)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _register(
        store: dict[str, Any],
        name: str,
        cls: Any,
        allow_overwrite: bool,
    ) -> None:
        if name in store and not allow_overwrite:
            raise RegistryError(
                f"'{name}' is already registered as {store[name].__name__} "
                f"(use allow_overwrite=True to replace)"
            )
        store[name] = cls
        logger.debug("Registered %s as '%s'", cls.__name__, name)

    @staticmethod
    def _get(store: dict[str, Any], name: str) -> Any:
        try:
            return store[name]
        except KeyError:
            raise RegistryError(
                f"'{name}' is not registered. "
                f"Available: {', '.join(sorted(store)) or 'none'}"
            ) from None


# Module-level singleton — this is the canonical registry.
registry = Registry()
