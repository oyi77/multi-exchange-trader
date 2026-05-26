"""Tests for the DEX strategy state machine."""

import json
import os
import tempfile

import pytest

from trading_bot.strategy.state_machine import DEXState, StateMachine
from trading_bot.core.errors import InvalidTransitionError, GuardConditionError


# ---------------------------------------------------------------------------
# 1. test_valid_transition
# ---------------------------------------------------------------------------
class TestValidTransition:
    """IDLE → ANALYZING → PENDING → CONFIRMED → EXECUTED full happy path."""

    def test_full_lifecycle(self):
        sm = StateMachine()
        assert sm.get_state() is DEXState.IDLE

        sm.transition(DEXState.ANALYZING)
        assert sm.get_state() is DEXState.ANALYZING

        sm.transition(DEXState.PENDING)
        assert sm.get_state() is DEXState.PENDING

        sm.transition(DEXState.CONFIRMED)
        assert sm.get_state() is DEXState.CONFIRMED

        sm.transition(DEXState.EXECUTED)
        assert sm.get_state() is DEXState.EXECUTED

    def test_transition_returns_self_for_chaining(self):
        sm = StateMachine()
        result = sm.transition(DEXState.ANALYZING)
        assert result is sm

    def test_executed_to_idle_resets_cycle(self):
        sm = StateMachine(initial_state=DEXState.EXECUTED)
        sm.transition(DEXState.IDLE)
        assert sm.get_state() is DEXState.IDLE

    def test_failed_to_idle_retry(self):
        sm = StateMachine(initial_state=DEXState.FAILED)
        sm.transition(DEXState.IDLE)
        assert sm.get_state() is DEXState.IDLE

    def test_analyzing_back_to_idle(self):
        sm = StateMachine(initial_state=DEXState.ANALYZING)
        sm.transition(DEXState.IDLE)
        assert sm.get_state() is DEXState.IDLE

    def test_idle_to_failed(self):
        sm = StateMachine()
        sm.transition(DEXState.FAILED)
        assert sm.get_state() is DEXState.FAILED


# ---------------------------------------------------------------------------
# 2. test_invalid_transition
# ---------------------------------------------------------------------------
class TestInvalidTransition:
    """IDLE → EXECUTED (and other illegal hops) must raise."""

    def test_idle_to_executed_raises(self):
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(DEXState.EXECUTED)
        assert "IDLE" in str(exc_info.value)
        assert "EXECUTED" in str(exc_info.value)

    def test_idle_to_confirmed_raises(self):
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(DEXState.CONFIRMED)

    def test_pending_to_idle_raises(self):
        sm = StateMachine(initial_state=DEXState.PENDING)
        with pytest.raises(InvalidTransitionError):
            sm.transition(DEXState.IDLE)

    def test_state_unchanged_after_invalid_transition(self):
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(DEXState.EXECUTED)
        assert sm.get_state() is DEXState.IDLE


# ---------------------------------------------------------------------------
# 3. test_guard_blocks_transition
# ---------------------------------------------------------------------------
class TestGuardBlocksTransition:
    """Guard returning False raises GuardConditionError and blocks."""

    def test_guard_returns_false_blocks(self):
        sm = StateMachine()

        def reject_guard(current, target, **kwargs):
            return False

        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, reject_guard)

        with pytest.raises(GuardConditionError):
            sm.transition(DEXState.ANALYZING)

        # State did not change
        assert sm.get_state() is DEXState.IDLE

    def test_guard_returns_true_allows(self):
        sm = StateMachine()

        def accept_guard(current, target, **kwargs):
            return True

        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, accept_guard)
        sm.transition(DEXState.ANALYZING)
        assert sm.get_state() is DEXState.ANALYZING

    def test_multiple_guards_all_must_pass(self):
        sm = StateMachine()
        calls = []

        def guard_a(current, target, **kwargs):
            calls.append("a")
            return True

        def guard_b(current, target, **kwargs):
            calls.append("b")
            return False  # blocks

        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, guard_a)
        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, guard_b)

        with pytest.raises(GuardConditionError):
            sm.transition(DEXState.ANALYZING)

        assert sm.get_state() is DEXState.IDLE

    def test_guard_receives_event_data(self):
        sm = StateMachine()
        captured = {}

        def capturing_guard(current, target, **kwargs):
            captured.update(kwargs)
            return True

        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, capturing_guard)
        sm.transition(DEXState.ANALYZING, event_data={"price": 42.0})

        assert captured.get("event_data") == {"price": 42.0}


# ---------------------------------------------------------------------------
# 4. test_action_executed
# ---------------------------------------------------------------------------
class TestActionExecuted:
    """Action functions are called on successful transition."""

    def test_action_called_on_transition(self):
        sm = StateMachine()
        action_log = []

        def log_action(current, target, **kwargs):
            action_log.append((current, target))

        sm.add_action(DEXState.IDLE, DEXState.ANALYZING, log_action)
        sm.transition(DEXState.ANALYZING)

        assert len(action_log) == 1
        assert action_log[0] == (DEXState.IDLE, DEXState.ANALYZING)

    def test_multiple_actions_executed_in_order(self):
        sm = StateMachine()
        order = []

        sm.add_action(DEXState.IDLE, DEXState.ANALYZING, lambda c, t, **kw: order.append("first"))
        sm.add_action(DEXState.IDLE, DEXState.ANALYZING, lambda c, t, **kw: order.append("second"))
        sm.transition(DEXState.ANALYZING)

        assert order == ["first", "second"]

    def test_action_not_called_on_guard_failure(self):
        sm = StateMachine()
        action_called = []

        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, lambda c, t, **kw: False)
        sm.add_action(DEXState.IDLE, DEXState.ANALYZING, lambda c, t, **kw: action_called.append(True))

        with pytest.raises(GuardConditionError):
            sm.transition(DEXState.ANALYZING)

        assert action_called == []

    def test_action_receives_event_data(self):
        sm = StateMachine()
        captured = {}

        def capture(current, target, **kwargs):
            captured.update(kwargs)

        sm.add_action(DEXState.IDLE, DEXState.ANALYZING, capture)
        sm.transition(DEXState.ANALYZING, event_data={"token": "WETH"})

        assert captured.get("event_data") == {"token": "WETH"}


# ---------------------------------------------------------------------------
# 5. test_persistence
# ---------------------------------------------------------------------------
class TestPersistence:
    """save → load round-trip preserves state and history."""

    def test_save_load_roundtrip(self):
        sm = StateMachine()
        sm.transition(DEXState.ANALYZING)
        sm.transition(DEXState.PENDING)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name

        try:
            sm.save(filepath)

            # Verify file is valid JSON
            with open(filepath) as f:
                data = json.load(f)
            assert data["state"] == "ANALYZING→PENDING" or data["state"] == "PENDING"

            sm2 = StateMachine()
            sm2.load(filepath)
            assert sm2.get_state() is DEXState.PENDING
        finally:
            os.unlink(filepath)

    def test_save_contains_history(self):
        sm = StateMachine()
        sm.transition(DEXState.ANALYZING)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name

        try:
            sm.save(filepath)
            with open(filepath) as f:
                data = json.load(f)

            assert "history" in data
            assert len(data["history"]) == 1
            assert data["history"][0]["from"] == "IDLE"
            assert data["history"][0]["to"] == "ANALYZING"
            assert "timestamp" in data["history"][0]
        finally:
            os.unlink(filepath)

    def test_save_contains_metadata(self):
        sm = StateMachine()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name

        try:
            sm.save(filepath)
            with open(filepath) as f:
                data = json.load(f)

            assert "metadata" in data
            assert "created_at" in data["metadata"]
            assert "updated_at" in data["metadata"]
        finally:
            os.unlink(filepath)

    def test_load_preserves_history(self):
        sm = StateMachine()
        sm.transition(DEXState.ANALYZING)
        sm.transition(DEXState.PENDING)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name

        try:
            sm.save(filepath)

            sm2 = StateMachine()
            sm2.load(filepath)
            assert len(sm2.history) == 2
        finally:
            os.unlink(filepath)


# ---------------------------------------------------------------------------
# 6. test_event_triggers
# ---------------------------------------------------------------------------
class TestEventTriggers:
    """Event methods trigger the correct transition."""

    def test_on_price_update_idle_to_analyzing(self):
        sm = StateMachine()
        sm.on_price_update({"price": 1800.50, "symbol": "WETH"})
        assert sm.get_state() is DEXState.ANALYZING

    def test_on_tx_confirmation_pending_to_confirmed(self):
        sm = StateMachine(initial_state=DEXState.PENDING)
        sm.on_tx_confirmation({"tx_hash": "0xabc", "block": 12345})
        assert sm.get_state() is DEXState.CONFIRMED

    def test_on_error_transitions_to_failed(self):
        for start in (DEXState.IDLE, DEXState.ANALYZING, DEXState.PENDING, DEXState.CONFIRMED):
            sm = StateMachine(initial_state=start)
            sm.on_error({"error": "timeout"})
            assert sm.get_state() is DEXState.FAILED

    def test_event_data_propagated_to_guards(self):
        sm = StateMachine()
        captured = {}

        def spy_guard(current, target, **kwargs):
            captured.update(kwargs)
            return True

        sm.add_guard(DEXState.IDLE, DEXState.ANALYZING, spy_guard)
        sm.on_price_update({"price": 99.9})
        assert captured["event_data"]["price"] == 99.9


# ---------------------------------------------------------------------------
# 7. reset
# ---------------------------------------------------------------------------
class TestReset:
    """reset() returns machine to IDLE and clears history."""

    def test_reset_to_idle(self):
        sm = StateMachine(initial_state=DEXState.CONFIRMED)
        sm.reset()
        assert sm.get_state() is DEXState.IDLE

    def test_reset_clears_history(self):
        sm = StateMachine()
        sm.transition(DEXState.ANALYZING)
        sm.reset()
        assert sm.history == []
