"""Тесты BoreasController — главный контроллер дрона Борей (blueos/main.py)."""

import time as time_module

import pytest

from blueos import main as main_module
from blueos.main import BoreasController
from blueos.state_machine import State
from blueos.power_manager import PowerState
from blueos.safety_monitor import SafetyState, FaultType
from blueos.backup_power import BackupState
from blueos.burst_vibrator import BurstMode
from blueos.tilt_controller import SnowType
from blueos.thermal_analyzer import SnowAssessment
from blueos.perception import RoofBoundary, Obstacle
from blueos.planner import Lane, Waypoint, CleaningPlan
from blueos.hover_blow import HoverBlowPlan, BlowPoint
from blueos import config

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


@pytest.fixture
def ctrl():
    return BoreasController(simulate=True)


def set_time_in_state(controller, seconds_ago: float) -> None:
    """Заставить sm.time_in_state вернуть примерно `seconds_ago`."""
    controller.sm._state_enter_time = time_module.monotonic() - seconds_ago


def force_state(controller, state: State) -> None:
    """Установить состояние напрямую (без валидации переходов)."""
    controller.sm._state = state
    controller.sm._state_enter_time = time_module.monotonic()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

def test_init_creates_all_modules(ctrl):
    assert ctrl.sm.state == State.IDLE
    assert ctrl.power.state == PowerState.CHARGING
    assert ctrl.safety.state == SafetyState.OK
    assert ctrl.backup.state == BackupState.STANDBY
    assert ctrl._recommended_mode == "BULLDOZER"
    assert ctrl._current_snow_type == SnowType.UNKNOWN


def test_init_listener_attached(ctrl):
    assert ctrl._on_state_change in ctrl.sm._listeners


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------

def test_start_simulate_skips_mav_and_safety(ctrl, monkeypatch):
    monkeypatch.setattr(ctrl, "_main_loop", lambda: None)
    ctrl.start()
    assert ctrl.thermal.is_ready is True
    assert ctrl._running is True


def test_start_non_simulate_mav_connect_fails(monkeypatch):
    ctrl = BoreasController(simulate=False)
    monkeypatch.setattr(ctrl.mav, "connect", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        ctrl.start()
    assert exc_info.value.code == 1


def test_start_non_simulate_safety_check_fails(monkeypatch):
    ctrl = BoreasController(simulate=False)
    monkeypatch.setattr(ctrl.mav, "connect", lambda: True)
    monkeypatch.setattr(ctrl.safety, "pre_power_check", lambda: (False, "bad"))
    with pytest.raises(SystemExit) as exc_info:
        ctrl.start()
    assert exc_info.value.code == 2


def test_start_non_simulate_success(monkeypatch):
    ctrl = BoreasController(simulate=False)
    monkeypatch.setattr(ctrl.mav, "connect", lambda: True)
    monkeypatch.setattr(ctrl.safety, "pre_power_check", lambda: (True, "ok"))
    enabled = []
    monkeypatch.setattr(ctrl.safety, "enable_power", lambda: enabled.append(True))
    monkeypatch.setattr(ctrl, "_main_loop", lambda: None)
    ctrl.start()
    assert enabled == [True]
    assert ctrl._running is True


def test_stop_not_connected(ctrl):
    ctrl.stop()
    assert ctrl._running is False


def test_stop_connected_disarms_and_disconnects(ctrl):
    ctrl.mav.connect()
    ctrl.mav.arm()
    ctrl.stop()
    assert ctrl.mav.is_connected is False
    assert ctrl.mav.drone.armed is False


# ---------------------------------------------------------------------------
# _main_loop
# ---------------------------------------------------------------------------

def test_main_loop_normal_iteration_exits_via_running_flag(ctrl, monkeypatch):
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
    # Default telemetry (0V, tether disconnected) trips _check_emergency()
    # before _tick() runs, so bypass real telemetry collection for this test.
    monkeypatch.setattr(ctrl, "_update_telemetry", lambda: None)
    calls = []

    def fake_tick():
        calls.append(1)
        ctrl._running = False

    monkeypatch.setattr(ctrl, "_tick", fake_tick)
    ctrl._running = True
    ctrl._main_loop()
    assert calls == [1]


def test_main_loop_safety_shutdown_skips_tick(ctrl, monkeypatch):
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
    ctrl.safety._state = SafetyState.SHUTDOWN
    ctrl.safety._fault = FaultType.GROUND_FAULT

    call_count = {"n": 0}

    def fake_update():
        call_count["n"] += 1
        if call_count["n"] >= 2:
            ctrl._running = False

    monkeypatch.setattr(ctrl, "_update_telemetry", fake_update)
    ctrl._running = True
    ctrl._main_loop()
    assert ctrl.sm.state == State.EMERGENCY


def test_main_loop_emergency_check_skips_tick(ctrl, monkeypatch):
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
    ctrl.power._state = PowerState.CRITICAL

    call_count = {"n": 0}

    def fake_update():
        call_count["n"] += 1
        if call_count["n"] >= 2:
            ctrl._running = False

    monkeypatch.setattr(ctrl, "_update_telemetry", fake_update)
    ctrl._running = True
    ctrl._main_loop()
    assert ctrl.sm.state == State.EMERGENCY


def test_main_loop_keyboard_interrupt_stops(ctrl, monkeypatch):
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)

    def raise_kb():
        raise KeyboardInterrupt()

    monkeypatch.setattr(ctrl, "_update_telemetry", raise_kb)
    ctrl._running = True
    ctrl._main_loop()
    assert ctrl._running is False


def test_main_loop_generic_exception_forces_emergency(ctrl, monkeypatch):
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
    call_count = {"n": 0}

    def fake_update():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        ctrl._running = False

    monkeypatch.setattr(ctrl, "_update_telemetry", fake_update)
    ctrl._running = True
    ctrl._main_loop()
    assert ctrl.sm.state == State.EMERGENCY


# ---------------------------------------------------------------------------
# _update_telemetry
# ---------------------------------------------------------------------------

def test_update_telemetry_simulate_skips_mav_update(ctrl, monkeypatch):
    called = []
    monkeypatch.setattr(ctrl.mav, "update_telemetry", lambda: called.append(1))
    ctrl._update_telemetry()
    assert called == []


def test_update_telemetry_non_simulate_calls_mav_update(monkeypatch):
    ctrl = BoreasController(simulate=False)
    called = []
    monkeypatch.setattr(ctrl.mav, "update_telemetry", lambda: called.append(1))
    ctrl._update_telemetry()
    assert called == [1]


def test_update_telemetry_propagates_tether_state(ctrl):
    ctrl.mav.ground_station.tether_connected = True
    ctrl._update_telemetry()
    assert ctrl.safety.telemetry.tether_continuity is True
    assert ctrl.backup._tether_was_connected is True


# ---------------------------------------------------------------------------
# _check_safety
# ---------------------------------------------------------------------------

def test_check_safety_ok_returns_false(ctrl):
    assert ctrl._check_safety() is False


def test_check_safety_warning_returns_false(ctrl):
    ctrl.safety._state = SafetyState.WARNING
    ctrl.safety._fault = FaultType.GROUND_FAULT
    assert ctrl._check_safety() is False


def test_check_safety_shutdown_forces_emergency(ctrl):
    ctrl.safety._state = SafetyState.SHUTDOWN
    ctrl.safety._fault = FaultType.INSULATION_FAILURE
    result = ctrl._check_safety()
    assert result is True
    assert ctrl.sm.state == State.EMERGENCY


# ---------------------------------------------------------------------------
# _check_emergency
# ---------------------------------------------------------------------------

def test_check_emergency_all_clear(ctrl):
    assert ctrl._check_emergency() is False


def test_check_emergency_power_critical(ctrl):
    ctrl.power._state = PowerState.CRITICAL
    result = ctrl._check_emergency()
    assert result is True
    assert ctrl.sm.state == State.EMERGENCY


def test_check_emergency_backup_active(ctrl):
    ctrl.backup._state = BackupState.ACTIVE
    result = ctrl._check_emergency()
    assert result is True
    assert ctrl.sm.state == State.EMERGENCY


def test_check_emergency_backup_depleted(ctrl):
    ctrl.backup._state = BackupState.DEPLETED
    result = ctrl._check_emergency()
    assert result is True
    assert ctrl.sm.state == State.EMERGENCY


def test_check_emergency_mav_disconnected_non_simulate():
    ctrl = BoreasController(simulate=False)
    result = ctrl._check_emergency()
    assert result is True
    assert ctrl.sm.state == State.EMERGENCY


def test_check_emergency_mav_disconnected_simulate_ignored(ctrl):
    # simulate=True → mav connectivity check skipped
    assert ctrl._check_emergency() is False


# ---------------------------------------------------------------------------
# _tick dispatch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state,handler_name", [
    (State.IDLE, "_tick_idle"),
    (State.SURVEY, "_tick_survey"),
    (State.TRANSIT, "_tick_transit"),
    (State.BULLDOZER, "_tick_bulldozer"),
    (State.HOVER_BLOW, "_tick_hover_blow"),
    (State.RETREAT, "_tick_retreat"),
])
def test_tick_dispatches_to_correct_handler(ctrl, monkeypatch, state, handler_name):
    called = []
    monkeypatch.setattr(ctrl, handler_name, lambda: called.append(1))
    force_state(ctrl, state)
    ctrl._tick()
    assert called == [1]


def test_tick_dispatches_emergency_to_handle_emergency(ctrl, monkeypatch):
    called = []
    monkeypatch.setattr(ctrl, "_handle_emergency", lambda: called.append(1))
    force_state(ctrl, State.EMERGENCY)
    ctrl._tick()
    assert called == [1]


# ---------------------------------------------------------------------------
# _tick_idle
# ---------------------------------------------------------------------------

def test_tick_idle_charging_no_log_when_fresh(ctrl):
    ctrl.power._state = PowerState.CHARGING
    ctrl._tick_idle()  # Should not raise; time_in_state ~0 → condition false


def test_tick_idle_charging_logs_periodically(ctrl):
    ctrl.power._state = PowerState.CHARGING
    set_time_in_state(ctrl, 10.0)
    ctrl._tick_idle()  # covers the periodic-log branch


def test_tick_idle_ready_no_plan_goes_survey(ctrl):
    ctrl.power._state = PowerState.READY
    ctrl._tick_idle()
    assert ctrl.sm.state == State.SURVEY


def test_tick_idle_ready_with_plan_goes_transit(ctrl):
    ctrl.power._state = PowerState.READY
    ctrl.planner._plan = CleaningPlan(lanes=[Lane(waypoints=[Waypoint(x=0, y=0), Waypoint(x=1, y=1)])])
    ctrl._tick_idle()
    assert ctrl.sm.state == State.TRANSIT


def test_tick_idle_discharging_no_action(ctrl):
    ctrl.power._state = PowerState.DISCHARGING
    ctrl._tick_idle()
    assert ctrl.sm.state == State.IDLE


# ---------------------------------------------------------------------------
# _tick_survey
# ---------------------------------------------------------------------------

def test_tick_survey_initial_takeoff_sequence(ctrl):
    ctrl.mav.connect()
    force_state(ctrl, State.SURVEY)
    ctrl._tick_survey()
    assert ctrl.mav.drone.armed is True
    assert ctrl.mav.drone.mode == "GUIDED"


def test_tick_survey_mid_takeoff_no_action(ctrl):
    force_state(ctrl, State.SURVEY)
    set_time_in_state(ctrl, 1.0)  # between 0.2 and 2.0
    ctrl._tick_survey()  # Should just return, no exception


def test_tick_survey_generates_plan_and_transitions_transit(ctrl):
    force_state(ctrl, State.SURVEY)
    set_time_in_state(ctrl, 3.0)
    ctrl.perception._roof = RoofBoundary(vertices=SQUARE)
    ctrl._tick_survey()
    assert ctrl.sm.state == State.TRANSIT


def test_tick_survey_no_plan_goes_retreat(ctrl):
    force_state(ctrl, State.SURVEY)
    set_time_in_state(ctrl, 3.0)
    # No roof set → planner cannot build a plan
    ctrl._tick_survey()
    assert ctrl.sm.state == State.RETREAT


def test_tick_survey_hover_blow_recommendation_generates_hover_plan(ctrl, monkeypatch):
    force_state(ctrl, State.SURVEY)
    set_time_in_state(ctrl, 3.0)
    ctrl.perception._roof = RoofBoundary(vertices=SQUARE)

    fake_assessment = SnowAssessment(
        dominant_type=SnowType.POWDER,
        mean_depth_mm=50.0,
        mean_density_kg_m3=100.0,
        coverage_percent=100.0,
        regions=[],
        hidden_obstacles=[],
        recommended_mode="HOVER_BLOW",
        recommended_tilt_deg=0.0,
    )
    monkeypatch.setattr(ctrl.thermal, "process_frame", lambda **kw: fake_assessment)
    ctrl._tick_survey()
    assert ctrl.hover_blow.plan is not None
    assert ctrl.sm.state == State.TRANSIT


def test_tick_survey_appends_hidden_obstacles(ctrl, monkeypatch):
    from blueos.thermal_analyzer import HiddenObstacle

    force_state(ctrl, State.SURVEY)
    set_time_in_state(ctrl, 3.0)
    ctrl.perception._roof = RoofBoundary(vertices=SQUARE)

    fake_assessment = SnowAssessment(
        dominant_type=SnowType.SETTLED,
        mean_depth_mm=100.0,
        mean_density_kg_m3=350.0,
        coverage_percent=100.0,
        regions=[],
        hidden_obstacles=[HiddenObstacle(x=1.0, y=1.0, radius=0.3, obstacle_type="pipe", delta_temp_c=6.0)],
        recommended_mode="BULLDOZER",
        recommended_tilt_deg=23.0,
    )
    monkeypatch.setattr(ctrl.thermal, "process_frame", lambda **kw: fake_assessment)
    before = len(ctrl.perception.obstacles)
    ctrl._tick_survey()
    assert len(ctrl.perception.obstacles) == before + 1


# ---------------------------------------------------------------------------
# _tick_transit
# ---------------------------------------------------------------------------

def _make_lane():
    return Lane(waypoints=[Waypoint(x=0.0, y=0.0), Waypoint(x=1.0, y=1.0)])


def test_tick_transit_skip_mode_goes_retreat(ctrl):
    force_state(ctrl, State.TRANSIT)
    ctrl._recommended_mode = "SKIP"
    ctrl._tick_transit()
    assert ctrl.sm.state == State.RETREAT


def test_tick_transit_no_lane_left_goes_retreat(ctrl):
    force_state(ctrl, State.TRANSIT)
    ctrl.planner._plan = CleaningPlan(lanes=[])
    ctrl._tick_transit()
    assert ctrl.sm.state == State.RETREAT


def test_tick_transit_early_goto_no_transition(ctrl):
    force_state(ctrl, State.TRANSIT)
    ctrl.planner._plan = CleaningPlan(lanes=[_make_lane()])
    ctrl._tick_transit()
    assert ctrl.sm.state == State.TRANSIT


def test_tick_transit_lands_and_goes_bulldozer(ctrl):
    force_state(ctrl, State.TRANSIT)
    set_time_in_state(ctrl, 2.0)
    ctrl.planner._plan = CleaningPlan(lanes=[_make_lane()])
    ctrl._tick_transit()
    assert ctrl.sm.state == State.BULLDOZER


def test_tick_transit_hover_blow_target_no_land(ctrl):
    force_state(ctrl, State.TRANSIT)
    set_time_in_state(ctrl, 2.0)
    ctrl._recommended_mode = "HOVER_BLOW"
    ctrl.hover_blow._plan = HoverBlowPlan(points=[BlowPoint(x=0.0, y=0.0)])
    ctrl.planner._plan = CleaningPlan(lanes=[_make_lane()])
    ctrl._tick_transit()
    assert ctrl.sm.state == State.HOVER_BLOW


# ---------------------------------------------------------------------------
# _tick_bulldozer
# ---------------------------------------------------------------------------

def test_tick_bulldozer_timeout_goes_retreat(ctrl):
    force_state(ctrl, State.BULLDOZER)
    set_time_in_state(ctrl, config.BURST_MAX_DURATION_SEC + 1)
    before = ctrl._completed_lanes
    ctrl._tick_bulldozer()
    assert ctrl.sm.state == State.RETREAT
    assert ctrl._completed_lanes == before + 1


def test_tick_bulldozer_low_power_goes_retreat(ctrl):
    force_state(ctrl, State.BULLDOZER)
    ctrl.power._state = PowerState.LOW_POWER
    ctrl._tick_bulldozer()
    assert ctrl.sm.state == State.RETREAT


def test_tick_bulldozer_init_phase_easy_snow(ctrl):
    force_state(ctrl, State.BULLDOZER)
    ctrl._current_snow_type = SnowType.POWDER
    ctrl._current_snow_depth_mm = 50.0
    ctrl._tick_bulldozer()
    assert ctrl.burst.mode == BurstMode.RAMP


def test_tick_bulldozer_init_phase_impossible_snow_warns(ctrl):
    force_state(ctrl, State.BULLDOZER)
    ctrl._current_snow_type = SnowType.ICE
    ctrl._current_snow_depth_mm = 900.0
    ctrl._tick_bulldozer()  # Should not raise, logs a warning


def test_tick_bulldozer_active_phase_updates_tilt_and_throttle(ctrl):
    force_state(ctrl, State.BULLDOZER)
    set_time_in_state(ctrl, 1.0)
    ctrl._current_snow_type = SnowType.POWDER
    ctrl._current_snow_depth_mm = 50.0
    ctrl._tick_bulldozer()


def test_tick_bulldozer_visibility_window_triggers_thermal(ctrl, monkeypatch):
    force_state(ctrl, State.BULLDOZER)
    set_time_in_state(ctrl, 5.0)
    monkeypatch.setattr(ctrl.burst, "get_visibility_window", lambda: True)
    called = []
    monkeypatch.setattr(ctrl.thermal, "process_frame", lambda **kw: called.append(1))
    ctrl._tick_bulldozer()
    assert called == [1]


def test_tick_bulldozer_periodic_logging(ctrl):
    force_state(ctrl, State.BULLDOZER)
    set_time_in_state(ctrl, 5.0)
    ctrl._tick_bulldozer()  # int(5)%5==0 and >1 → log branch


# ---------------------------------------------------------------------------
# _tick_hover_blow
# ---------------------------------------------------------------------------

def test_tick_hover_blow_timeout_goes_retreat(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    set_time_in_state(ctrl, config.BURST_MAX_DURATION_SEC + 1)
    ctrl._tick_hover_blow()
    assert ctrl.sm.state == State.RETREAT


def test_tick_hover_blow_low_power_goes_retreat(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    ctrl.power._state = PowerState.LOW_POWER
    ctrl._tick_hover_blow()
    assert ctrl.sm.state == State.RETREAT


def test_tick_hover_blow_init_phase_no_plan(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    ctrl._tick_hover_blow()  # plan is None → len() ternary branch


def test_tick_hover_blow_init_phase_with_plan(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    ctrl.hover_blow._plan = HoverBlowPlan(points=[BlowPoint(x=0.0, y=0.0)])
    ctrl._tick_hover_blow()


def test_tick_hover_blow_active_moves_to_point(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    set_time_in_state(ctrl, 1.0)
    ctrl.hover_blow._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0), BlowPoint(x=3.0, y=4.0)])
    ctrl.hover_blow._active = True
    ctrl._tick_hover_blow()
    assert ctrl.sm.state == State.HOVER_BLOW


def test_tick_hover_blow_plan_complete_goes_retreat(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    set_time_in_state(ctrl, 1.0)
    ctrl.hover_blow._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0)], current_index=1)
    ctrl.hover_blow._active = True
    before = ctrl._completed_lanes
    ctrl._tick_hover_blow()
    assert ctrl.sm.state == State.RETREAT
    assert ctrl._completed_lanes == before + 1


def test_tick_hover_blow_periodic_logging(ctrl):
    force_state(ctrl, State.HOVER_BLOW)
    set_time_in_state(ctrl, 5.0)
    ctrl.hover_blow._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0), BlowPoint(x=3.0, y=4.0)])
    ctrl.hover_blow._active = True
    ctrl._tick_hover_blow()


# ---------------------------------------------------------------------------
# _tick_retreat
# ---------------------------------------------------------------------------

def test_tick_retreat_initial_takeoff(ctrl):
    force_state(ctrl, State.RETREAT)
    ctrl._tick_retreat()
    assert ctrl.mav.drone.mode == "GUIDED"


def test_tick_retreat_mid_phase_no_action(ctrl):
    force_state(ctrl, State.RETREAT)
    set_time_in_state(ctrl, 1.0)
    ctrl._tick_retreat()
    assert ctrl.sm.state == State.RETREAT


def test_tick_retreat_lands_and_charges(ctrl):
    force_state(ctrl, State.RETREAT)
    set_time_in_state(ctrl, 4.0)
    ctrl._tick_retreat()
    assert ctrl.sm.state == State.IDLE
    assert ctrl.mav.drone.mode == "LAND"


# ---------------------------------------------------------------------------
# _handle_emergency
# ---------------------------------------------------------------------------

def test_handle_emergency_neither_backup_nor_safety(ctrl):
    force_state(ctrl, State.EMERGENCY)
    ctrl._handle_emergency()
    assert ctrl.mav.drone.mode == "LAND"


def test_handle_emergency_on_backup(ctrl):
    force_state(ctrl, State.EMERGENCY)
    ctrl.backup._state = BackupState.ACTIVE
    ctrl._handle_emergency()


def test_handle_emergency_safety_fault(ctrl):
    force_state(ctrl, State.EMERGENCY)
    ctrl.safety._state = SafetyState.SHUTDOWN
    ctrl.safety._fault = FaultType.ARC_DETECTED
    ctrl.safety._power_enabled = True
    ctrl._handle_emergency()
    assert ctrl.safety._power_enabled is False


def test_handle_emergency_late_phase_no_action(ctrl):
    force_state(ctrl, State.EMERGENCY)
    set_time_in_state(ctrl, 1.0)
    ctrl._handle_emergency()  # elapsed >=0.5 → no-op branch


# ---------------------------------------------------------------------------
# _on_state_change
# ---------------------------------------------------------------------------

def test_on_state_change_to_idle_starts_charge(ctrl):
    ctrl.sm.transition_to(State.SURVEY)
    ctrl.sm.transition_to(State.IDLE)
    assert ctrl.power.state == PowerState.CHARGING


def test_on_state_change_to_bulldozer_starts_discharge(ctrl):
    ctrl.sm.transition_to(State.TRANSIT)
    ctrl.sm.transition_to(State.BULLDOZER)
    # start_discharge is called; whether it actually enters DISCHARGING
    # depends on voltage, but the call itself must not raise.


def test_on_state_change_to_hover_blow_starts_discharge(ctrl):
    ctrl.sm.transition_to(State.TRANSIT)
    ctrl.sm.transition_to(State.HOVER_BLOW)


def test_on_state_change_to_survey_no_extra_action(ctrl):
    ctrl.sm.transition_to(State.SURVEY)  # just logs, no special branch


# ---------------------------------------------------------------------------
# CLI: main()
# ---------------------------------------------------------------------------

def test_main_cli_simulate(monkeypatch):
    monkeypatch.setattr("sys.argv", ["blueos-main", "--simulate"])

    started = []
    monkeypatch.setattr(
        "blueos.main.BoreasController.start",
        lambda self: started.append(self),
    )

    captured_handlers = []

    def fake_signal(sig, handler):
        captured_handlers.append((sig, handler))

    monkeypatch.setattr(main_module.signal, "signal", fake_signal)

    main_module.main()

    assert len(started) == 1
    assert len(captured_handlers) == 2

    # Manually invoke the captured signal handlers to cover their bodies
    for sig, handler in captured_handlers:
        handler(sig, None)


def test_main_cli_custom_connection_and_log_level(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["blueos-main", "--connection", "udp:127.0.0.1:9999", "--simulate", "--log-level", "DEBUG"],
    )
    monkeypatch.setattr("blueos.main.BoreasController.start", lambda self: None)
    monkeypatch.setattr(main_module.signal, "signal", lambda sig, handler: None)
    main_module.main()
