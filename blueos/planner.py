"""
Планировщик дорожек (Planner Module) — генерация оптимальных маршрутов
очистки крыши по boustrophedon-паттерну (зигзаг).

Учитывает:
- Ширину захвата отвала (1200 мм)
- Перекрытие дорожек (LANE_OVERLAP_MM)
- Направление к ближайшему краю крыши
- Зоны исключения вокруг препятствий
"""

import logging
import math
from dataclasses import dataclass, field

from . import config
from .perception import PerceptionModule, RoofBoundary, Obstacle

logger = logging.getLogger(__name__)


@dataclass
class Waypoint:
    """Точка маршрута."""
    x: float                # м
    y: float                # м
    heading: float = 0.0    # рад — курс дрона
    speed: float = 0.5      # м/с — скорость движения
    is_bulldozer: bool = True  # Режим толкания (True) или перелёт (False)


@dataclass
class Lane:
    """Одна дорожка (полоса) очистки."""
    waypoints: list[Waypoint] = field(default_factory=list)
    direction_deg: float = 0.0   # градусов — направление толкания снега
    length_m: float = 0.0

    @property
    def is_valid(self) -> bool:
        return len(self.waypoints) >= 2


@dataclass
class CleaningPlan:
    """Полный план очистки крыши."""
    lanes: list[Lane] = field(default_factory=list)
    total_area_m2: float = 0.0
    estimated_cycles: int = 0       # Количество циклов BURST
    estimated_total_time_min: float = 0.0

    @property
    def lane_count(self) -> int:
        return len(self.lanes)


class PlannerModule:
    """
    Планировщик маршрутов очистки.

    Строит boustrophedon-покрытие (зигзагообразные полосы)
    от центра крыши к ближайшему краю.
    """

    def __init__(self, perception: PerceptionModule) -> None:
        self._perception = perception
        self._plan: CleaningPlan | None = None

    @property
    def plan(self) -> CleaningPlan | None:
        return self._plan

    def generate_plan(self) -> CleaningPlan:
        """
        Сгенерировать план очистки крыши.

        Алгоритм:
        1. Получить границы крыши и препятствия из PerceptionModule
        2. Определить оптимальное направление толкания (к ближайшему краю)
        3. Разбить область на полосы шириной (BLADE_WIDTH - LANE_OVERLAP)
        4. Для каждой полосы — создать маршрут от дальнего края к ближнему
        5. Упорядочить полосы в boustrophedon-порядке (зигзаг)
        """
        roof = self._perception.roof
        obstacles = self._perception.obstacles

        if not roof.is_valid:
            logger.error("Невозможно построить план: границы крыши не определены")
            self._plan = CleaningPlan()
            return self._plan

        # Ширина эффективной полосы (с учётом перекрытия)
        lane_width_m = (config.BLADE_WIDTH_MM - config.LANE_OVERLAP_MM) / 1000.0

        # Определение направления толкания
        push_direction = self._find_best_push_direction(roof)

        # Генерация полос
        lanes = self._generate_boustrophedon_lanes(
            roof=roof,
            obstacles=obstacles,
            lane_width=lane_width_m,
            push_direction=push_direction,
        )

        # Оценка ресурсов
        total_length = sum(lane.length_m for lane in lanes)
        push_speed = 0.5  # м/с — средняя скорость толкания
        burst_time = config.BURST_MAX_DURATION_SEC
        charge_time = config.CHARGE_TIME_SEC

        # Сколько метров за один BURST?
        meters_per_burst = push_speed * burst_time
        estimated_cycles = max(1, math.ceil(total_length / meters_per_burst))
        estimated_total_time = estimated_cycles * (burst_time + charge_time)

        self._plan = CleaningPlan(
            lanes=lanes,
            total_area_m2=roof.area(),
            estimated_cycles=estimated_cycles,
            estimated_total_time_min=estimated_total_time / 60.0,
        )

        logger.info(
            "План очистки: %d дорожек, %d циклов, ~%.0f мин",
            len(lanes), estimated_cycles, self._plan.estimated_total_time_min,
        )
        return self._plan

    def get_next_lane(self, completed_lanes: int) -> Lane | None:
        """Получить следующую дорожку для выполнения."""
        if self._plan is None or completed_lanes >= len(self._plan.lanes):
            return None
        return self._plan.lanes[completed_lanes]

    def _find_best_push_direction(self, roof: RoofBoundary) -> float:
        """
        Определить оптимальное направление толкания снега.

        Стратегия: толкать к ближайшему краю крыши (минимальное расстояние).
        Возвращает угол в радианах.
        """
        if not roof.vertices:
            return 0.0

        # Центр крыши
        cx = sum(v[0] for v in roof.vertices) / len(roof.vertices)
        cy = sum(v[1] for v in roof.vertices) / len(roof.vertices)

        # Ближайшая точка на краю
        min_dist = float("inf")
        best_angle = 0.0

        for i in range(len(roof.vertices)):
            j = (i + 1) % len(roof.vertices)
            # Середина ребра
            mx = (roof.vertices[i][0] + roof.vertices[j][0]) / 2.0
            my = (roof.vertices[i][1] + roof.vertices[j][1]) / 2.0
            dist = math.hypot(mx - cx, my - cy)
            if dist < min_dist:
                min_dist = dist
                best_angle = math.atan2(my - cy, mx - cx)

        logger.info(
            "Направление толкания: %.1f° (к ближайшему краю на %.1f м)",
            math.degrees(best_angle), min_dist,
        )
        return best_angle

    def _generate_boustrophedon_lanes(
        self,
        roof: RoofBoundary,
        obstacles: list[Obstacle],
        lane_width: float,
        push_direction: float,
    ) -> list[Lane]:
        """
        Генерация полос в boustrophedon-порядке.

        Полосы идут перпендикулярно направлению толкания,
        чередуя направление (зигзаг).
        """
        if not roof.vertices:
            return []

        # Определяем bounding box крыши
        xs = [v[0] for v in roof.vertices]
        ys = [v[1] for v in roof.vertices]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        # Перпендикулярное направление
        perp_angle = push_direction + math.pi / 2

        # Ширина крыши вдоль перпендикуляра
        width = max(x_max - x_min, y_max - y_min)
        num_lanes = max(1, math.ceil(width / lane_width))

        lanes: list[Lane] = []

        for i in range(num_lanes):
            offset = (i - num_lanes / 2) * lane_width

            # Начальная и конечная точки полосы
            cos_push = math.cos(push_direction)
            sin_push = math.sin(push_direction)
            cos_perp = math.cos(perp_angle)
            sin_perp = math.sin(perp_angle)

            cx = (x_min + x_max) / 2 + offset * cos_perp
            cy = (y_min + y_max) / 2 + offset * sin_perp

            # Длина полосы (от дальней стороны к ближнему краю)
            lane_length = width * 0.8  # Приблизительно

            # Старт (дальняя точка от края)
            start_x = cx - (lane_length / 2) * cos_push
            start_y = cy - (lane_length / 2) * sin_push

            # Конец (ближе к краю сброса)
            end_x = cx + (lane_length / 2) * cos_push
            end_y = cy + (lane_length / 2) * sin_push

            # Чередование направления (бустрофедон)
            if i % 2 == 1:
                start_x, end_x = end_x, start_x
                start_y, end_y = end_y, start_y

            # Проверка безопасности точек
            if (self._perception.is_point_safe(start_x, start_y) and
                    self._perception.is_point_safe(end_x, end_y)):
                lane = Lane(
                    waypoints=[
                        Waypoint(x=start_x, y=start_y,
                                 heading=push_direction,
                                 is_bulldozer=True),
                        Waypoint(x=end_x, y=end_y,
                                 heading=push_direction,
                                 is_bulldozer=True),
                    ],
                    direction_deg=math.degrees(push_direction),
                    length_m=lane_length,
                )
                lanes.append(lane)

        logger.info("Сгенерировано %d дорожек (ширина полосы %.2f м)", len(lanes), lane_width)
        return lanes
