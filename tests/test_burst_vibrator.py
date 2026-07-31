"""Тесты BurstVibrator — импульсный режим тяги."""

import time

import pytest

from blueos.burst_vibrator import BurstMode, BurstVibrator


@pytest.fixture
def bv():
    return BurstVibrator()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_mode_is_continuous(bv):
    assert bv.mode == BurstMode.CONTINUOUS


def test_initial_enabled_from_config(bv):
    assert bv.is_enabled is True


def test_initial_pulse_count_zero(bv):
    assert bv.pulse_count == 0


# ---------------------------------------------------------------------------
# set_mode
# ---------------------------------------------------------------------------

def test_set_mode_same_is_noop(bv):
    bv.set_mode(BurstMode.CONTINUOUS)
    cycle_start_before = bv._cycle_start
    bv.set_mode(BurstMode.CONTINUOUS)
    assert bv._cycle_start == cycle_start_before


def test_set_mode_pulsed(bv):
    bv.set_mode(BurstMode.PULSED)
    assert bv.mode == BurstMode.PULSED
    assert bv._on_duration == pytest.approx(0.8)
    assert bv._off_duration == pytest.approx(0.3)


def test_set_mode_hammer(bv):
    bv.set_mode(BurstMode.HAMMER)
    assert bv.mode == BurstMode.HAMMER
    assert bv._on_duration == pytest.approx(0.3)
    assert bv._off_duration == pytest.approx(0.7)
    assert bv._throttle_high == pytest.approx(100.0)
    assert bv._throttle_low == pytest.approx(30.0)


def test_set_mode_ramp(bv):
    bv.set_mode(BurstMode.RAMP)
    assert bv.mode == BurstMode.RAMP
    assert bv._ramp_progress == 0.0
    assert bv._throttle_high == pytest.approx(100.0)
    assert bv._throttle_low == pytest.approx(20.0)


def test_set_mode_continuous_explicit(bv):
    bv.set_mode(BurstMode.PULSED)
    bv.set_mode(BurstMode.CONTINUOUS)
    assert bv._throttle_high == pytest.approx(95.0)
    assert bv._throttle_low == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# get_throttle
# ---------------------------------------------------------------------------

def test_get_throttle_disabled_returns_high(bv):
    bv._enabled = False
    bv._throttle_high = 42.0
    assert bv.get_throttle() == pytest.approx(42.0)


def test_get_throttle_continuous_default(bv):
    # Mode is CONTINUOUS from __init__; set_mode() is a no-op for same mode,
    # so throttle_high retains its config default.
    assert bv.get_throttle() == pytest.approx(config_throttle_high())


def config_throttle_high():
    from blueos import config
    return config.BURST_PULSE_THROTTLE_HIGH


def test_get_throttle_continuous_after_switch(bv):
    bv.set_mode(BurstMode.PULSED)
    bv.set_mode(BurstMode.CONTINUOUS)
    assert bv.get_throttle() == pytest.approx(95.0)


def test_get_throttle_pulsed_on_phase_and_pulse_count(bv):
    bv.set_mode(BurstMode.PULSED)
    # Force elapsed just past a full cycle boundary → ON phase, pulse counted
    cycle_duration = bv._on_duration + bv._off_duration
    bv._cycle_start = time.monotonic() - cycle_duration - 0.001
    throttle = bv.get_throttle()
    assert throttle == pytest.approx(bv._throttle_high)
    assert bv.pulse_count == 1


def test_get_throttle_pulsed_off_phase(bv):
    bv.set_mode(BurstMode.PULSED)
    bv._cycle_start = time.monotonic() - (bv._on_duration + 0.1)
    throttle = bv.get_throttle()
    assert throttle == pytest.approx(bv._throttle_low)


def test_get_throttle_ramp_midway(bv):
    bv.set_mode(BurstMode.RAMP)
    bv._cycle_start = time.monotonic() - 1.5  # 50% of 3.0s ramp
    throttle = bv.get_throttle()
    expected = bv._throttle_low + (bv._throttle_high - bv._throttle_low) * 0.5
    assert throttle == pytest.approx(expected, abs=2.0)
    assert bv.mode == BurstMode.RAMP


def test_get_throttle_ramp_completes_switches_to_pulsed(bv):
    bv.set_mode(BurstMode.RAMP)
    bv._cycle_start = time.monotonic() - 3.5  # past ramp_duration
    throttle = bv.get_throttle()
    assert throttle == pytest.approx(100.0, abs=0.5)
    assert bv.mode == BurstMode.PULSED


# ---------------------------------------------------------------------------
# get_visibility_window
# ---------------------------------------------------------------------------

def test_visibility_window_disabled(bv):
    bv._enabled = False
    assert bv.get_visibility_window() is False


def test_visibility_window_continuous(bv):
    bv.set_mode(BurstMode.CONTINUOUS)
    assert bv.get_visibility_window() is False


def test_visibility_window_true_in_late_off_phase(bv):
    bv.set_mode(BurstMode.PULSED)
    # off_mid = on_duration + off_duration*0.5 = 0.8 + 0.15 = 0.95
    bv._cycle_start = time.monotonic() - 1.0
    assert bv.get_visibility_window() is True


def test_visibility_window_false_in_on_phase(bv):
    bv.set_mode(BurstMode.PULSED)
    bv._cycle_start = time.monotonic() - 0.1
    assert bv.get_visibility_window() is False


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def test_reset_clears_counters(bv):
    bv.set_mode(BurstMode.PULSED)
    cycle_duration = bv._on_duration + bv._off_duration
    bv._cycle_start = time.monotonic() - cycle_duration - 0.001
    bv.get_throttle()
    assert bv.pulse_count == 1
    bv.reset()
    assert bv.pulse_count == 0
    assert bv._pulse_count == 0
    assert bv._ramp_progress == 0.0


# ---------------------------------------------------------------------------
# recommend_mode
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snow_type,depth,expected", [
    ("POWDER", 100, BurstMode.CONTINUOUS),
    ("POWDER", 200, BurstMode.PULSED),
    ("SETTLED", 200, BurstMode.PULSED),
    ("WET", 200, BurstMode.HAMMER),
    ("ICE", 50, BurstMode.HAMMER),
    ("UNKNOWN", 100, BurstMode.PULSED),
])
def test_recommend_mode(bv, snow_type, depth, expected):
    assert bv.recommend_mode(snow_type, depth) == expected
