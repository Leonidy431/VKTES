"""
Инварианты config.py — BLIND SPOTS BS-89.

Раньше ни один тест/lint не проверял, что константы конфигурации остаются
в разумных диапазонах (проценты в [0,100], пороги напряжения упорядочены,
таблицы монотонны и т.д.). Ошибочная константа (опечатка, минус потерян,
единицы перепутаны) проходила бы через ruff/mypy/pytest незамеченной, пока
не проявилась бы в полёте. Этот файл — граница защиты от таких регрессий.
"""

import math

import pytest

from blueos import config

# ---------------------------------------------------------------------------
# Проценты и доли
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    config.BURST_PULSE_THROTTLE_HIGH,
    config.BURST_PULSE_THROTTLE_LOW,
    config.HOVER_BLOW_THROTTLE_PCT,
])
def test_percent_constants_in_range(value):
    assert 0.0 <= value <= 100.0


@pytest.mark.parametrize("value", [
    config.ONBOARD_CONVERTER_EFFICIENCY,
    config.THERMAL_MIN_CONFIDENCE_FOR_HOVER_BLOW,
])
def test_fraction_constants_in_unit_interval(value):
    assert 0.0 <= value <= 1.0


def test_burst_pulse_high_above_low():
    assert config.BURST_PULSE_THROTTLE_HIGH > config.BURST_PULSE_THROTTLE_LOW


# ---------------------------------------------------------------------------
# Тяга и наклон
# ---------------------------------------------------------------------------

def test_tilt_bounds_ordered():
    assert 0.0 <= config.TILT_MIN_DEG < config.TILT_MAX_DEG <= 90.0


def test_tilt_default_within_bounds():
    assert config.TILT_MIN_DEG <= config.TILT_DEFAULT_BULLDOZER_DEG <= config.TILT_MAX_DEG


def test_tilt_step_positive():
    assert config.TILT_STEP_DEG_PER_SEC > 0.0


def test_tilt_thrust_table_keys_within_bounds():
    for angle in config.TILT_THRUST_TABLE:
        assert config.TILT_MIN_DEG <= angle <= config.TILT_MAX_DEG


def test_tilt_thrust_table_values_positive_at_nonzero_angle():
    for angle, (horizontal, vertical) in config.TILT_THRUST_TABLE.items():
        assert horizontal >= 0.0
        assert vertical > 0.0
        if angle > 0:
            assert horizontal > 0.0


def test_tilt_thrust_table_horizontal_monotonic_increasing():
    angles = sorted(config.TILT_THRUST_TABLE)
    horizontals = [config.TILT_THRUST_TABLE[a][0] for a in angles]
    assert horizontals == sorted(horizontals)


def test_tilt_thrust_table_vertical_monotonic_decreasing():
    angles = sorted(config.TILT_THRUST_TABLE)
    verticals = [config.TILT_THRUST_TABLE[a][1] for a in angles]
    assert verticals == sorted(verticals, reverse=True)


def test_tilt_thrust_table_pythagorean_consistency():
    # h^2 + v^2 should stay close to MAX_STATIC_THRUST_KGF^2 for every angle
    # (both components come from decomposing the same total static thrust).
    total = config.MAX_STATIC_THRUST_KGF
    for angle, (h, v) in config.TILT_THRUST_TABLE.items():
        magnitude = math.hypot(h, v)
        assert magnitude == pytest.approx(total, rel=0.02)


def test_snow_resistance_increases_with_density():
    assert (
        config.SNOW_RESISTANCE_POWDER_KGF_M
        < config.SNOW_RESISTANCE_SETTLED_KGF_M
        < config.SNOW_RESISTANCE_WET_KGF_M
    )


# ---------------------------------------------------------------------------
# Энергосистема / напряжения
# ---------------------------------------------------------------------------

def test_voltage_thresholds_ordered():
    assert (
        config.VOLTAGE_EMERGENCY
        < config.VOLTAGE_LOW_POWER
        < config.VOLTAGE_BURST_READY
        <= config.SUPERCAP_NOMINAL_VOLTAGE
    )


def test_supercap_min_below_nominal():
    assert config.SUPERCAP_MIN_VOLTAGE < config.SUPERCAP_NOMINAL_VOLTAGE


def test_energy_positive():
    assert config.SUPERCAP_MAX_ENERGY_KJ > 0.0
    assert config.SUPERCAP_CAPACITANCE > 0.0


# ---------------------------------------------------------------------------
# Электробезопасность
# ---------------------------------------------------------------------------

def test_gfci_thresholds_positive():
    assert config.SAFETY_GFCI_TRIP_MA > 0.0
    assert config.SAFETY_GFCI_TRIP_TIME_MS > 0


def test_insulation_warn_above_min():
    assert config.SAFETY_INSULATION_WARN_KOHM > config.SAFETY_INSULATION_MIN_KOHM > 0.0


def test_arc_freq_range_ordered():
    lo, hi = config.SAFETY_ARC_FREQ_RANGE_KHZ
    assert 0.0 < lo < hi


def test_voltage_imbalance_threshold_positive():
    assert config.SAFETY_VOLTAGE_IMBALANCE_MAX_V > 0.0


def test_alt_voltages_below_tether_voltage():
    assert config.SAFETY_ALT_VOLTAGE_120V < config.SAFETY_ALT_VOLTAGE_200V < config.TETHER_VOLTAGE


# ---------------------------------------------------------------------------
# Тепловизор
# ---------------------------------------------------------------------------

def test_thermal_temp_ranges_ordered():
    fresh_lo, fresh_hi = config.THERMAL_SNOW_TEMP_FRESH_C
    wet_lo, wet_hi = config.THERMAL_SNOW_TEMP_WET_C
    assert fresh_lo < fresh_hi
    assert wet_lo < wet_hi


def test_thermal_deltas_positive():
    assert config.THERMAL_PIPE_DELTA_C > 0.0
    assert config.THERMAL_VENT_DELTA_C > config.THERMAL_PIPE_DELTA_C


# ---------------------------------------------------------------------------
# Резервное питание
# ---------------------------------------------------------------------------

def test_backup_type_is_known():
    assert config.BACKUP_TYPE in ("LIPO", "SUPERCAP", "HYBRID")


def test_backup_capacity_positive():
    assert config.BACKUP_LIPO_CAPACITY_MAH > 0
    assert config.BACKUP_SUPERCAP_F > 0
    assert config.BACKUP_SUPERCAP_SERIES > 0


def test_backup_emergency_flight_positive():
    assert config.BACKUP_EMERGENCY_FLIGHT_SEC > 0
    assert config.BACKUP_SWITCHOVER_MS > 0


# ---------------------------------------------------------------------------
# Частоты и таймауты
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hz", [
    config.TELEMETRY_RATE_HZ,
    config.PERCEPTION_RATE_HZ,
    config.SAFETY_CHECK_RATE_HZ,
    config.THERMAL_FPS,
])
def test_rates_positive(hz):
    assert hz > 0


def test_burst_and_charge_durations_positive():
    assert config.BURST_MAX_DURATION_SEC > 0
    assert config.CHARGE_TIMEOUT_SEC > config.CHARGE_TIME_SEC > 0


# ---------------------------------------------------------------------------
# Высоты — здравый порядок (зависание < перелёт < облёт)
# ---------------------------------------------------------------------------

def test_altitude_ordering():
    assert (
        config.HOVER_BLOW_ALTITUDE_M
        < config.TRANSIT_ALTITUDE_M
        < config.SURVEY_ALTITUDE_M
        < config.GEOFENCE_MAX_ALTITUDE_M
    )


def test_hover_blow_max_snow_depth_positive():
    assert config.HOVER_BLOW_MAX_SNOW_DEPTH_MM > 0


# ---------------------------------------------------------------------------
# Геозабор и физическая платформа
# ---------------------------------------------------------------------------

def test_geofence_margin_positive():
    assert config.GEOFENCE_MARGIN_M > 0.0


def test_motor_count_matches_x8():
    assert config.MOTOR_COUNT == 8


def test_physical_weights_positive():
    assert config.DRONE_WEIGHT_KG > 0.0
    assert config.BLADE_WEIGHT_KG > 0.0
    assert config.MAX_STATIC_THRUST_KGF > 0.0


def test_thrust_to_weight_ratio_at_least_2(  # engineering_review.md §5
):
    ratio = config.MAX_STATIC_THRUST_KGF / config.DRONE_WEIGHT_KG
    assert ratio >= 2.0


def test_tether_length_positive():
    assert config.TETHER_LENGTH_M > 0.0


def test_min_roof_edge_distance_non_negative():
    assert config.MIN_ROOF_EDGE_DISTANCE_M >= 0.0
