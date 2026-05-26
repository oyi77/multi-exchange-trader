"""Shared configuration models for cross-bot risk management and exchange connectivity.

Format-agnostic dataclass models that define the canonical schema for risk
parameters, exchange connections, and trading-mode settings.  Each consuming
bot can load these models from its own config source (JSON, YAML, env vars,
etc.) via the provided ``from_dict()`` class methods.

Design decisions
~~~~~~~~~~~~~~~~
* **No external dependencies** – only stdlib ``dataclasses`` and ``typing``.
* **Strict ``__post_init__`` validation** – fail fast on bad config rather
  than propagating nonsense through a live trading loop.
* **``from_dict()`` tolerates missing keys** – callers get safe defaults for
  any key they omit, matching the progressive-disclosure style used in
  ``config/default.yml``.

Usage::

    from trading_bot.utils.shared_config import (
        RiskConfig,
        ExchangeConnectionConfig,
        TradingModeConfig,
    )

    risk = RiskConfig.from_dict(yaml_data.get("risk", {}))
    conn = ExchangeConnectionConfig.from_dict(yaml_data.get("exchange", {}))
    mode = TradingModeConfig.from_dict(yaml_data.get("trading", {}))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

__all__ = [
    "RiskConfig",
    "ExchangeConnectionConfig",
    "TradingModeConfig",
]

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Risk configuration
# ------------------------------------------------------------------

@dataclass
class RiskConfig:
    """Risk-management parameters shared across trading systems.

    Every numeric field is validated in ``__post_init__`` to catch
    configuration errors at startup rather than during live trading.

    Attributes
    ----------
    risk_percentage:
        Fraction of account equity risked per trade (0–1 inclusive).
        Example: ``0.02`` means 2 % risk per trade.
    max_drawdown_percent:
        Maximum allowable portfolio drawdown before trading halts
        (0–100 inclusive, expressed as a percentage).
    max_position_size_percent:
        Largest single position as a percentage of total equity
        (0–100 inclusive).
    max_daily_loss_percent:
        Maximum cumulative loss allowed in a single calendar day
        (0–100 inclusive).
    circuit_breaker_enabled:
        When ``True``, the bot pauses trading after hitting the daily-
        loss or drawdown threshold.
    circuit_breaker_cooldown_seconds:
        Seconds to wait after the circuit breaker trips before trading
        may resume.  Must be ≥ 0.
    """

    risk_percentage: float = 0.02
    max_drawdown_percent: float = 20.0
    max_position_size_percent: float = 5.0
    max_daily_loss_percent: float = 10.0
    circuit_breaker_enabled: bool = True
    circuit_breaker_cooldown_seconds: int = 300

    def __post_init__(self) -> None:
        # --- risk_percentage: 0-1 fraction ---
        if not 0 <= self.risk_percentage <= 1:
            raise ValueError(
                f"risk_percentage must be between 0 and 1, "
                f"got {self.risk_percentage}"
            )

        # --- max_drawdown_percent: 0-100 ---
        if not 0 <= self.max_drawdown_percent <= 100:
            raise ValueError(
                f"max_drawdown_percent must be between 0 and 100, "
                f"got {self.max_drawdown_percent}"
            )

        # --- max_position_size_percent: 0-100 ---
        if not 0 <= self.max_position_size_percent <= 100:
            raise ValueError(
                f"max_position_size_percent must be between 0 and 100, "
                f"got {self.max_position_size_percent}"
            )

        # --- max_daily_loss_percent: 0-100 ---
        if not 0 <= self.max_daily_loss_percent <= 100:
            raise ValueError(
                f"max_daily_loss_percent must be between 0 and 100, "
                f"got {self.max_daily_loss_percent}"
            )

        # --- circuit_breaker_cooldown_seconds: non-negative ---
        if self.circuit_breaker_cooldown_seconds < 0:
            raise ValueError(
                f"circuit_breaker_cooldown_seconds must be >= 0, "
                f"got {self.circuit_breaker_cooldown_seconds}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskConfig":
        """Create a ``RiskConfig`` from a plain dictionary.

        Missing keys fall back to the dataclass defaults so callers are
        free to provide only the overrides they care about.

        Parameters
        ----------
        data:
            Dictionary of risk-management parameters.  Keys should match
            the attribute names of this dataclass.

        Returns
        -------
        RiskConfig
            Validated configuration instance.

        Raises
        ------
        ValueError
            If any supplied value falls outside its valid range.
        """
        return cls(
            risk_percentage=float(
                data.get("risk_percentage", cls.risk_percentage)
            ),
            max_drawdown_percent=float(
                data.get("max_drawdown_percent", cls.max_drawdown_percent)
            ),
            max_position_size_percent=float(
                data.get(
                    "max_position_size_percent",
                    cls.max_position_size_percent,
                )
            ),
            max_daily_loss_percent=float(
                data.get("max_daily_loss_percent", cls.max_daily_loss_percent)
            ),
            circuit_breaker_enabled=bool(
                data.get(
                    "circuit_breaker_enabled", cls.circuit_breaker_enabled
                )
            ),
            circuit_breaker_cooldown_seconds=int(
                data.get(
                    "circuit_breaker_cooldown_seconds",
                    cls.circuit_breaker_cooldown_seconds,
                )
            ),
        )


# ------------------------------------------------------------------
# Exchange connection configuration
# ------------------------------------------------------------------

@dataclass
class ExchangeConnectionConfig:
    """Credentials and options for connecting to a single exchange.

    Attributes
    ----------
    name:
        Exchange identifier (e.g. ``"binance"``, ``"bybit"``, ``"okx"``).
        Lowercased and stripped during validation.
    api_key:
        Public API key.
    secret:
        API secret / private key.
    passphrase:
        Optional passphrase required by some exchanges (e.g. OKX).
    sandbox:
        When ``True``, connect to the exchange's testnet/sandbox
        environment.  Defaults to ``True`` for safety.
    """

    name: str = ""
    api_key: str = ""
    secret: str = ""
    passphrase: Optional[str] = None
    sandbox: bool = True

    def __post_init__(self) -> None:
        # Normalise the exchange name for consistent downstream lookups.
        self.name = self.name.strip().lower()

        if not self.name:
            raise ValueError("ExchangeConnectionConfig.name must not be empty")

        if not self.api_key:
            logger.warning(
                "ExchangeConnectionConfig for '%s' has an empty api_key; "
                "live trading will not be possible.",
                self.name,
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExchangeConnectionConfig":
        """Create an ``ExchangeConnectionConfig`` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary of exchange connection parameters.  The only
            required key is ``"name"``; all others have safe defaults.

        Returns
        -------
        ExchangeConnectionConfig
            Validated configuration instance.

        Raises
        ------
        ValueError
            If ``name`` is missing or empty.
        """
        return cls(
            name=str(data.get("name", cls.name)),
            api_key=str(data.get("api_key", cls.api_key)),
            secret=str(data.get("secret", cls.secret)),
            passphrase=data.get("passphrase", cls.passphrase),
            sandbox=bool(data.get("sandbox", cls.sandbox)),
        )


# ------------------------------------------------------------------
# Trading-mode configuration
# ------------------------------------------------------------------

_VALID_MODES = frozenset({"paper", "frontest", "real"})


@dataclass
class TradingModeConfig:
    """Environment and leverage settings for a trading session.

    Attributes
    ----------
    mode:
        Execution mode: ``"paper"`` (simulated fills), ``"frontest"``
        (forward-test with live feeds but no real orders), or ``"real"``
        (live execution).
    default_leverage:
        Leverage applied when a strategy does not specify its own.
        Must be ≥ 1 and ≤ ``max_leverage``.
    max_leverage:
        Upper bound for leverage across all strategies.  Must be ≥ 1.
    """

    mode: Literal["paper", "frontest", "real"] = "paper"
    default_leverage: int = 1
    max_leverage: int = 20

    def __post_init__(self) -> None:
        # --- mode ---
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"mode must be one of {sorted(_VALID_MODES)}, "
                f"got {self.mode!r}"
            )

        # --- max_leverage: ≥ 1 ---
        if self.max_leverage < 1:
            raise ValueError(
                f"max_leverage must be >= 1, got {self.max_leverage}"
            )

        # --- default_leverage: 1..max_leverage ---
        if not 1 <= self.default_leverage <= self.max_leverage:
            raise ValueError(
                f"default_leverage must be between 1 and {self.max_leverage}, "
                f"got {self.default_leverage}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingModeConfig":
        """Create a ``TradingModeConfig`` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary of trading-mode parameters.  Missing keys use
            safe defaults (paper mode, 1× leverage).

        Returns
        -------
        TradingModeConfig
            Validated configuration instance.

        Raises
        ------
        ValueError
            If ``mode`` is unrecognised or leverage values are invalid.
        """
        return cls(
            mode=str(data.get("mode", cls.mode)),  # type: ignore[arg-type]
            default_leverage=int(
                data.get("default_leverage", cls.default_leverage)
            ),
            max_leverage=int(data.get("max_leverage", cls.max_leverage)),
        )
