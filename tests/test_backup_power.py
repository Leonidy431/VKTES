"""
Тесты BackupPowerManager — резервное питание.

Проверяется:
- Корректность расчёта ёмкости BCAP3000×6 (500 Ф, не 3000 Ф)
- Автоактивация при обрыве кабеля
- Правильное время аварийного полёта для каждого типа
- is_emergency_time_critical при <10с
"""

import pytest

from blueos import config
from blueos.backup_power import BackupPowerManager, BackupState, BackupType

# ---------------------------------------------------------------------------
# SUPERCAP capacity fix
# ---------------------------------------------------------------------------

def test_supercap_series_capacitance_is_500F():
    mgr = BackupPowerManager("SUPERCAP")
    # C_series = 3000 / 6 = 500 Ф
    # E = 0.5 × 500 × 16.2² = 65 610 Дж = 18.225 Вт·ч
    assert mgr._capacity_wh == pytest.approx(18.225, abs=0.5)


def test_supercap_voltage_is_162V():
    mgr = BackupPowerManager("SUPERCAP")
    assert mgr._nominal_voltage == pytest.approx(16.2, abs=0.01)


def test_supercap_max_flight_sec_is_about_13s():
    mgr = BackupPowerManager("SUPERCAP")
    # 65 610 Дж / 5000 Вт ≈ 13.1 с
    assert mgr._max_flight_sec == pytest.approx(13.1, abs=1.0)


# ---------------------------------------------------------------------------
# LIPO capacity
# ---------------------------------------------------------------------------

def test_lipo_capacity_wh():
    mgr = BackupPowerManager("LIPO")
    # 6S × 3.7В × 5000мАч / 1000 = 22.2В × 5А = 111 Вт·ч
    assert mgr._capacity_wh == pytest.approx(111.0, abs=2.0)


def test_lipo_max_flight_sec_capped_at_config():
    mgr = BackupPowerManager("LIPO")
    assert mgr._max_flight_sec <= config.BACKUP_EMERGENCY_FLIGHT_SEC


# ---------------------------------------------------------------------------
# Tether disconnect triggers backup activation
# ---------------------------------------------------------------------------

def test_tether_disconnect_activates_backup():
    mgr = BackupPowerManager("HYBRID")
    # Первый тик — кабель подключён
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=True)
    assert mgr.state == BackupState.STANDBY
    # Второй тик — кабель оборван
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=False)
    assert mgr.state == BackupState.ACTIVE


def test_continuous_tether_disconnect_does_not_retrigger():
    mgr = BackupPowerManager("HYBRID")
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=True)
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=False)
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=False)
    assert mgr.state == BackupState.ACTIVE


# ---------------------------------------------------------------------------
# emergency_time_remaining_sec
# ---------------------------------------------------------------------------

def test_time_remaining_when_standby_equals_max():
    mgr = BackupPowerManager("HYBRID")
    assert mgr.emergency_time_remaining_sec == pytest.approx(mgr._max_flight_sec, abs=0.1)


def test_time_remaining_decreases_while_active():
    import time
    mgr = BackupPowerManager("HYBRID")
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=True)
    mgr.update_telemetry(voltage=25.0, current=5.0, temperature=20.0, tether_connected=False)
    t1 = mgr.emergency_time_remaining_sec
    time.sleep(0.05)
    t2 = mgr.emergency_time_remaining_sec
    assert t2 < t1


def test_time_remaining_never_negative():
    import time
    mgr = BackupPowerManager("SUPERCAP")
    mgr.update_telemetry(voltage=16.0, current=5.0, temperature=20.0, tether_connected=True)
    mgr.update_telemetry(voltage=16.0, current=5.0, temperature=20.0, tether_connected=False)
    time.sleep(0.1)
    assert mgr.emergency_time_remaining_sec >= 0.0


# ---------------------------------------------------------------------------
# is_emergency_time_critical
# ---------------------------------------------------------------------------

def test_not_critical_in_standby():
    mgr = BackupPowerManager("HYBRID")
    assert mgr.is_emergency_time_critical is False


def test_critical_detected_near_end(monkeypatch):
    mgr = BackupPowerManager("SUPERCAP")
    mgr.update_telemetry(voltage=16.0, current=5.0, temperature=20.0, tether_connected=True)
    mgr.update_telemetry(voltage=16.0, current=5.0, temperature=20.0, tether_connected=False)
    # Подделаем _emergency_flight_start так, чтобы осталось 5 с
    import time
    mgr._emergency_flight_start = time.monotonic() - (mgr._max_flight_sec - 5.0)
    assert mgr.is_emergency_time_critical is True


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------

def test_is_ready_in_standby_full_charge():
    mgr = BackupPowerManager("LIPO")
    mgr._capacity_remaining_pct = 100.0
    mgr._health_pct = 100.0
    assert mgr.is_ready is True


def test_not_ready_when_depleted():
    mgr = BackupPowerManager("LIPO")
    mgr._capacity_remaining_pct = 50.0
    assert mgr.is_ready is False


# ---------------------------------------------------------------------------
# get_status_report
# ---------------------------------------------------------------------------

def test_status_report_keys():
    mgr = BackupPowerManager("HYBRID")
    report = mgr.get_status_report()
    for key in ("type", "state", "voltage", "capacity_pct", "weight_kg", "emergency_time_sec"):
        assert key in report


# ---------------------------------------------------------------------------
# Simple property getters
# ---------------------------------------------------------------------------

def test_backup_type_property():
    mgr = BackupPowerManager("SUPERCAP")
    assert mgr.backup_type == BackupType.SUPERCAP


def test_voltage_property_reflects_telemetry():
    mgr = BackupPowerManager("LIPO")
    mgr.update_telemetry(voltage=22.0, current=1.0, temperature=20.0, tether_connected=True)
    assert mgr.voltage == pytest.approx(22.0)


def test_weight_kg_property_hybrid():
    mgr = BackupPowerManager("HYBRID")
    assert mgr.weight_kg == pytest.approx(config.BACKUP_LIPO_WEIGHT_KG + 0.3)


# ---------------------------------------------------------------------------
# _check_health temperature branches (LIPO/HYBRID)
# ---------------------------------------------------------------------------

def test_check_health_overheat_reduces_health():
    mgr = BackupPowerManager("LIPO")
    mgr._last_health_check = 0.0
    mgr.update_telemetry(voltage=22.0, current=1.0, temperature=50.0, tether_connected=True)
    assert mgr._health_pct < 100.0


def test_check_health_overcool_reduces_health():
    mgr = BackupPowerManager("HYBRID")
    mgr._last_health_check = 0.0
    mgr.update_telemetry(voltage=22.0, current=1.0, temperature=-20.0, tether_connected=True)
    assert mgr._health_pct < 100.0


def test_check_health_supercap_ignores_temperature():
    # SUPERCAP is not in (LIPO, HYBRID) so the temperature branch is skipped
    mgr = BackupPowerManager("SUPERCAP")
    mgr._last_health_check = 0.0
    mgr.update_telemetry(voltage=16.0, current=1.0, temperature=90.0, tether_connected=True)
    assert mgr._health_pct == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# request_emergency_landing
# ---------------------------------------------------------------------------

def test_request_emergency_landing_when_not_active():
    mgr = BackupPowerManager("HYBRID")
    assert mgr.request_emergency_landing() is False


def test_request_emergency_landing_when_active():
    mgr = BackupPowerManager("HYBRID")
    mgr.update_telemetry(voltage=22.0, current=1.0, temperature=20.0, tether_connected=True)
    mgr.update_telemetry(voltage=22.0, current=1.0, temperature=20.0, tether_connected=False)
    assert mgr.state == BackupState.ACTIVE
    assert mgr.request_emergency_landing() is True
