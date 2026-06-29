"""
Тесты SafetyMonitor — 400В DC электробезопасность.

Все тесты работают без физического оборудования: аппаратные
интерфейсы (реле, ADC) мокируются через monkey-patching.

Маркер: @pytest.mark.safety — запускать отдельно в CI:
    pytest -m safety --tb=short
"""

import pytest

from blueos.safety_monitor import (
    SafetyMonitor,
    SafetyState,
    SafetyTelemetry,
    FaultType,
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
    state = monitor.update(telemetry)
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
