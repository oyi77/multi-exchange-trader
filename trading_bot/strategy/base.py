"""
Abstract strategy interface
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Optional, List, Tuple

from trading_bot.core.models import Position, OrderSide


class Strategy(ABC):
    """Base class for all strategies"""
    
    def __init__(self, config):
        self.config = config
    
    @abstractmethod
    def on_tick(self, price: float, bid: float, ask: float,
                positions: List[Position], timestamp: Optional[int] = None) -> Optional[Dict]:
        """
        Returns action dict or None:
        {'action': 'open', 'side': OrderSide.BUY, 'amount': 0.1, 'sl': 50000}
        {'action': 'close', 'position_id': 'pos_1'}
        """
        pass
    
    def get_point_value(self, price: float) -> float:
        """Deprecated: use get_pip_value"""
        return self.get_pip_value(price)

    def get_pip_value(self, price: float) -> float:
        """Returns 0.01 for XAU/JPY and 0.0001 for others"""
        return 0.01 if price < 100 or price > 1500 else 0.0001

    def calculate_auto_lot(self, equity: float, risk_percent: float, sl_pips: float, price: float = 2000.0) -> float:
        """Calculate lot size based on risk percentage and SL distance"""
        if sl_pips <= 0 or risk_percent <= 0:
            return getattr(self.config, 'lots', 0.01)
            
        risk_amount = equity * (risk_percent / 100.0)
        pip_value = self.get_pip_value(price)
        
        # Standard lot value (usually $10 per pip for 1.0 lot in Forex, 
        # but for Gold it's different. We'll use a simplified model)
        # For XAUUSD, 0.01 lot = $0.01 per 0.01 price change (1 pip)
        # So 1.0 lot = $1.00 per pip. 
        # Actually, standard XAUUSD 1.0 lot = $100 per $1 change = $1 per 0.01 change.
        # Let's normalize to: risk_amount / (sl_pips * point_cost_per_pip)
        
        # simplified: 0.01 lot risks $0.01 per pip (0.01 price change)
        # lot = risk_amount / (sl_pips * multiplier)
        # For XAU: 0.01 lot -> 1 pip = $0.01. 1.0 lot -> 1 pip = $1.00.
        lot = risk_amount / sl_pips
        return max(0.01, round(lot, 2))

    def is_session_active(self, timestamp: Optional[int] = None) -> bool:
        """Check if trading is allowed in current session"""
        # Timestamps may be in milliseconds (>1e10) — normalize to seconds
        if timestamp and timestamp > 1e10:
            timestamp = timestamp // 1000
        dt = datetime.fromtimestamp(timestamp) if timestamp else datetime.now()
        hour = dt.hour
        
        if hasattr(self.config, 'use_session_filter') and not self.config.use_session_filter:
            return True
            
        # Default sessions (GMT/UTC)
        # Asia: 00-08, London: 08-16, NY: 13-21
        use_asia = getattr(self.config, 'use_asia_session', True)
        use_london = getattr(self.config, 'use_london_open', True)
        use_ny = getattr(self.config, 'use_ny_session', True)
        
        if use_asia and (0 <= hour < 8): return True
        if use_london and (8 <= hour < 16): return True
        if use_ny and (13 <= hour < 21): return True
        
        return False
