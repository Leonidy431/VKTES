"""Тесты geometрических утилит blueos/utils.py."""

import pytest

from blueos.utils import (
    bounding_box,
    distance_to_polygon_boundary,
    in_obstacle_zone,
    point_in_polygon,
    point_to_segment_distance,
    polygon_area,
    polygon_centroid,
)

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


# ---------------------------------------------------------------------------
# point_in_polygon
# ---------------------------------------------------------------------------

def test_point_inside_square():
    assert point_in_polygon(5.0, 5.0, SQUARE) is True


def test_point_outside_square():
    assert point_in_polygon(15.0, 5.0, SQUARE) is False


def test_point_outside_negative():
    assert point_in_polygon(-1.0, 5.0, SQUARE) is False


def test_point_on_edge_or_near():
    # Near-boundary points; ray casting behavior for exact edge is implementation-defined,
    # but should not raise
    result = point_in_polygon(0.0, 5.0, SQUARE)
    assert isinstance(result, bool)


def test_point_in_triangle():
    triangle = [(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]
    assert point_in_polygon(5.0, 3.0, triangle) is True
    assert point_in_polygon(9.0, 9.0, triangle) is False


# ---------------------------------------------------------------------------
# in_obstacle_zone
# ---------------------------------------------------------------------------

def test_in_obstacle_zone_none_obstacles():
    assert in_obstacle_zone(1.0, 1.0, None) is False


def test_in_obstacle_zone_empty_list():
    assert in_obstacle_zone(1.0, 1.0, []) is False


def test_in_obstacle_zone_hit():
    obstacles = [(5.0, 5.0, 1.0)]
    assert in_obstacle_zone(5.0, 5.0, obstacles) is True


def test_in_obstacle_zone_miss():
    obstacles = [(5.0, 5.0, 1.0)]
    assert in_obstacle_zone(20.0, 20.0, obstacles) is False


def test_in_obstacle_zone_with_buffer():
    obstacles = [(5.0, 5.0, 1.0)]
    # 1.5m away from center, radius 1.0 → outside without buffer, inside with buffer 1.0
    assert in_obstacle_zone(6.5, 5.0, obstacles, buffer_m=0.0) is False
    assert in_obstacle_zone(6.5, 5.0, obstacles, buffer_m=1.0) is True


def test_in_obstacle_zone_multiple():
    obstacles = [(0.0, 0.0, 1.0), (10.0, 10.0, 1.0)]
    assert in_obstacle_zone(10.0, 10.0, obstacles) is True
    assert in_obstacle_zone(5.0, 5.0, obstacles) is False


# ---------------------------------------------------------------------------
# polygon_area
# ---------------------------------------------------------------------------

def test_polygon_area_square():
    assert polygon_area(SQUARE) == pytest.approx(100.0)


def test_polygon_area_triangle():
    triangle = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]
    assert polygon_area(triangle) == pytest.approx(50.0)


def test_polygon_area_too_few_vertices():
    assert polygon_area([(0.0, 0.0), (1.0, 1.0)]) == 0.0
    assert polygon_area([]) == 0.0


# ---------------------------------------------------------------------------
# polygon_centroid
# ---------------------------------------------------------------------------

def test_polygon_centroid_square():
    cx, cy = polygon_centroid(SQUARE)
    assert cx == pytest.approx(5.0)
    assert cy == pytest.approx(5.0)


def test_polygon_centroid_empty():
    cx, cy = polygon_centroid([])
    assert cx == 0.0
    assert cy == 0.0


# ---------------------------------------------------------------------------
# bounding_box
# ---------------------------------------------------------------------------

def test_bounding_box_square():
    x_min, y_min, x_max, y_max = bounding_box(SQUARE)
    assert (x_min, y_min, x_max, y_max) == (0.0, 0.0, 10.0, 10.0)


def test_bounding_box_single_point():
    x_min, y_min, x_max, y_max = bounding_box([(3.0, 4.0)])
    assert (x_min, y_min, x_max, y_max) == (3.0, 4.0, 3.0, 4.0)


# ---------------------------------------------------------------------------
# point_to_segment_distance
# ---------------------------------------------------------------------------

def test_point_to_segment_distance_perpendicular():
    # Point directly above the middle of a horizontal segment.
    d = point_to_segment_distance(5.0, 3.0, 0.0, 0.0, 10.0, 0.0)
    assert d == pytest.approx(3.0)


def test_point_to_segment_distance_beyond_endpoint():
    # Point past segment end -> distance to nearest endpoint.
    d = point_to_segment_distance(15.0, 0.0, 0.0, 0.0, 10.0, 0.0)
    assert d == pytest.approx(5.0)


def test_point_to_segment_distance_degenerate_segment():
    # Zero-length segment (a == b) -> plain point distance.
    d = point_to_segment_distance(3.0, 4.0, 0.0, 0.0, 0.0, 0.0)
    assert d == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# distance_to_polygon_boundary
# ---------------------------------------------------------------------------

def test_distance_to_polygon_boundary_center():
    d = distance_to_polygon_boundary(5.0, 5.0, SQUARE)
    assert d == pytest.approx(5.0)


def test_distance_to_polygon_boundary_too_few_vertices():
    assert distance_to_polygon_boundary(0.0, 0.0, [(0.0, 0.0)]) == float("inf")
