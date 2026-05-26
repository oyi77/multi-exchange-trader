"""
Position domain model.
"""

from dataclasses import dataclass
from enum import Enum


class PositionSide(Enum):
    """Direction of an open position."""

    LONG = "long"
    SHORT = "short"


class PositionStatus(Enum):
    """Lifecycle states a position can be in."""

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"


@dataclass
class Position:
    """Represents an open (or recently closed) position.

    .. note::

       For backward compatibility with the legacy flat ``models.py``,
       the fields are named ``sl`` and ``tp``.  Forward-compatible
       aliases (``stop_loss``, ``take_profit``) are available as
       read-write properties.
    """

    id: str
    symbol: str
    side: str  # PositionSide value
    entry_price: float
    amount: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    sl: float = 0.0  # stop loss
    tp: float = 0.0  # take profit
    leverage: float = 1.0
    margin: float = 0.0
    status: str = "open"  # PositionStatus value
    open_time: int = 0
    close_time: int = 0
    exchange: str = ""

    # -- forward-compat property aliases -----------------------------------

    @property
    def stop_loss(self) -> float:
        return self.sl

    @stop_loss.setter
    def stop_loss(self, value: float) -> None:
        self.sl = value

    @property
    def take_profit(self) -> float:
        return self.tp

    @take_profit.setter
    def take_profit(self, value: float) -> None:
        self.tp = value
