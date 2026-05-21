import numpy as np


def calc_confidence_obi(obi, rsi_val, change_24h, volume, session='off_session',
                        weights=None):
    """Calculate confidence score 0-100 for OBI-based strategy (vilona-trader style).

    Args:
        obi: order book imbalance (-1 to 1)
        rsi_val: RSI value (0-100)
        change_24h: 24h price change percentage
        volume: 24h quote volume
        session: trading session name
        weights: optional weight overrides
    """
    w = weights or {
        'obi': 0.30, 'trend': 0.25, 'volume': 0.20, 'rsi': 0.15, 'season': 0.10
    }

    scores = {}
    scores['obi'] = min(100, abs(obi) * 200) * w['obi']
    scores['trend'] = min(100, abs(change_24h) * 10) * w['trend']
    scores['volume'] = min(100, volume / 10_000_000) * w['volume']
    rsi_score = 100 - abs(50 - rsi_val) * 2
    scores['rsi'] = max(0, rsi_score) * w['rsi']

    session_scores = {
        'asia_open': 70, 'london_open': 85, 'ny_open': 90, 'off_session': 30
    }
    scores['season'] = session_scores.get(session, 30) * w['season']

    return round(sum(scores.values()), 1)


def calc_confidence_zscore(z_h1, z_m15, rsi_val, vol_ratio, roc_6h, atr_pct,
                           funding_rate=0, direction='long'):
    """Calculate confidence score 0-100 for Z-score strategy (deriv engine style).

    Components (total 100):
        technical: 35 (z-score H1+M15, RSI)
        volume: 20 (vol vs avg)
        momentum: 15 (ROC, direction alignment)
        session: 10 (ATR regime)
        historical: 10 (neutral default)
        sentiment: 10 (funding rate contrarian)
    """
    # Technical
    technical = min(35, abs(z_h1) * 8 + abs(z_m15) * 4)
    if (direction == 'long' and rsi_val < 35) or (direction == 'short' and rsi_val > 65):
        technical = min(35, technical + 5)

    # Volume
    volume = min(20, vol_ratio * 8)

    # Momentum
    momentum = min(15, abs(roc_6h) * 1.5)
    if (direction == 'long' and roc_6h > 0) or (direction == 'short' and roc_6h < 0):
        momentum = min(15, momentum + 3)

    # Session/volatility
    if 0.3 <= atr_pct <= 1.2:
        session = 10
    elif atr_pct < 0.2 or atr_pct > 2.5:
        session = 2
    else:
        session = 6

    # Historical (neutral default)
    historical = 6

    # Sentiment via funding rate (contrarian)
    sentiment = 5
    if direction == 'short' and funding_rate > 0.0001:
        sentiment = 10
    elif direction == 'long' and funding_rate < -0.0001:
        sentiment = 10
    elif direction == 'short' and funding_rate < -0.0001:
        sentiment = 2
    elif direction == 'long' and funding_rate > 0.0001:
        sentiment = 2

    return round(technical + volume + momentum + session + historical + sentiment, 1)


def tier_for(score, tiers=None):
    """Map confidence score to leverage tier.

    Default tiers: [(threshold, leverage, pct_modal, name), ...]
    """
    t = tiers or [
        (50, 20, 0.05, 'HIGH_PROB'),
        (30, 10, 0.03, 'STANDARD'),
        (15, 5, 0.02, 'SPECULATIVE'),
        (0, 0, 0.00, 'SKIP'),
    ]
    for thr, lev, pct, name in t:
        if score >= thr:
            return {'tier': name, 'leverage': lev, 'pct_modal': pct}
    return {'tier': 'SKIP', 'leverage': 0, 'pct_modal': 0}
