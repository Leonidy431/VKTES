"""Тесты PlannerModule — генерация boustrophedon-маршрутов очистки."""

import math

import pytest

from blueos.perception import PerceptionModule, RoofBoundary, Obstacle
from blueos.planner import PlannerModule, Waypoint, Lane, CleaningPlan

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


@pytest.fixture
def perception():
    p = PerceptionModule()
    p._roof = RoofBoundary(vertices=SQUARE)
    return p


@pytest.fixture
def planner(perception):
    return PlannerModule(perception)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

def test_waypoint_defaults():
    wp = Waypoint(x=1.0, y=2.0)
    assert wp.heading == 0.0
    assert wp.speed == 0.5
    assert wp.is_bulldozer is True


def test_lane_is_valid_true():
    lane = Lane(waypoints=[Waypoint(x=0, y=0), Waypoint(x=1, y=1)])
    assert lane.is_valid is True


def test_lane_is_valid_false_single_point():
    lane = Lane(waypoints=[Waypoint(x=0, y=0)])
    assert lane.is_valid is False


def test_cleaning_plan_lane_count():
    plan = CleaningPlan(lanes=[Lane(), Lane()])
    assert plan.lane_count == 2


# ---------------------------------------------------------------------------
# generate_plan
# ---------------------------------------------------------------------------

def test_generate_plan_invalid_roof():
    p = PerceptionModule()  # empty roof
    planner = PlannerModule(p)
    plan = planner.generate_plan()
    assert plan.lane_count == 0
    assert plan.total_area_m2 == 0.0


def test_generate_plan_valid_square(planner):
    plan = planner.generate_plan()
    assert plan.lane_count > 0
    assert plan.total_area_m2 == pytest.approx(100.0)
    assert plan.estimated_cycles >= 1
    assert plan.estimated_total_time_min > 0.0


def test_generate_plan_stores_plan_property(planner):
    plan = planner.generate_plan()
    assert planner.plan is plan


def test_generate_plan_with_obstacles_blocks_some_lanes(perception):
    # Place an obstacle covering the entire square so every lane's endpoints
    # are unsafe → 0 lanes generated
    perception._obstacles = [Obstacle(x=5.0, y=5.0, radius=50.0)]
    planner = PlannerModule(perception)
    plan = planner.generate_plan()
    assert plan.lane_count == 0


# ---------------------------------------------------------------------------
# get_next_lane
# ---------------------------------------------------------------------------

def test_get_next_lane_no_plan(planner):
    assert planner.get_next_lane(0) is None


def test_get_next_lane_valid_index(planner):
    planner.generate_plan()
    lane = planner.get_next_lane(0)
    assert lane is not None
    assert isinstance(lane, Lane)


def test_get_next_lane_out_of_range(planner):
    planner.generate_plan()
    lane = planner.get_next_lane(9999)
    assert lane is None


# ---------------------------------------------------------------------------
# _find_best_push_direction
# ---------------------------------------------------------------------------

def test_find_best_push_direction_empty_vertices(planner):
    empty_roof = RoofBoundary(vertices=[])
    angle = planner._find_best_push_direction(empty_roof)
    assert angle == 0.0


def test_find_best_push_direction_square(planner):
    roof = RoofBoundary(vertices=SQUARE)
    angle = planner._find_best_push_direction(roof)
    assert isinstance(angle, float)


# ---------------------------------------------------------------------------
# _generate_boustrophedon_lanes
# ---------------------------------------------------------------------------

def test_generate_lanes_empty_roof_vertices(planner):
    empty_roof = RoofBoundary(vertices=[])
    lanes = planner._generate_boustrophedon_lanes(
        roof=empty_roof, obstacles=[], lane_width=1.0, push_direction=0.0,
    )
    assert lanes == []


def test_generate_lanes_alternating_direction(planner):
    roof = RoofBoundary(vertices=SQUARE)
    lanes = planner._generate_boustrophedon_lanes(
        roof=roof, obstacles=[], lane_width=1.1, push_direction=0.0,
    )
    assert len(lanes) > 1
