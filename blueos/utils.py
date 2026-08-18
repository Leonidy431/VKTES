"""
Геометрические утилиты общего назначения для BlueOS.

Централизованные функции, используемые в perception.py, planner.py и
hover_blow.py для работы с полигонами крыши и буферными зонами препятствий.
"""

import math


def point_in_polygon(x: float, y: float, vertices: list[tuple[float, float]]) -> bool:
    """Ray casting: True если точка (x, y) внутри полигона vertices."""
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def in_obstacle_zone(
    x: float,
    y: float,
    obstacles: list[tuple[float, float, float]] | None,
    buffer_m: float = 0.0,
) -> bool:
    """True если точка попадает в буферную зону любого препятствия (ox, oy, radius)."""
    if not obstacles:
        return False
    for ox, oy, radius in obstacles:
        if math.hypot(x - ox, y - oy) < radius + buffer_m:
            return True
    return False


def polygon_area(vertices: list[tuple[float, float]]) -> float:
    """Площадь полигона (формула Шнурка / Гаусса)."""
    n = len(vertices)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        j = (i + 1) % n
        a += vertices[i][0] * vertices[j][1]
        a -= vertices[j][0] * vertices[i][1]
    return abs(a) / 2.0


def polygon_centroid(vertices: list[tuple[float, float]]) -> tuple[float, float]:
    """Центроид (центр масс) полигона."""
    if not vertices:
        return 0.0, 0.0
    cx = sum(v[0] for v in vertices) / len(vertices)
    cy = sum(v[1] for v in vertices) / len(vertices)
    return cx, cy


def point_to_segment_distance(
    px: float, py: float, ax: float, ay: float, bx: float, by: float,
) -> float:
    """Кратчайшее расстояние от точки (px,py) до отрезка (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def distance_to_polygon_boundary(
    x: float, y: float, vertices: list[tuple[float, float]],
) -> float:
    """Кратчайшее расстояние от точки до ближайшего ребра полигона."""
    n = len(vertices)
    if n < 2:
        return float("inf")
    best = float("inf")
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        d = point_to_segment_distance(x, y, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def bounding_box(
    vertices: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    """Возвращает (x_min, y_min, x_max, y_max) для набора точек."""
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return min(xs), min(ys), max(xs), max(ys)
