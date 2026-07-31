"""Тесты PowerManager — управление циклами заряд/разряд суперконденсаторов."""

import time

import pytest

from blueos import config
from blueos.power_manager import PowerManager, PowerState


@pytest.fixture
def pm():
    return PowerManager()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_state_is_charging(pm):
    assert pm.state == PowerState.CHARGING


def test_initial_voltage_zero(pm):
    assert pm.voltage == 0.0


def test_initial_energy_zero(pm):
    assert pm.energy_kj == 0.0


# ---------------------------------------------------------------------------
# energy_percent
# ---------------------------------------------------------------------------

def test_energy_percent_zero_voltage(pm):
    pm.update_telemetry(voltage=0.0, current=0.0, temperature=20.0)
    assert pm.energy_percent == pytest.approx(0.0)


def test_energy_percent_full_voltage(pm):
    pm.update_telemetry(voltage=config.SUPERCAP_NOMINAL_VOLTAGE, current=0.0, temperature=20.0)
    assert 0.0 < pm.energy_percent <= 100.0


def test_energy_percent_capped_at_100(pm):
    pm.update_telemetry(voltage=200.0, current=0.0, temperature=20.0)
    assert pm.energy_percent == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# estimated_burst_time_sec
# ---------------------------------------------------------------------------

def test_estimated_burst_time_zero_energy(pm):
    assert pm.estimated_burst_time_sec == pytest.approx(0.0)


def test_estimated_burst_time_positive(pm):
    pm.update_telemetry(voltage=48.0, current=0.0, temperature=20.0)
    assert pm.estimated_burst_time_sec > 0.0


def test_energy_percent_zero_when_max_energy_config_zero(pm, monkeypatch):
    monkeypatch.setattr(config, "SUPERCAP_MAX_ENERGY_KJ", 0.0)
    pm.update_telemetry(voltage=48.0, current=0.0, temperature=20.0)
    assert pm.energy_percent == 0.0


def test_estimated_burst_time_zero_when_max_thrust_config_zero(pm, monkeypatch):
    monkeypatch.setattr(config, "MAX_THRUST_KW", 0.0)
    pm.update_telemetry(voltage=48.0, current=0.0, temperature=20.0)
    assert pm.estimated_burst_time_sec == 0.0


# ---------------------------------------------------------------------------
# is_burst_ready
# ---------------------------------------------------------------------------

def test_is_burst_ready_false_initially(pm):
    assert pm.is_burst_ready is False


def test_is_burst_ready_true_above_threshold(pm):
    pm.update_telemetry(voltage=config.VOLTAGE_BURST_READY + 1, current=0.0, temperature=20.0)
    assert pm.is_burst_ready is True


# ---------------------------------------------------------------------------
# update_telemetry state transitions
# ---------------------------------------------------------------------------

def test_state_critical_at_emergency_voltage(pm):
    state = pm.update_telemetry(voltage=config.VOLTAGE_EMERGENCY - 1, current=0.0, temperature=20.0)
    assert state == PowerState.CRITICAL


def test_state_low_power(pm):
    state = pm.update_telemetry(
        voltage=(config.VOLTAGE_EMERGENCY + config.VOLTAGE_LOW_POWER) / 2,
        current=0.0, temperature=20.0,
    )
    assert state == PowerState.LOW_POWER


def test_state_ready_above_burst_threshold(pm):
    state = pm.update_telemetry(voltage=config.VOLTAGE_BURST_READY + 5, current=0.0, temperature=20.0)
    assert state == PowerState.READY


def test_state_charging_between_low_and_ready(pm):
    mid_voltage = (config.VOLTAGE_LOW_POWER + config.VOLTAGE_BURST_READY) / 2
    state = pm.update_telemetry(voltage=mid_voltage, current=0.0, temperature=20.0)
    assert state == PowerState.CHARGING


def test_state_stays_discharging_below_ready_threshold(pm):
    pm.update_telemetry(voltage=config.VOLTAGE_BURST_READY + 5, current=0.0, temperature=20.0)
    pm.start_discharge()
    assert pm.state == PowerState.DISCHARGING

    mid_voltage = (config.VOLTAGE_LOW_POWER + config.VOLTAGE_BURST_READY) / 2
    state = pm.update_telemetry(voltage=mid_voltage, current=5.0, temperature=20.0)
    assert state == PowerState.DISCHARGING


# ---------------------------------------------------------------------------
# start_charge / start_discharge
# ---------------------------------------------------------------------------

def test_start_charge_sets_state(pm):
    pm.start_charge()
    assert pm.state == PowerState.CHARGING
    assert pm._charge_start_time > 0.0


def test_start_discharge_when_not_ready_does_nothing(pm):
    pm.start_discharge()
    assert pm.state == PowerState.CHARGING
    assert pm._discharge_start_time == 0.0


def test_start_discharge_when_ready(pm):
    pm.update_telemetry(voltage=config.VOLTAGE_BURST_READY + 5, current=0.0, temperature=20.0)
    pm.start_discharge()
    assert pm.state == PowerState.DISCHARGING
    assert pm._discharge_start_time > 0.0


# ---------------------------------------------------------------------------
# elapsed timers
# ---------------------------------------------------------------------------

def test_discharge_elapsed_zero_before_start(pm):
    assert pm.discharge_elapsed_sec == 0.0


def test_charge_elapsed_zero_before_start(pm):
    assert pm.charge_elapsed_sec == 0.0


def test_discharge_elapsed_increases(pm):
    pm.update_telemetry(voltage=config.VOLTAGE_BURST_READY + 5, current=0.0, temperature=20.0)
    pm.start_discharge()
    time.sleep(0.02)
    assert pm.discharge_elapsed_sec > 0.0


def test_charge_elapsed_increases(pm):
    pm.start_charge()
    time.sleep(0.02)
    assert pm.charge_elapsed_sec > 0.0
