"""
Domain-specific exceptions.

Typed error hierarchy for trading operations:
- ExchangeError  → InsufficientBalanceError, ExchangeConnectionError, ExchangeTimeoutError
- StrategyError  → InvalidTransitionError, GuardConditionError, ConfigurationError
"""

from trading_bot.core.errors.exchange_errors import (
    ExchangeError,
    InsufficientBalanceError,
    ExchangeConnectionError,
    ExchangeTimeoutError,
)
from trading_bot.core.errors.strategy_errors import (
    StrategyError,
    InvalidTransitionError,
    GuardConditionError,
    ConfigurationError,
)

__all__ = [
    "ExchangeError",
    "InsufficientBalanceError",
    "ExchangeConnectionError",
    "ExchangeTimeoutError",
    "StrategyError",
    "InvalidTransitionError",
    "GuardConditionError",
    "ConfigurationError",
]
