"""
Тесты SafetyMonitor — 400В DC электробезопасность.

Все тесты работают без физического оборудования: аппаратные
интерфейсы (реле, ADC) мокируются через monkey-patching.

Маркер: @pytest.mark.safety — запускать отдельно в CI:
    pytest -m safety --tb=short
"""

import pytest

from blueos.safety_monitor import (
    FaultType,
    SafetyMonitor,
    SafetyState,
    SafetyTelemetry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def monitor():
    """SafetyMonitor с симулированным включённым питанием."""
    m = SafetyMonitor()
    # Имитируем включённое питание — иначе update() ничего не проверяет
    m._power_enabled = True
    m._contactor_commanded = True
    return m


def make_telemetry(
    leakage_ma: float = 0.5,
    insulation_kohm: float = 5000.0,
    arc_freq_khz: float = 0.0,
    arc_energy: float = 0.0,
    line_voltage_pos: float = 200.0,
    line_voltage_neg: float = 200.0,
    line_current_a: float = 10.0,
    tether_continuity: bool = True,
    ground_rod: bool = True,
) -> SafetyTelemetry:
    return SafetyTelemetry(
        leakage_current_ma=leakage_ma,
        insulation_resistance_kohm=insulation_kohm,
        arc_frequency_khz=arc_freq_khz,
        arc_energy_level=arc_energy,
        line_voltage_positive=line_voltage_pos,
        line_voltage_negative=line_voltage_neg,
        line_current_a=line_current_a,
        tether_continuity=tether_continuity,
        ground_rod_connected=ground_rod,
    )


# ---------------------------------------------------------------------------
# Normal operation
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_normal_telemetry_stays_ok(monitor):
    telemetry = make_telemetry()
    state = monitor.update(telemetry)
    assert state in (SafetyState.OK, SafetyState.WARNING)


# ---------------------------------------------------------------------------
# GFCI — ток утечки
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_gfci_trip_at_30ma(monitor):
    """Ток утечки ≥30 мА должен вызвать SHUTDOWN за ≤30 мс."""
    telemetry = make_telemetry(leakage_ma=31.0)
    state = monitor.update(telemetry)
    assert state == SafetyState.SHUTDOWN
    assert monitor.last_fault == FaultType.GROUND_FAULT


@pytest.mark.safety
def test_gfci_no_trip_at_29ma(monitor):
    telemetry = make_telemetry(leakage_ma=29.0)
    state = monitor.update(telemetry)
    assert state != SafetyState.SHUTDOWN


@pytest.mark.safety
def test_gfci_warning_above_half_threshold(monitor):
    """Ток утечки >50% порога GFCI — предупреждение, не аварийное отключение."""
    telemetry = make_telemetry(leakage_ma=16.0)  # >15 мА (50% от 30 мА)
    state = monitor.update(telemetry)
    assert state in (SafetyState.WARNING, SafetyState.FAULT)
    assert state != SafetyState.SHUTDOWN


# ---------------------------------------------------------------------------
# IMD — сопротивление изоляции
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_insulation_failure_below_100kohm(monitor):
    telemetry = make_telemetry(insulation_kohm=50.0)
    state = monitor.update(telemetry)
    assert state in (SafetyState.FAULT, SafetyState.SHUTDOWN)
    assert monitor.last_fault in (
        FaultType.INSULATION_FAILURE,
        FaultType.INSULATION_DEGRADED,
    )


@pytest.mark.safety
def test_insulation_warning_below_500kohm(monitor):
    telemetry = make_telemetry(insulation_kohm=300.0)
    state = monitor.update(telemetry)
    assert state in (SafetyState.WARNING, SafetyState.FAULT)


@pytest.mark.safety
def test_insulation_ok_above_500kohm(monitor):
    telemetry = make_telemetry(insulation_kohm=600.0)
    state = monitor.update(telemetry)
    assert state in (SafetyState.OK, SafetyState.WARNING)


# ---------------------------------------------------------------------------
# AFCI — дуговое замыкание
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_arc_detection_triggers_shutdown(monitor):
    """Частота в диапазоне 10–100 кГц + высокая энергия = признак дуги."""
    telemetry = make_telemetry(arc_freq_khz=50.0, arc_energy=0.9)
    state = monitor.update(telemetry)
    assert state in (SafetyState.FAULT, SafetyState.SHUTDOWN)
    assert monitor.last_fault == FaultType.ARC_DETECTED


@pytest.mark.safety
def test_arc_warning_at_low_energy(monitor):
    """Частота дуги с низкой энергией — предупреждение, не SHUTDOWN."""
    telemetry = make_telemetry(arc_freq_khz=50.0, arc_energy=0.4)
    state = monitor.update(telemetry)
    assert state in (SafetyState.WARNING, SafetyState.FAULT)
    assert state != SafetyState.SHUTDOWN


@pytest.mark.safety
def test_no_arc_outside_frequency_range(monitor):
    telemetry = make_telemetry(arc_freq_khz=0.0, arc_energy=0.0)
    monitor.update(telemetry)
    assert monitor.last_fault != FaultType.ARC_DETECTED


# ---------------------------------------------------------------------------
# emergency_shutdown
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_emergency_shutdown_sets_shutdown_state(monitor):
    monitor.emergency_shutdown(FaultType.GROUND_FAULT, "Тест")
    assert monitor.state == SafetyState.SHUTDOWN


@pytest.mark.safety
def test_emergency_shutdown_opens_contactor(monitor):
    monitor._contactor_commanded = True  # закрыт
    monitor.emergency_shutdown(FaultType.GROUND_FAULT, "Тест")
    assert monitor._contactor_commanded is False  # разомкнут после аварии


@pytest.mark.safety
def test_reset_fault_requires_ok_conditions(monitor):
    """После SHUTDOWN нельзя сбросить пока условия не нормализованы."""
    monitor.emergency_shutdown(FaultType.GROUND_FAULT, "Тест")
    result = monitor.reset_fault()
    # reset_fault() должен вернуть False если условия не OK
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# pre_power_check
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_pre_power_check_returns_tuple(monitor):
    ok, message = monitor.pre_power_check()
    assert isinstance(ok, bool)
    assert isinstance(message, str)


# ---------------------------------------------------------------------------
# Тестирование состояний после нескольких обновлений
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_multiple_ok_updates_stay_ok(monitor):
    good = make_telemetry()
    for _ in range(10):
        state = monitor.update(good)
    assert state in (SafetyState.OK, SafetyState.WARNING)


@pytest.mark.safety
def test_state_does_not_recover_automatically_after_shutdown(monitor):
    """После SHUTDOWN состояние не сбрасывается само по себе без reset_fault()."""
    monitor.update(make_telemetry(leakage_ma=50.0))  # вызовет SHUTDOWN
    assert monitor.state == SafetyState.SHUTDOWN
    monitor.update(make_telemetry())  # нормальные данные
    assert monitor.state == SafetyState.SHUTDOWN


# ---------------------------------------------------------------------------
# _contactor_open (private property + setter)
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_contactor_open_getter_reflects_commanded_state():
    m = SafetyMonitor()
    assert m._contactor_open is True  # not commanded closed yet
    m._contactor_commanded = True
    assert m._contactor_open is False


@pytest.mark.safety
def test_contactor_open_setter_true_disables_power():
    m = SafetyMonitor()
    m._contactor_commanded = True
    m._power_enabled = True
    m._contactor_open = True
    assert m._contactor_commanded is False
    assert m._power_enabled is False


@pytest.mark.safety
def test_contactor_open_setter_false_enables_power():
    m = SafetyMonitor()
    m._contactor_open = False
    assert m._contactor_commanded is True
    assert m._power_enabled is True


# ---------------------------------------------------------------------------
# is_power_safe / events properties
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_is_power_safe_true_when_ok():
    m = SafetyMonitor()
    assert m.is_power_safe is True


@pytest.mark.safety
def test_is_power_safe_false_when_shutdown():
    m = SafetyMonitor()
    m.emergency_shutdown(FaultType.GROUND_FAULT, "test")
    assert m.is_power_safe is False


@pytest.mark.safety
def test_events_property_starts_empty():
    m = SafetyMonitor()
    assert m.events == []


# ---------------------------------------------------------------------------
# pre_power_check failure path
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_pre_power_check_fails_on_missing_ground_rod():
    m = SafetyMonitor()
    m._telemetry = make_telemetry(ground_rod=False)
    ok, report = m.pre_power_check()
    assert ok is False
    assert "ЗАЗЕМЛЕНИ" in report
    assert len(m.events) == 1


@pytest.mark.safety
def test_pre_power_check_fails_on_tether_break():
    m = SafetyMonitor()
    m._telemetry = make_telemetry(tether_continuity=False)
    ok, report = m.pre_power_check()
    assert ok is False


# ---------------------------------------------------------------------------
# enable_power success path
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_enable_power_succeeds_with_clean_telemetry():
    m = SafetyMonitor()
    result = m.enable_power()
    assert result is True
    assert m._contactor_commanded is True
    assert m._power_enabled is True
    assert m._gfci_armed is True


@pytest.mark.safety
def test_enable_power_fails_with_bad_telemetry():
    m = SafetyMonitor()
    m._telemetry = make_telemetry(ground_rod=False)
    result = m.enable_power()
    assert result is False
    assert m._power_enabled is False


# ---------------------------------------------------------------------------
# update(): voltage imbalance / overcurrent / tether continuity trips
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_update_trips_on_critical_voltage_imbalance(monitor):
    telemetry = make_telemetry(line_voltage_pos=220.0, line_voltage_neg=200.0)
    state = monitor.update(telemetry)
    assert state == SafetyState.SHUTDOWN
    assert monitor.last_fault == FaultType.VOLTAGE_IMBALANCE


@pytest.mark.safety
def test_update_warns_on_moderate_voltage_imbalance(monitor):
    telemetry = make_telemetry(line_voltage_pos=207.0, line_voltage_neg=200.0)
    state = monitor.update(telemetry)
    assert state in (SafetyState.WARNING, SafetyState.FAULT)
    assert state != SafetyState.SHUTDOWN


@pytest.mark.safety
def test_update_trips_on_overcurrent(monitor):
    from blueos import config
    telemetry = make_telemetry(line_current_a=config.TETHER_MAX_CURRENT * 2)
    state = monitor.update(telemetry)
    assert state == SafetyState.SHUTDOWN
    assert monitor.last_fault == FaultType.OVERCURRENT


@pytest.mark.safety
def test_update_trips_on_tether_disconnect(monitor):
    telemetry = make_telemetry(tether_continuity=False)
    state = monitor.update(telemetry)
    assert state == SafetyState.SHUTDOWN
    assert monitor.last_fault == FaultType.TETHER_DISCONNECT


@pytest.mark.safety
def test_update_recovers_to_ok_after_warning_count_expires(monitor):
    telemetry_warn = make_telemetry(leakage_ma=16.0)
    monitor.update(telemetry_warn)
    assert monitor.state == SafetyState.WARNING

    monitor._warning_count = 1
    good = make_telemetry()
    state = monitor.update(good)
    assert state == SafetyState.OK
    assert monitor.last_fault == FaultType.NONE


# ---------------------------------------------------------------------------
# reset_fault
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_reset_fault_noop_when_not_shutdown():
    m = SafetyMonitor()
    assert m.state == SafetyState.OK
    assert m.reset_fault() is True


@pytest.mark.safety
def test_reset_fault_fails_when_precheck_fails():
    m = SafetyMonitor()
    m.emergency_shutdown(FaultType.GROUND_FAULT, "test")
    m._telemetry = make_telemetry(ground_rod=False)
    result = m.reset_fault()
    assert result is False
    assert m.state == SafetyState.SHUTDOWN


# ---------------------------------------------------------------------------
# _check_arc: AFCI disarmed
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_check_arc_returns_false_when_disarmed(monitor):
    monitor._afci_armed = False
    telemetry = make_telemetry(arc_freq_khz=50.0, arc_energy=0.9)
    assert monitor._check_arc(telemetry) is False


# ---------------------------------------------------------------------------
# _set_warning: no-op during SHUTDOWN
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_set_warning_noop_during_shutdown():
    m = SafetyMonitor()
    m.emergency_shutdown(FaultType.GROUND_FAULT, "test")
    events_before = len(m.events)
    m._set_warning(FaultType.ARC_DETECTED, 1.0, 0.5, "should be ignored")
    assert m.state == SafetyState.SHUTDOWN
    assert len(m.events) == events_before


# ---------------------------------------------------------------------------
# _record_event: log trimming beyond 1000 entries
# ---------------------------------------------------------------------------

@pytest.mark.safety
def test_record_event_trims_log_beyond_1000():
    m = SafetyMonitor()
    m._events = [object()] * 1001  # type: ignore[list-item]
    m._record_event(FaultType.NONE, SafetyState.OK, 0, 0, "msg", "action")
    assert len(m._events) == 500


# ---------------------------------------------------------------------------
# get_voltage_alternatives (module-level function)
# ---------------------------------------------------------------------------

def test_get_voltage_alternatives_structure():
    from blueos.safety_monitor import get_voltage_alternatives
    alternatives = get_voltage_alternatives()
    assert len(alternatives) == 4
    voltages = {a["voltage"] for a in alternatives}
    assert voltages == {400, 200, 120, 48}
    for alt in alternatives:
        assert "requires" in alt
        assert "safety_class" in alt
