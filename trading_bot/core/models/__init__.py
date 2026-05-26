"""
Domain models — value objects, entities, and aggregates.

Pure data structures with no infrastructure dependencies.
"""

from trading_bot.core.models.order import (
    OrderSide,
    OrderType,
    OrderStatus,
    SignalAction,
    TradeSignal,
    Order,
)
from trading_bot.core.models.position import (
    PositionSide,
    PositionStatus,
    Position,
)
from trading_bot.core.models.market_data import (
    Candle,
    OrderBook,
    MarketData,
)
from trading_bot.core.models.wallet import WalletBalance, WalletProfile
from dataclasses import dataclass

from trading_bot.core.models.trade import TradeMode, Trade
from trading_bot.core.models.market_data import Candle, OrderBook, MarketData

# Re-export legacy model types for backward compat.  These were defined
# in the old flat ``trading_bot/core/models.py`` which is now shadowed by
# this package.  Everything below this line is a thin re-export stub.
class OHLCV(Candle):
    """Legacy alias for :class:`Candle` — keep positional param order."""
    def __init__(self, timestamp: int, open_: float, high: float,
                 low: float, close: float, volume: float = 0.0) -> None:
        super().__init__(timestamp=timestamp, open=open_, high=high,
                         low=low, close=close, volume=volume)


@dataclass
class Balance:
    """Account balance snapshot (legacy flat-model compat)."""
    total: float = 0.0
    free: float = 0.0
    used: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def equity(self) -> float:
        return self.total + self.unrealized_pnl


__all__ = [
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "SignalAction",
    "TradeSignal",
    "Order",
    "PositionSide",
    "PositionStatus",
    "Position",
    "TradeMode",
    "Trade",
    "OHLCV",
    "Balance",
    "Candle",
    "OrderBook",
    "MarketData",
    "WalletBalance",
    "WalletProfile",
]
