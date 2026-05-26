"""
Trade domain model.
"""

from dataclasses import dataclass
from enum import Enum


class TradeMode(Enum):
    """Trading mode / environment."""

    PAPER = "paper"
    FRONTEST = "frontest"
    REAL = "real"
    BACKTEST = "backtest"


@dataclass
class Trade:
    """Represents an executed trade (open or close leg)."""

    id: str
    symbol: str
    side: str
    price: float
    amount: float
    pnl: float = 0.0
    fee: float = 0.0
    timestamp: int = 0
    tx_hash: str = ""
    provider: str = ""
