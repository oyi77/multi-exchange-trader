"""
Token sniper strategy — monitors for new liquidity pairs and executes
immediate buys on launch.

Designed for low-latency detection of newly listed tokens on DEX
protocols (PancakeSwap, etc.).  Relies on a data provider or direct
chain polling to detect pair-creation events.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from trading_bot.strategy.base import Strategy
from trading_bot.core.models import Position, OrderSide, PositionSide

logger = logging.getLogger(__name__)


@dataclass
class SniperConfig:
    """Configuration for the token sniper strategy.

    Attributes:
        buy_amount_usd: USD amount to spend per snipe.
        slippage: Max acceptable slippage (0-1, e.g. 0.01 = 1%).
        max_concurrent: Max simultaneous positions from sniping.
        cooldown_seconds: Minimum seconds between snipes.
        stop_loss_pct: Stop loss as fraction of entry (e.g. 0.2 = 20%).
        take_profit_pct: Take profit target as fraction of entry.
        min_liquidity_usd: Minimum pool liquidity at launch.
        max_snipes_per_pair: Only snipe each pair address once.
    """

    buy_amount_usd: float = 30.0
    slippage: float = 0.01
    max_concurrent: int = 3
    cooldown_seconds: float = 5.0
    stop_loss_pct: float = 0.2
    take_profit_pct: float = 0.3
    min_liquidity_usd: float = 1_000.0
    max_snipes_per_pair: int = 1


@dataclass
class SnipeTarget:
    """A newly detected pair to snipe."""

    pair_address: str
    token_address: str
    token_symbol: str
    dex_name: str
    detected_at: float  # unix timestamp


class TokenSniperStrategy(Strategy):
    """Monitors for new token pairs and executes snipes.

    The strategy itself is tick-driven; it relies on an **external
    watcher** (asynchronous event listener or polling loop) to
    populate fresh ``SnipeTarget`` instances via ``add_target``.

    Usage::

        from trading_bot.strategy.dex.sniper import (
            TokenSniperStrategy, SniperConfig,
        )

        strategy = TokenSniperStrategy(SniperConfig())

        # External watcher feeds targets:
        strategy.add_target(SnipeTarget(...))

        # Engine calls on_tick periodically:
        action = strategy.on_tick(price, bid, ask, positions, ts)
    """

    def __init__(self, config: SniperConfig | None = None) -> None:
        super().__init__(config or SniperConfig())
        self._targets: list[SnipeTarget] = []
        self._sniped_pairs: set[str] = set()
        self._last_snipe_at: float = 0.0

    # -- external target feed -------------------------------------------------

    def add_target(self, target: SnipeTarget) -> None:
        """Queue a new pair for sniping.

        Called by an external watcher (e.g. a background loop that
        polls the DEX factory for ``PairCreated`` events).
        """
        if target.pair_address in self._sniped_pairs:
            return
        # Deduplicate by pair address
        if any(t.pair_address == target.pair_address for t in self._targets):
            return
        self._targets.append(target)
        logger.info("Snipe target queued: %s (%s)", target.token_symbol, target.pair_address)

    def targets_remaining(self) -> int:
        """Number of targets still pending."""
        return len(self._targets)

    # -- Strategy ABC ---------------------------------------------------------

    def on_tick(
        self,
        price: float,
        bid: float,
        ask: float,
        positions: List[Position],
        timestamp: int | None = None,
    ) -> dict | None:
        """Check for pending snipe targets and emit a buy action.

        Returns an action dict when a target is ready, else ``None``.
        """
        if not self._targets:
            return None

        # Cooldown
        now = time.time()
        if now - self._last_snipe_at < self.config.cooldown_seconds:
            return None

        # Position limit
        active = len([p for p in positions if p.side in (PositionSide.LONG.value, PositionSide.SHORT.value)])
        if active >= self.config.max_concurrent:
            return None

        target = self._targets.pop(0)
        self._sniped_pairs.add(target.pair_address)
        self._last_snipe_at = now

        logger.info(
            "SNIPE: %s on %s — amount=%.2f USD, sl=%.0f%%, tp=%.0f%%",
            target.token_symbol,
            target.dex_name,
            self.config.buy_amount_usd,
            self.config.stop_loss_pct * 100,
            self.config.take_profit_pct * 100,
        )

        # Return a buy action — the engine will route it to the
        # exchange provider.  The actual swap execution uses the
        # DexSwapService under the hood via provider.create_order().
        return {
            "action": "open",
            "side": OrderSide.BUY,
            "amount": self.config.buy_amount_usd,
            "token_address": target.token_address,
            "pair_address": target.pair_address,
            "slippage": self.config.slippage,
            "sl": bid * (1 - self.config.stop_loss_pct) if bid > 0 else 0,
            "tp": ask * (1 + self.config.take_profit_pct) if ask > 0 else 0,
        }
