"""Formal state machine for DEX trading strategies.

Provides a reusable state machine pattern with:
- Defined states and valid transitions
- Guard conditions that block transitions
- Side-effect actions on successful transitions
- Event-driven transition triggers
- JSON state persistence for crash recovery
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class DEXState(Enum):
    """All possible states in the DEX strategy lifecycle."""
    IDLE = auto()
    ANALYZING = auto()
    PENDING = auto()       # Tx submitted, waiting chain confirmation
    CONFIRMED = auto()     # Tx confirmed on chain
    EXECUTED = auto()      # Strategy action fully completed
    FAILED = auto()


# ---------------------------------------------------------------------------
# Import core errors (tests import from trading_bot.core.errors)
# ---------------------------------------------------------------------------

from trading_bot.core.errors import InvalidTransitionError, GuardConditionError


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

GuardFn = Callable[..., bool]
ActionFn = Callable[..., None]


class StateMachine:
    """Generic finite state machine for DEX strategies.

    Usage::

        sm = StateMachine()
        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, lambda: market_active())
        sm.add_action(DEXState.ANALYZING, DEXState.PENDING, lambda: log_tx())
        sm.transition(DEXState.ANALYZING)          # IDLE → ANALYZING
        sm.transition(DEXState.PENDING, tx_hash=…)  # ANALYZING → PENDING
    """

    # Allowable transitions
    _TRANSITIONS: Dict[DEXState, List[DEXState]] = {
        DEXState.IDLE: [DEXState.ANALYZING, DEXState.FAILED],
        DEXState.ANALYZING: [DEXState.PENDING, DEXState.IDLE, DEXState.FAILED],
        DEXState.PENDING: [DEXState.CONFIRMED, DEXState.FAILED],
        DEXState.CONFIRMED: [DEXState.EXECUTED, DEXState.FAILED],
        DEXState.EXECUTED: [DEXState.IDLE],            # reset for next cycle
        DEXState.FAILED: [DEXState.IDLE],               # retry
    }

    def __init__(self, initial_state: DEXState = DEXState.IDLE) -> None:
        self._state: DEXState = initial_state
        self._guards: Dict[Tuple[DEXState, DEXState], List[GuardFn]] = {}
        self._actions: Dict[Tuple[DEXState, DEXState], List[ActionFn]] = {}
        self._history: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> DEXState:
        return self._state

    # Alias for test compatibility
    def get_state(self) -> DEXState:
        return self._state

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_guard(self, from_state: DEXState, to_state: DEXState,
                  guard_fn: GuardFn) -> None:
        """Register a guard condition.

        The transition will be blocked if the guard returns False.
        """
        key = (from_state, to_state)
        self._guards.setdefault(key, []).append(guard_fn)

    def add_action(self, from_state: DEXState, to_state: DEXState,
                   action_fn: ActionFn) -> None:
        """Register a side-effect action executed on successful transition."""
        key = (from_state, to_state)
        self._actions.setdefault(key, []).append(action_fn)

    def enforce_guard(self, from_state: DEXState, to_state: DEXState) -> StateMachine:
        """Check all guards for a potential transition without executing it.

        Raises GuardConditionError if any guard would block.
        Raises InvalidTransitionError if the transition is not allowed at all.
        """
        self._check_valid(from_state, to_state)
        self._check_guards(from_state, to_state)
        return self

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition(self, target: DEXState, **kwargs: Any) -> StateMachine:
        """Attempt to transition to *target*.

        If the transition passes guard checks, all registered action callbacks
        are invoked with **kwargs.

        Raises:
            InvalidTransitionError: Transition not in the allowed set.
            GuardConditionError: A guard condition returned False.
        """
        from_state = self._state
        self._check_valid(from_state, target)
        self._check_guards(from_state, target, **kwargs)

        self._execute_actions(from_state, target, **kwargs)

        self._record(from_state, target, kwargs)

        self._state = target
        return self

    def reset(self) -> StateMachine:
        """Reset to IDLE state, clearing history."""
        self._state = DEXState.IDLE
        self._history.clear()
        return self

    # ------------------------------------------------------------------
    # Event-driven triggers
    # ------------------------------------------------------------------

    def on_price_update(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> StateMachine:
        """Handle price update event. Triggers IDLE→ANALYZING transition."""
        if self._state == DEXState.IDLE:
            payload = data or kwargs
            return self.transition(DEXState.ANALYZING, event_data=payload)
        return self

    def on_tx_confirmation(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> StateMachine:
        """Handle transaction confirmation event. Triggers PENDING→CONFIRMED."""
        if self._state == DEXState.PENDING:
            payload = data or kwargs
            return self.transition(DEXState.CONFIRMED, event_data=payload)
        return self

    def on_error(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> StateMachine:
        """Handle error event. Transitions current state to FAILED."""
        if self._state not in (DEXState.EXECUTED, DEXState.FAILED):
            payload = data or kwargs
            return self.transition(DEXState.FAILED, event_data=payload)
        return self

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, filepath: str | Path) -> None:
        """Serialize machine state to JSON."""
        data = {
            "state": self._state.name,
            "history": self._history,
            "metadata": {
                "created_at": self._history[0]["timestamp"] if self._history else "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        Path(filepath).write_text(json.dumps(data, indent=2))

    def load(self, filepath: str | Path) -> StateMachine:
        """Restore machine state from previously saved JSON."""
        data = json.loads(Path(filepath).read_text())
        self._state = DEXState[data["state"]]
        self._history = data.get("history", [])
        return self

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_valid(self, from_state: DEXState, target: DEXState) -> None:
        allowed = self._TRANSITIONS.get(from_state, [])
        if target not in allowed:
            raise InvalidTransitionError(
                message=f"Cannot transition from {from_state.name} → {target.name}",
                from_state=from_state.name,
                to_state=target.name,
            )

    def _check_guards(self, from_state: DEXState, target: DEXState,
                      **kwargs: Any) -> None:
        key = (from_state, target)
        for guard_fn in self._guards.get(key, []):
            name = getattr(guard_fn, "__name__", repr(guard_fn))
            if not guard_fn(from_state, target, **kwargs):
                raise GuardConditionError(
                    message=f"Guard '{name}' blocked transition {from_state.name} → {target.name}",
                    guard_name=name,
                    from_state=from_state.name,
                    to_state=target.name,
                )

    def _execute_actions(self, from_state: DEXState, target: DEXState,
                         **kwargs: Any) -> None:
        key = (from_state, target)
        for action_fn in self._actions.get(key, []):
            action_fn(from_state, target, **kwargs)

    def _record(self, from_state: DEXState, target: DEXState,
                data: Dict[str, Any]) -> None:
        self._history.append({
            "from": from_state.name,
            "to": target.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })
