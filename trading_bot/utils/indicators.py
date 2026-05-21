"""
Technical indicators for trading strategies.
ATR, RSI, MACD, EMA calculations.
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ATRResult:
    """ATR calculation result"""

    atr: float
    high_band: float
    low_band: float


def calculate_atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Optional[float]:
    """
    Calculate Average True Range (ATR).

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: ATR period (default 14)

    Returns:
        ATR value or None if insufficient data
    """
    if len(closes) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i - 1])
        low_close = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(high_low, high_close, low_close))

    if len(true_ranges) < period:
        return None

    # First ATR is simple average
    atr = sum(true_ranges[:period]) / period

    # Subsequent ATRs use smoothing
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return atr


def calculate_atr_bands(price: float, atr: float, multiplier: float = 2.0) -> ATRResult:
    """
    Calculate ATR-based price bands.

    Args:
        price: Current price
        atr: ATR value
        multiplier: Band multiplier (default 2.0)

    Returns:
        ATRResult with atr, high_band, low_band
    """
    offset = atr * multiplier
    return ATRResult(atr=atr, high_band=price + offset, low_band=price - offset)


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI).

    Args:
        closes: List of close prices
        period: RSI period (default 14)

    Returns:
        RSI value (0-100) or None if insufficient data
    """
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    if len(gains) < period:
        return None

    # First average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed average
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Exponential Moving Average (EMA).

    Args:
        prices: List of prices
        period: EMA period

    Returns:
        EMA value or None if insufficient data
    """
    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)

    # First EMA is SMA
    ema = sum(prices[:period]) / period

    # Calculate EMA
    for i in range(period, len(prices)):
        ema = (prices[i] - ema) * multiplier + ema

    return ema


def calculate_macd(
    closes: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    Args:
        closes: List of close prices
        fast_period: Fast EMA period (default 12)
        slow_period: Slow EMA period (default 26)
        signal_period: Signal line period (default 9)

    Returns:
        Tuple of (macd_line, signal_line, histogram) or None
    """
    if len(closes) < slow_period + signal_period:
        return None

    fast_ema = calculate_ema(closes, fast_period)
    slow_ema = calculate_ema(closes, slow_period)

    if fast_ema is None or slow_ema is None:
        return None

    macd_line = fast_ema - slow_ema

    # Build MACD line history from slow_period onward
    multiplier_fast = 2 / (fast_period + 1)
    multiplier_slow = 2 / (slow_period + 1)

    # Seed EMAs using SMA of first slow_period values
    fast_ema_val = sum(closes[:fast_period]) / fast_period
    slow_ema_val = sum(closes[:slow_period]) / slow_period

    # Advance fast EMA to slow_period index
    for i in range(fast_period, slow_period):
        fast_ema_val = (closes[i] - fast_ema_val) * multiplier_fast + fast_ema_val

    # Now iterate from slow_period to end, collecting MACD history
    macd_history = []
    for i in range(slow_period, len(closes)):
        fast_ema_val = (closes[i] - fast_ema_val) * multiplier_fast + fast_ema_val
        slow_ema_val = (closes[i] - slow_ema_val) * multiplier_slow + slow_ema_val
        macd_history.append(fast_ema_val - slow_ema_val)

    # Signal line = 9-period EMA of MACD history
    signal_line = calculate_ema(macd_history, signal_period)
    if signal_line is None:
        return None

    # Use the final MACD value (last element of history)
    macd_line = macd_history[-1]
    histogram = macd_line - signal_line

    return (macd_line, signal_line, histogram)


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Simple Moving Average (SMA).

    Args:
        prices: List of prices
        period: SMA period

    Returns:
        SMA value or None if insufficient data
    """
    if len(prices) < period:
        return None

    return sum(prices[-period:]) / period


def calculate_bollinger_bands(
    closes: List[float], period: int = 20, std_dev: float = 2.0
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate Bollinger Bands.

    Args:
        closes: List of close prices
        period: SMA period (default 20)
        std_dev: Standard deviation multiplier (default 2.0)

    Returns:
        Tuple of (upper_band, middle_band, lower_band) or None
    """
    if len(closes) < period:
        return None

    middle = calculate_sma(closes, period)
    if middle is None:
        return None

    # Calculate standard deviation
    recent = closes[-period:]
    variance = sum((x - middle) ** 2 for x in recent) / period
    std = variance**0.5

    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)

    return (upper, middle, lower)


def calculate_donchian_channel(
    highs: List[float], lows: List[float], period: int = 20
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate Donchian Channel.

    Args:
        highs: List of high prices
        lows: List of low prices
        period: Lookback period (default 20)

    Returns:
        Tuple of (upper, middle, lower) or None
    """
    if len(highs) < period or len(lows) < period:
        return None

    upper = max(highs[-period:])
    lower = min(lows[-period:])
    middle = (upper + lower) / 2

    return (upper, middle, lower)


def get_trend_direction(
    fast_ema: float, slow_ema: float, rsi: Optional[float] = None
) -> int:
    """
    Determine trend direction from indicators.

    Args:
        fast_ema: Fast EMA value
        slow_ema: Slow EMA value
        rsi: Optional RSI value

    Returns:
        1 for bullish, -1 for bearish, 0 for neutral
    """
    if fast_ema > slow_ema:
        if rsi is not None and rsi > 70:
            return 0  # Overbought, reduce bullish bias
        return 1
    elif fast_ema < slow_ema:
        if rsi is not None and rsi < 30:
            return 0  # Oversold, reduce bearish bias
        return -1
    return 0


def calculate_zlema(closes: List[float], length: int) -> Optional[float]:
    """
    Calculate Zero Lag EMA (ZLEMA).

    Uses lag-compensated source: src + (src - src[lag]) where lag = floor((length-1)/2).
    Then applies standard EMA on the compensated source.

    Args:
        closes: List of close prices
        length: EMA period / band length

    Returns:
        ZLEMA value or None if insufficient data
    """
    lag = int((length - 1) / 2)
    min_needed = length + lag + 1

    if len(closes) < min_needed:
        return None

    # Build lag-compensated source: src[i] + (src[i] - src[i + lag])
    # Working with most-recent-last ordering
    compensated = []
    for i in range(lag, len(closes)):
        compensated.append(closes[i] + (closes[i] - closes[i - lag]))

    if len(compensated) < length:
        return None

    # Apply EMA on compensated source
    return calculate_ema(compensated, length)


def calculate_zlema_series(closes: List[float], length: int) -> Optional[List[float]]:
    """
    Calculate Zero Lag EMA series (returns recent values for crossover detection).

    Args:
        closes: List of close prices
        length: EMA period / band length

    Returns:
        List of recent ZLEMA values (last N values) or None
    """
    lag = int((length - 1) / 2)
    min_needed = length + lag + 1

    if len(closes) < min_needed:
        return None

    # Build lag-compensated source
    compensated = []
    for i in range(lag, len(closes)):
        compensated.append(closes[i] + (closes[i] - closes[i - lag]))

    if len(compensated) < length:
        return None

    # Calculate full EMA series on compensated source
    k = 2.0 / (length + 1.0)
    ema_val = sum(compensated[:length]) / length
    series = [ema_val]

    for i in range(length, len(compensated)):
        ema_val = (compensated[i] - ema_val) * k + ema_val
        series.append(ema_val)

    return series


def calculate_highest(values: List[float], period: int) -> Optional[float]:
    """
    Calculate highest value over a lookback period.

    Args:
        values: List of values
        period: Lookback period

    Returns:
        Highest value or None if insufficient data
    """
    if len(values) < period:
        return None

    return max(values[-period:])


def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    entry_price: float,
    stop_loss_price: float,
    pip_value: float = 10.0,
) -> float:
    """
    Calculate position size based on risk percentage.

    Args:
        account_balance: Account balance
        risk_percent: Risk percentage (e.g., 1.0 for 1%)
        entry_price: Entry price
        stop_loss_price: Stop loss price
        pip_value: Value per pip per lot (default $10 for XAU)

    Returns:
        Position size in lots
    """
    risk_amount = account_balance * (risk_percent / 100)
    stop_distance = abs(entry_price - stop_loss_price)

    if stop_distance == 0 or pip_value == 0:
        return 0.01

    # Calculate lots: risk_amount / (stop_pips * pip_value)
    stop_pips = stop_distance / 0.01  # Assuming 0.01 pip size
    lots = risk_amount / (stop_pips * pip_value)

    # Minimum lot size
    return max(0.01, round(lots, 2))


def zscore(arr, lookback=50):
    """Z-score of last value vs recent window."""
    import numpy as np

    if len(arr) < lookback + 1:
        return 0.0
    window = arr[-lookback - 1:-1]
    mu = np.mean(window)
    sd = np.std(window) or 1e-9
    return (arr[-1] - mu) / sd


def calc_spread(price_long, price_short):
    """Log spread between two prices."""
    import numpy as np

    return np.log(price_long / price_short)


def zscore_spread(spread_history, min_obs=30, lookback=60):
    """Z-score of spread history for pairs trading."""
    import numpy as np

    if len(spread_history) < min_obs:
        return 0
    arr = np.array(spread_history[-lookback:])
    mu, sd = np.mean(arr), np.std(arr) or 1e-9
    return (arr[-1] - mu) / sd
