"""
Тесты конечного автомата состояний (StateMachine).

Проверяются:
- Все 7 состояний достижимы и корректно работают
- Допустимые переходы разрешены, недопустимые отклонены
- force_emergency() работает из любого состояния
- История переходов записывается корректно
- Listeners вызываются при каждом переходе
"""

import pytest

from blueos.state_machine import State, StateMachine


def make_sm() -> StateMachine:
    return StateMachine()


# ---------------------------------------------------------------------------
# Начальное состояние
# ---------------------------------------------------------------------------

def test_initial_state_is_idle():
    sm = make_sm()
    assert sm.state == State.IDLE


def test_initial_history_empty():
    sm = make_sm()
    assert sm.history == []


# ---------------------------------------------------------------------------
# Допустимые переходы
# ---------------------------------------------------------------------------

def test_idle_to_survey():
    sm = make_sm()
    assert sm.transition_to(State.SURVEY) is True
    assert sm.state == State.SURVEY


def test_idle_to_transit():
    sm = make_sm()
    assert sm.transition_to(State.TRANSIT) is True
    assert sm.state == State.TRANSIT


def test_survey_to_transit():
    sm = make_sm()
    sm.transition_to(State.SURVEY)
    assert sm.transition_to(State.TRANSIT) is True
    assert sm.state == State.TRANSIT


def test_survey_to_retreat():
    # Planner failure in SURVEY must be able to route the drone home.
    sm = make_sm()
    sm.transition_to(State.SURVEY)
    assert sm.transition_to(State.RETREAT) is True
    assert sm.state == State.RETREAT


def test_transit_to_bulldozer():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    assert sm.transition_to(State.BULLDOZER) is True
    assert sm.state == State.BULLDOZER


def test_transit_to_hover_blow():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    assert sm.transition_to(State.HOVER_BLOW) is True
    assert sm.state == State.HOVER_BLOW


def test_bulldozer_to_retreat():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    sm.transition_to(State.BULLDOZER)
    assert sm.transition_to(State.RETREAT) is True
    assert sm.state == State.RETREAT


def test_retreat_to_idle():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    sm.transition_to(State.BULLDOZER)
    sm.transition_to(State.RETREAT)
    assert sm.transition_to(State.IDLE) is True
    assert sm.state == State.IDLE


def test_hover_blow_to_transit():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    sm.transition_to(State.HOVER_BLOW)
    assert sm.transition_to(State.TRANSIT) is True
    assert sm.state == State.TRANSIT


# ---------------------------------------------------------------------------
# Недопустимые переходы
# ---------------------------------------------------------------------------

def test_idle_to_bulldozer_rejected():
    sm = make_sm()
    result = sm.transition_to(State.BULLDOZER)
    assert result is False
    assert sm.state == State.IDLE


def test_survey_to_bulldozer_rejected():
    sm = make_sm()
    sm.transition_to(State.SURVEY)
    result = sm.transition_to(State.BULLDOZER)
    assert result is False
    assert sm.state == State.SURVEY


def test_bulldozer_to_idle_rejected():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    sm.transition_to(State.BULLDOZER)
    result = sm.transition_to(State.IDLE)
    assert result is False
    assert sm.state == State.BULLDOZER


def test_retreat_to_bulldozer_rejected():
    sm = make_sm()
    sm.transition_to(State.TRANSIT)
    sm.transition_to(State.BULLDOZER)
    sm.transition_to(State.RETREAT)
    result = sm.transition_to(State.BULLDOZER)
    assert result is False
    assert sm.state == State.RETREAT


def test_same_state_transition_returns_true():
    sm = make_sm()
    assert sm.transition_to(State.IDLE) is True
    assert sm.state == State.IDLE


# ---------------------------------------------------------------------------
# EMERGENCY из любого состояния
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start_state, path", [
    (State.IDLE, []),
    (State.SURVEY, [State.SURVEY]),
    (State.TRANSIT, [State.TRANSIT]),
    (State.BULLDOZER, [State.TRANSIT, State.BULLDOZER]),
    (State.HOVER_BLOW, [State.TRANSIT, State.HOVER_BLOW]),
    (State.RETREAT, [State.TRANSIT, State.BULLDOZER, State.RETREAT]),
])
def test_force_emergency_from_any_state(start_state, path):
    sm = make_sm()
    for s in path:
        sm.transition_to(s)
    sm.force_emergency()
    assert sm.state == State.EMERGENCY


def test_emergency_to_idle_allowed():
    sm = make_sm()
    sm.force_emergency()
    assert sm.transition_to(State.IDLE) is True
    assert sm.state == State.IDLE


def test_emergency_to_survey_rejected():
    sm = make_sm()
    sm.force_emergency()
    result = sm.transition_to(State.SURVEY)
    assert result is False
    assert sm.state == State.EMERGENCY


# ---------------------------------------------------------------------------
# История переходов
# ---------------------------------------------------------------------------

def test_history_records_transitions():
    sm = make_sm()
    sm.transition_to(State.SURVEY)
    sm.transition_to(State.TRANSIT)
    assert len(sm.history) == 2
    _, from_s, to_s = sm.history[0]
    assert from_s == State.IDLE
    assert to_s == State.SURVEY


def test_force_emergency_recorded_in_history():
    sm = make_sm()
    sm.force_emergency()
    assert len(sm.history) == 1
    _, from_s, to_s = sm.history[0]
    assert from_s == State.IDLE
    assert to_s == State.EMERGENCY


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------

def test_listener_called_on_transition():
    sm = make_sm()
    calls = []
    sm.add_listener(lambda old, new: calls.append((old, new)))
    sm.transition_to(State.SURVEY)
    assert len(calls) == 1
    assert calls[0] == (State.IDLE, State.SURVEY)


def test_listener_not_called_on_same_state():
    sm = make_sm()
    calls = []
    sm.add_listener(lambda old, new: calls.append((old, new)))
    sm.transition_to(State.IDLE)
    assert calls == []


def test_listener_called_on_force_emergency():
    sm = make_sm()
    calls = []
    sm.add_listener(lambda old, new: calls.append((old, new)))
    sm.force_emergency()
    assert len(calls) == 1
    assert calls[0][1] == State.EMERGENCY


def test_listener_exception_does_not_crash_sm():
    sm = make_sm()

    def bad_listener(old, new):
        raise RuntimeError("listener error")

    sm.add_listener(bad_listener)
    sm.transition_to(State.SURVEY)
    assert sm.state == State.SURVEY


# ---------------------------------------------------------------------------
# time_in_state
# ---------------------------------------------------------------------------

def test_time_in_state_is_positive():
    sm = make_sm()
    assert sm.time_in_state >= 0.0


def test_time_in_state_resets_on_transition():
    import time
    sm = make_sm()
    time.sleep(0.02)
    t_before = sm.time_in_state
    sm.transition_to(State.SURVEY)
    assert sm.time_in_state < t_before
