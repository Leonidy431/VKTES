"""Тесты HoverBlowController — режим обдува зависанием."""

import pytest

from blueos.hover_blow import HoverBlowController, HoverBlowPlan, BlowPoint
from blueos import config

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


@pytest.fixture
def ctrl():
    return HoverBlowController()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

def test_blow_point_defaults():
    p = BlowPoint(x=1.0, y=2.0)
    assert p.completed is False
    assert p.dwell_elapsed == 0.0


def test_plan_is_complete_empty():
    plan = HoverBlowPlan()
    assert plan.is_complete is True


def test_plan_progress_percent_empty():
    plan = HoverBlowPlan()
    assert plan.progress_percent == 100.0


def test_plan_progress_percent_partial():
    plan = HoverBlowPlan(points=[BlowPoint(x=0, y=0), BlowPoint(x=1, y=1)], current_index=1)
    assert plan.progress_percent == pytest.approx(50.0)


def test_plan_current_point_none_when_finished():
    plan = HoverBlowPlan(points=[BlowPoint(x=0, y=0)], current_index=1)
    assert plan.current_point is None


def test_plan_current_point_valid():
    pt = BlowPoint(x=5.0, y=6.0)
    plan = HoverBlowPlan(points=[pt], current_index=0)
    assert plan.current_point is pt


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_not_active(ctrl):
    assert ctrl.is_active is False


def test_initial_plan_none(ctrl):
    assert ctrl.plan is None


def test_initial_throttle_and_altitude(ctrl):
    assert ctrl.throttle == config.HOVER_BLOW_THROTTLE_PCT
    assert ctrl.altitude == config.HOVER_BLOW_ALTITUDE_M


# ---------------------------------------------------------------------------
# generate_plan
# ---------------------------------------------------------------------------

def test_generate_plan_too_few_vertices(ctrl):
    plan = ctrl.generate_plan([(0.0, 0.0), (1.0, 1.0)])
    assert plan.points == []


def test_generate_plan_valid_square_no_obstacles(ctrl):
    plan = ctrl.generate_plan(SQUARE)
    assert len(plan.points) > 0
    assert plan.total_area_m2 == pytest.approx(100.0)
    assert plan.estimated_time_sec > 0.0


def test_generate_plan_with_obstacle_blocking_everything(ctrl):
    obstacles = [(5.0, 5.0, 50.0)]
    plan = ctrl.generate_plan(SQUARE, obstacles=obstacles)
    assert len(plan.points) == 0


def test_generate_plan_with_small_obstacle(ctrl):
    plan_no_obs = ctrl.generate_plan(SQUARE)
    plan_with_obs = ctrl.generate_plan(SQUARE, obstacles=[(5.0, 5.0, 1.0)])
    assert len(plan_with_obs.points) <= len(plan_no_obs.points)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def test_start_without_plan_does_nothing(ctrl):
    ctrl.start()
    assert ctrl.is_active is False


def test_start_with_empty_plan_does_nothing(ctrl):
    ctrl._plan = HoverBlowPlan(points=[])
    ctrl.start()
    assert ctrl.is_active is False


def test_start_with_valid_plan(ctrl):
    ctrl.generate_plan(SQUARE)
    ctrl.start()
    assert ctrl.is_active is True


def test_stop_with_no_plan(ctrl):
    ctrl.stop()  # Should not raise even with self._plan is None
    assert ctrl.is_active is False


def test_stop_with_plan(ctrl):
    ctrl.generate_plan(SQUARE)
    ctrl.start()
    ctrl.stop()
    assert ctrl.is_active is False


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_update_not_active_returns_hover_position(ctrl):
    x, y, z, done = ctrl.update(0.1)
    assert x == 0
    assert y == 0
    assert z == -ctrl.altitude
    assert done is False


def test_update_plan_complete_when_current_point_none(ctrl):
    ctrl._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0)], current_index=1)
    ctrl._active = True
    x, y, z, done = ctrl.update(0.1)
    assert done is True
    assert ctrl.is_active is False


def test_update_point_not_done_before_dwell(ctrl):
    ctrl._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0), BlowPoint(x=3.0, y=4.0)])
    ctrl._active = True
    x, y, z, done = ctrl.update(0.1)
    assert done is False
    assert x == 1.0
    assert y == 2.0


def test_update_point_done_advances_to_next(ctrl):
    ctrl._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0), BlowPoint(x=3.0, y=4.0)])
    ctrl._active = True
    x, y, z, done = ctrl.update(config.HOVER_BLOW_DWELL_SEC + 1.0)
    assert done is True
    assert ctrl._plan.current_index == 1
    assert ctrl.is_active is True  # plan not complete yet


def test_update_last_point_completes_plan(ctrl):
    ctrl._plan = HoverBlowPlan(points=[BlowPoint(x=1.0, y=2.0)])
    ctrl._active = True
    x, y, z, done = ctrl.update(config.HOVER_BLOW_DWELL_SEC + 1.0)
    assert done is True
    assert ctrl.is_active is False
    assert ctrl._plan.is_complete is True


# ---------------------------------------------------------------------------
# adjust_for_snow_depth
# ---------------------------------------------------------------------------

def test_adjust_for_shallow_snow(ctrl):
    ctrl.adjust_for_snow_depth(30.0)
    assert ctrl.altitude == pytest.approx(2.0)
    assert ctrl.throttle == pytest.approx(70.0)


def test_adjust_for_medium_snow(ctrl):
    ctrl.adjust_for_snow_depth(75.0)
    assert ctrl.altitude == pytest.approx(1.5)
    assert ctrl.throttle == pytest.approx(85.0)


def test_adjust_for_deep_snow(ctrl):
    ctrl.adjust_for_snow_depth(150.0)
    assert ctrl.altitude == pytest.approx(1.0)
    assert ctrl.throttle == pytest.approx(95.0)
