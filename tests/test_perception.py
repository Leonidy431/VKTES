"""Тесты PerceptionModule — обработка LiDAR/камеры."""

import pytest

from blueos.perception import (
    Obstacle,
    PerceptionModule,
    RoofBoundary,
    SnowLayer,
)

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


# ---------------------------------------------------------------------------
# Obstacle
# ---------------------------------------------------------------------------

def test_obstacle_defaults():
    obs = Obstacle(x=1.0, y=2.0, radius=0.5)
    assert obs.label == ""


def test_obstacle_with_label():
    obs = Obstacle(x=1.0, y=2.0, radius=0.5, label="pipe")
    assert obs.label == "pipe"


# ---------------------------------------------------------------------------
# RoofBoundary
# ---------------------------------------------------------------------------

def test_roof_boundary_empty_invalid():
    roof = RoofBoundary()
    assert roof.is_valid is False


def test_roof_boundary_valid_with_3_vertices():
    roof = RoofBoundary(vertices=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)])
    assert roof.is_valid is True


def test_roof_boundary_contains_inside():
    roof = RoofBoundary(vertices=SQUARE)
    assert roof.contains(5.0, 5.0) is True


def test_roof_boundary_contains_outside():
    roof = RoofBoundary(vertices=SQUARE)
    assert roof.contains(20.0, 20.0) is False


def test_roof_boundary_contains_invalid_roof():
    roof = RoofBoundary()
    assert roof.contains(1.0, 1.0) is False


def test_roof_boundary_area_square():
    roof = RoofBoundary(vertices=SQUARE)
    assert roof.area() == pytest.approx(100.0)


def test_roof_boundary_area_invalid():
    roof = RoofBoundary()
    assert roof.area() == 0.0


# ---------------------------------------------------------------------------
# SnowLayer
# ---------------------------------------------------------------------------

def test_snow_layer_defaults():
    layer = SnowLayer(x=1.0, y=2.0, depth_mm=50.0)
    assert layer.surface == "snow"


# ---------------------------------------------------------------------------
# PerceptionModule
# ---------------------------------------------------------------------------

@pytest.fixture
def perception():
    return PerceptionModule()


def test_perception_initial_roof_invalid(perception):
    assert perception.roof.is_valid is False


def test_perception_initial_obstacles_empty(perception):
    assert perception.obstacles == []


def test_perception_initial_snow_grid_empty(perception):
    assert perception.snow_grid == []


def test_process_survey_frame_with_lidar_points(perception):
    perception.process_survey_frame(
        image=None,
        lidar_points=[(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)],
        drone_position=(0.0, 0.0, 10.0),
    )  # Should not raise


def test_process_survey_frame_without_lidar_points(perception):
    perception.process_survey_frame(image=None)  # Should not raise


def test_detect_roof_boundary(perception):
    roof = perception.detect_roof_boundary([(0.0, 0.0, 0.0)])
    assert isinstance(roof, RoofBoundary)


def test_detect_obstacles(perception):
    obstacles = perception.detect_obstacles([(0.0, 0.0, 0.0)])
    assert obstacles == []


def test_estimate_snow_depth_basic(perception):
    points = [(0.0, 0.0, 0.05), (1.0, 1.0, 0.0)]
    grid = perception.estimate_snow_depth(points, reference_surface_z=0.0)
    assert len(grid) == 2
    assert grid[0].depth_mm == pytest.approx(50.0)
    assert grid[0].surface == "snow"
    assert grid[1].depth_mm == pytest.approx(0.0)
    assert grid[1].surface == "clear"


def test_estimate_snow_depth_empty_points(perception):
    grid = perception.estimate_snow_depth([], reference_surface_z=0.0)
    assert grid == []


def test_estimate_snow_depth_updates_snow_grid_property(perception):
    perception.estimate_snow_depth([(0.0, 0.0, 0.05)], reference_surface_z=0.0)
    assert len(perception.snow_grid) == 1


# ---------------------------------------------------------------------------
# is_point_safe
# ---------------------------------------------------------------------------

def test_is_point_safe_no_roof_defined(perception):
    # Roof invalid → contains() always False in RoofBoundary, but is_point_safe
    # only checks contains when roof.is_valid — with no roof, considered safe
    assert perception.is_point_safe(5.0, 5.0) is True


def test_is_point_safe_inside_roof(perception):
    perception._roof = RoofBoundary(vertices=SQUARE)
    assert perception.is_point_safe(5.0, 5.0) is True


def test_is_point_safe_outside_roof(perception):
    perception._roof = RoofBoundary(vertices=SQUARE)
    assert perception.is_point_safe(50.0, 50.0) is False


def test_is_point_safe_near_obstacle(perception):
    perception._roof = RoofBoundary(vertices=SQUARE)
    perception._obstacles = [Obstacle(x=5.0, y=5.0, radius=1.0)]
    assert perception.is_point_safe(5.0, 5.0) is False


def test_is_point_safe_away_from_obstacle(perception):
    perception._roof = RoofBoundary(vertices=SQUARE)
    perception._obstacles = [Obstacle(x=1.0, y=1.0, radius=0.2)]
    assert perception.is_point_safe(9.0, 9.0) is True
