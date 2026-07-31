"""
Модуль восприятия (Perception Module) — обработка данных камеры и LiDAR
для определения границ крыши, препятствий и толщины снежного покрова.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from . import config

logger = logging.getLogger(__name__)


@dataclass
class Obstacle:
    """Обнаруженное препятствие на крыше."""
    x: float            # м — координата X в системе крыши
    y: float            # м — координата Y
    radius: float       # м — радиус зоны исключения
    label: str = ""     # Тип: "pipe", "antenna", "vent", "unknown"


@dataclass
class RoofBoundary:
    """Граница крыши — замкнутый полигон."""
    vertices: list[tuple[float, float]] = field(default_factory=list)  # [(x, y), ...]

    @property
    def is_valid(self) -> bool:
        return len(self.vertices) >= 3

    def contains(self, x: float, y: float) -> bool:
        """Проверка принадлежности точки полигону (ray casting)."""
        if not self.is_valid:
            return False
        n = len(self.vertices)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = self.vertices[i]
            xj, yj = self.vertices[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def area(self) -> float:
        """Площадь полигона (формула шнурка)."""
        if not self.is_valid:
            return 0.0
        n = len(self.vertices)
        a = 0.0
        for i in range(n):
            j = (i + 1) % n
            a += self.vertices[i][0] * self.vertices[j][1]
            a -= self.vertices[j][0] * self.vertices[i][1]
        return abs(a) / 2.0


@dataclass
class SnowLayer:
    """Оценка снежного покрова в ячейке сетки."""
    x: float                # м
    y: float                # м
    depth_mm: float         # мм — толщина снега
    surface: str = "snow"   # "snow", "ice", "clear"


class PerceptionModule:
    """
    Анализ данных камеры и LiDAR.

    В реальной реализации подключается к ROS/MAVLink-потоку изображений
    и облакам точек. Здесь — каркас логики с заглушками для алгоритмов CV.
    """

    def __init__(self) -> None:
        self._roof: RoofBoundary = RoofBoundary()
        self._obstacles: list[Obstacle] = []
        self._snow_grid: list[SnowLayer] = []
        self._grid_resolution_m: float = 0.5  # Размер ячейки сетки (м)

    @property
    def roof(self) -> RoofBoundary:
        return self._roof

    @property
    def obstacles(self) -> list[Obstacle]:
        return self._obstacles

    @property
    def snow_grid(self) -> list[SnowLayer]:
        return self._snow_grid

    def process_survey_frame(
        self,
        image: Any,
        lidar_points: list[tuple[float, float, float]] | None = None,
        drone_position: tuple[float, float, float] | None = None,
    ) -> None:
        """
        Обработать один кадр камеры + облако точек LiDAR.

        Вызывается с частотой PERCEPTION_RATE_HZ во время фазы SURVEY.
        В реальной реализации здесь:
        - Edge detection для границ крыши
        - Object detection для труб/антенн
        - Расчёт высоты снежного покрова по разнице LiDAR-данных
        """
        # Заглушка: в реальной реализации — CV-пайплайн
        logger.debug("Perception: обработка кадра, LiDAR-точек: %d",
                      len(lidar_points) if lidar_points else 0)

    def detect_roof_boundary(
        self,
        lidar_points: list[tuple[float, float, float]],
    ) -> RoofBoundary:
        """
        Определить границы крыши по облаку точек LiDAR.

        Алгоритм (каркас):
        1. Фильтрация точек по высоте (отсечь землю)
        2. Кластеризация плоских поверхностей (RANSAC)
        3. Извлечение контура основной плоскости (Convex Hull / Alpha Shape)
        4. Упрощение полигона (Ramer–Douglas–Peucker)
        """
        # Заглушка
        logger.info("Определение границ крыши по %d точкам LiDAR", len(lidar_points))
        self._roof = RoofBoundary()
        return self._roof

    def detect_obstacles(
        self,
        lidar_points: list[tuple[float, float, float]],
    ) -> list[Obstacle]:
        """
        Обнаружить препятствия (объекты, выступающие над плоскостью крыши).

        Каждое препятствие получает буферную зону OBSTACLE_BUFFER_M.
        """
        logger.info("Поиск препятствий по %d точкам LiDAR", len(lidar_points))
        self._obstacles = []
        # Заглушка: в реальной реализации — фильтрация выбросов над плоскостью
        return self._obstacles

    def estimate_snow_depth(
        self,
        lidar_points: list[tuple[float, float, float]],
        reference_surface_z: float,
    ) -> list[SnowLayer]:
        """
        Оценить толщину снежного покрова.

        Сравнивает высоту точек LiDAR с известной высотой чистой крыши
        (reference_surface_z). Разница = толщина снега.
        """
        grid: list[SnowLayer] = []
        for px, py, pz in lidar_points:
            depth_mm = max(0.0, (pz - reference_surface_z) * 1000.0)
            surface = "clear"
            if depth_mm > 10:
                surface = "snow"
            grid.append(SnowLayer(x=px, y=py, depth_mm=depth_mm, surface=surface))

        self._snow_grid = grid
        logger.info(
            "Оценка снега: %d ячеек, средняя глубина %.0f мм",
            len(grid),
            sum(c.depth_mm for c in grid) / max(1, len(grid)),
        )
        return grid

    def is_point_safe(self, x: float, y: float) -> bool:
        """
        Проверить, что точка (x, y) безопасна для движения:
        - Внутри границ крыши (с отступом MIN_ROOF_EDGE_DISTANCE_M)
        - Вне буферных зон препятствий
        """
        if self._roof.is_valid and not self._roof.contains(x, y):
            return False

        for obs in self._obstacles:
            dist = math.hypot(x - obs.x, y - obs.y)
            if dist < obs.radius + config.OBSTACLE_BUFFER_M:
                return False

        return True
