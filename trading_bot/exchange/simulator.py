"""
Standalone Trading Simulator - No broker connection required
Pure simulation with generated or historical data
"""

import random
import time
from typing import Optional, List, Dict, Any
from datetime import datetime

from trading_bot.core.models import Position, OrderSide, PositionSide


def calculate_profit(side: str, entry_price: float, current_price: float, volume: float, point_value: float = 0.01) -> float:
    """Calculate unrealized P&L"""
    if str(side).lower() == "buy" or str(side).lower() == "long":
        pips = (current_price - entry_price) / point_value
    else:
        pips = (entry_price - current_price) / point_value

    # XAU/USD: volume * 100 oz * $0.01 per pip
    contract_size = 100
    return pips * volume * contract_size * point_value


class SimulatorExchange:
    """
    Standalone simulator - no broker connection needed
    Uses simulated price data for pure backtesting/paper trading
    """

    def __init__(self, initial_balance: float = 100.0, symbol: str = "XAUUSDm"):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.symbol = symbol

        self.name = "Simulator"

        self.positions: List[Position] = []
        self.closed_positions: List[Dict] = []
        self.trades: List[Dict] = []
        self.position_counter = 0

        # Current simulated price
        self.current_price = 5000.0
        self.price_history: List[float] = []

        # Simulation parameters
        self.volatility = 0.5  # Price movement volatility
        self.trend = 0.0  # Price trend bias

    def get_price(self) -> float:
        """Get current simulated price"""
        return self.current_price

    def get_balance(self) -> float:
        """Get current balance"""
        return self.balance

    def get_equity(self) -> float:
        """Get equity (balance + unrealized P&L)"""
        unrealized = sum(p.unrealized_pnl for p in self.positions)
        return self.balance + unrealized

    def get_positions(self, symbol: str = None) -> List[Position]:
        """Get open positions"""
        return list(self.positions)

    def open_position(
        self,
        symbol: str,
        side: str,
        volume: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> Optional[str]:
        """Open a simulated position"""
        self.position_counter += 1

        # Use current price with small spread
        spread = 0.04
        if side == "buy":
            entry_price = self.current_price + spread / 2
        else:
            entry_price = self.current_price - spread / 2

        pos_side = PositionSide.LONG if str(side).lower() == "buy" else PositionSide.SHORT
        position = Position(
            id=str(self.position_counter),
            symbol=symbol,
            side=pos_side,  # store enum for backward compat; compare w/ .value for string
            entry_price=entry_price,
            amount=volume,
            current_price=self.current_price,
            unrealized_pnl=0.0,
            sl=sl if sl else 0.0,
            tp=tp if tp else 0.0,
        )

        self.positions.append(position)

        self.trades.append(
            {
                "time": datetime.now().isoformat(),
                "action": "open",
                "side": side,
                "symbol": symbol,
                "volume": volume,
                "price": entry_price,
            }
        )

        return position.id

    def close_position(self, position_id: str) -> bool:
        """Close a position by ID"""
        for i, pos in enumerate(list(self.positions)):
            if pos.id == position_id:
                # Calculate profit
                profit = calculate_profit(str(pos.side.value), pos.entry_price, self.current_price, pos.amount)

                self.balance += profit
                self.closed_positions.append({
                    "id": pos.id,
                    "profit": profit,
                    "close_price": self.current_price,
                    "close_time": time.time(),
                })
                
                self.positions.pop(i)

                self.trades.append(
                    {
                        "time": datetime.now().isoformat(),
                        "action": "close",
                        "position_id": position_id,
                        "price": self.current_price,
                        "profit": profit,
                    }
                )

                return True

        return False

    def modify_position(
        self, position_id: str, sl: float = None, tp: float = None
    ) -> bool:
        """Modify position SL/TP"""
        for pos in self.positions:
            if pos.id == position_id:
                if sl is not None:
                    pos.sl = sl
                if tp is not None:
                    pos.tp = tp
                return True
        return False

    def update_price(self, new_price: Optional[float] = None):
        """
        Update simulated price
        If new_price not provided, generates random walk
        """
        if new_price is not None:
            self.current_price = new_price
        else:
            # Random walk with trend
            change = random.gauss(self.trend, self.volatility)
            self.current_price += change

            # Keep price positive
            self.current_price = max(1.0, self.current_price)

        self.price_history.append(self.current_price)
        
        # Check SL/TP and Update Position PnL
        for pos in self.positions:
            pos.current_price = self.current_price
            pos_side_str = pos.side.value if hasattr(pos.side, 'value') else pos.side
            pos.unrealized_pnl = calculate_profit(pos_side_str, pos.entry_price, self.current_price, pos.amount)

        self._check_triggers()

    def _check_triggers(self):
        """Check and execute SL/TP"""
        for pos in list(self.positions):

            # Check SL
            if pos.sl:
                pos_side = pos.side.value if hasattr(pos.side, 'value') else pos.side
                if pos_side.lower() == "long" and self.current_price <= pos.sl:
                    # SL hit
                    print(f"[SL] {pos.symbol}: {self.current_price:.2f} <= {pos.sl:.2f}")
                    self.close_position(pos.id)
                elif pos_side.lower() == "short" and self.current_price >= pos.sl:
                    # SL hit
                    print(f"[SL] {pos.symbol}: {self.current_price:.2f} >= {pos.sl:.2f}")
                    self.close_position(pos.id)
                else:
                    pass
            # Check TP
            if pos.tp:
                pos_side2 = pos.side.value if hasattr(pos.side, 'value') else pos.side
                if pos_side2.lower() == "long" and self.current_price >= pos.tp:
                    self.close_position(pos.id)
                elif pos_side2.lower() == "short" and self.current_price <= pos.tp:
                    self.close_position(pos.id)

    def update_positions(self, current_price: float):
        """Update positions with new price"""
        self.current_price = current_price
        self._check_triggers()

    def get_stats(self) -> Dict[str, Any]:
        """Get trading statistics"""
        if not self.closed_positions:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_profit": 0.0,
                "total_loss": 0.0,
                "net_pnl": 0.0,
                "balance": self.balance,
                "equity": self.get_equity(),
            }

        wins = [p for p in self.closed_positions if p["profit"] > 0]
        losses = [p for p in self.closed_positions if p["profit"] <= 0]

        total_profit = sum(p["profit"] for p in wins)
        total_loss = sum(abs(p["profit"]) for p in losses)
        net_pnl = sum(p["profit"] for p in self.closed_positions)

        return {
            "total_trades": len(self.closed_positions),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": (len(wins) / len(self.closed_positions) * 100)
            if self.closed_positions
            else 0,
            "total_profit": total_profit,
            "total_loss": total_loss,
            "net_pnl": net_pnl,
            "balance": self.balance,
            "equity": self.get_equity(),
            "open_positions": len(self.positions),
        }

    def close(self):
        return None

    def print_report(self):
        """Print trading report"""
        stats = self.get_stats()

        print("\n" + "=" * 60)
        print("📊 SIMULATION REPORT")
        print("=" * 60)
        print(f"Initial Balance: ${self.initial_balance:.2f}")
        print(f"Final Balance:   ${stats['balance']:.2f}")
        print(f"Final Equity:    ${stats['equity']:.2f}")
        print(f"Net P&L:         ${stats['net_pnl']:+.2f}")
        print("-" * 60)
        print(f"Total Trades:    {stats['total_trades']}")
        print(f"Winning:         {stats['winning_trades']}")
        print(f"Losing:          {stats['losing_trades']}")
        if stats["total_trades"] > 0:
            print(f"Win Rate:        {stats['win_rate']:.1f}%")
            print(
                f"Profit Factor:   {stats['total_profit'] / stats['total_loss']:.2f}"
                if stats["total_loss"] > 0
                else "∞"
            )
        print("=" * 60)
