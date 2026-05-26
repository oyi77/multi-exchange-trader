"""
Core interfaces for exchange providers and pluggable strategies/data sources.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class Exchange(ABC):
    """Abstract base class for exchange providers"""
    
    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Get current price for symbol"""
        pass
    
    @abstractmethod
    def get_balance(self) -> float:
        """Get account balance"""
        pass
    
    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get open positions"""
        pass

    @abstractmethod
    def open_position(self, symbol: str, side: str, volume: float,
                      sl: Optional[float] = None, tp: Optional[float] = None) -> Optional[str]:
        """Open a new position"""
        pass

    @abstractmethod
    def close_position(self, ticket: str) -> bool:
        """Close a position by ticket"""
        pass

    @abstractmethod
    def modify_position(self, ticket: str, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        """Modify position SL/TP"""
        pass
    
    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        pass
    
    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str = "1h", 
                    limit: int = 100) -> List[Dict]:
        """Get historical candles"""
        pass
