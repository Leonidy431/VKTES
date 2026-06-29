"""
Режим HOVER_BLOW — обдув снега зависанием.

Для свежего рыхлого снега (POWDER) глубиной до 150 мм не нужен
механический контакт с крышей. Достаточно зависнуть на 1–2 м
и использовать нисходящий поток 8 пропеллеров (15 кВт) как
«воздушную метлу».

Преимущества:
- Нет контакта с крышей → нет повреждения кровли
- Не нужны лыжи, отвал, Ground Steering
- Быстрее: не нужна посадка/взлёт между дорожками
- Безопаснее: дрон всегда в воздухе

Паттерн движения:
- Зависание на HOVER_BLOW_ALTITUDE_M (1.5 м)
- Медленное перемещение по сетке с шагом HOVER_BLOW_STEP_M
- Задержка HOVER_BLOW_DWELL_SEC на каждой точке
- Мощность HOVER_BLOW_THROTTLE_PCT (85%)
"""

import logging
import math
from dataclasses import dataclass, field

from . import config
from .utils import point_in_polygon, in_obstacle_zone

logger = logging.getLogger(__name__)


@dataclass
class BlowPoint:
    """Точка обдува."""
    x: float          # м
    y: float          # м
    completed: bool = False
    dwell_elapsed: float = 0.0  # с — сколько уже обдували


@dataclass
class HoverBlowPlan:
    """План обдува всей крыши."""
    points: list[BlowPoint] = field(default_factory=list)
    current_index: int = 0
    total_area_m2: float = 0.0
    estimated_time_sec: float = 0.0

    @property
    def is_complete(self) -> bool:
        return self.current_index >= len(self.points)

    @property
    def progress_percent(self) -> float:
        if not self.points:
            return 100.0
        return (self.current_index / len(self.points)) * 100.0

    @property
    def current_point(self) -> BlowPoint | None:
        if self.current_index < len(self.points):
            return self.points[self.current_index]
        return None


class HoverBlowController:
    """
    Контроллер режима HOVER_BLOW.

    Управляет перемещением дрона по сетке обдува и временем
    задержки на каждой точке.
    """

    def __init__(self) -> None:
        self._plan: HoverBlowPlan | None = None
        self._active = False
        self._throttle_pct = config.HOVER_BLOW_THROTTLE_PCT
        self._altitude_m = config.HOVER_BLOW_ALTITUDE_M

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def plan(self) -> HoverBlowPlan | None:
        return self._plan

    @property
    def throttle(self) -> float:
        return self._throttle_pct

    @property
    def altitude(self) -> float:
        return self._altitude_m

    def generate_plan(
        self,
        roof_vertices: list[tuple[float, float]],
        obstacles: list[tuple[float, float, float]] | None = None,
    ) -> HoverBlowPlan:
        """
        Сгенерировать план обдува по сетке.

        Создаёт равномерную сетку точек внутри полигона крыши
        с шагом HOVER_BLOW_STEP_M, исключая зоны препятствий.
        """
        if len(roof_vertices) < 3:
            logger.warning("HOVER_BLOW: недостаточно вершин крыши для плана")
            self._plan = HoverBlowPlan()
            return self._plan

        # Bounding box крыши
        xs = [v[0] for v in roof_vertices]
        ys = [v[1] for v in roof_vertices]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        step = config.HOVER_BLOW_STEP_M
        points: list[BlowPoint] = []

        # Генерация сетки
        y = y_min + step / 2
        row = 0
        while y <= y_max:
            x = x_min + step / 2
            row_points: list[BlowPoint] = []

            while x <= x_max:
                # Проверка: точка внутри полигона?
                if point_in_polygon(x, y, roof_vertices):
                    # Проверка: не в зоне препятствия?
                    if not in_obstacle_zone(x, y, obstacles, config.OBSTACLE_BUFFER_M):
                        row_points.append(BlowPoint(x=x, y=y))
                x += step

            # Бустрофедон: чётные ряды — слева направо, нечётные — справа налево
            if row % 2 == 1:
                row_points.reverse()

            points.extend(row_points)
            y += step
            row += 1

        # Расчёт времени
        dwell = config.HOVER_BLOW_DWELL_SEC
        transit_time = step / 1.0  # ~1 м/с скорость перемещения
        total_time = len(points) * (dwell + transit_time)

        self._plan = HoverBlowPlan(
            points=points,
            total_area_m2=(x_max - x_min) * (y_max - y_min),
            estimated_time_sec=total_time,
        )

        logger.info(
            "HOVER_BLOW план: %d точек, шаг %.1f м, ~%.0f с (%d циклов заряда)",
            len(points), step, total_time,
            max(1, math.ceil(total_time / config.BURST_MAX_DURATION_SEC)),
        )

        return self._plan

    def start(self) -> None:
        """Начать выполнение плана обдува."""
        if self._plan is None or not self._plan.points:
            logger.error("HOVER_BLOW: нет плана")
            return
        self._active = True
        logger.info("HOVER_BLOW: старт (%d точек)", len(self._plan.points))

    def stop(self) -> None:
        """Остановить обдув."""
        self._active = False
        logger.info("HOVER_BLOW: остановлен на точке %d/%d",
                     self._plan.current_index if self._plan else 0,
                     len(self._plan.points) if self._plan else 0)

    def update(self, dt: float) -> tuple[float, float, float, bool]:
        """
        Обновить состояние (вызывается каждый тик).

        Возвращает (target_x, target_y, target_z, point_complete).
        target_z отрицательный = вверх (NED).
        """
        if not self._active or self._plan is None:
            return 0, 0, -self._altitude_m, False

        point = self._plan.current_point
        if point is None:
            self._active = False
            return 0, 0, -self._altitude_m, True

        # Обновить время обдува
        point.dwell_elapsed += dt

        # Текущая точка завершена?
        point_done = point.dwell_elapsed >= config.HOVER_BLOW_DWELL_SEC

        if point_done:
            point.completed = True
            self._plan.current_index += 1

            if self._plan.is_complete:
                self._active = False
                logger.info("HOVER_BLOW: план завершён (%.0f%%)", self._plan.progress_percent)
            else:
                next_pt = self._plan.current_point
                if next_pt:
                    logger.debug(
                        "HOVER_BLOW: точка %d/%d → (%.1f, %.1f)",
                        self._plan.current_index + 1,
                        len(self._plan.points),
                        next_pt.x, next_pt.y,
                    )

        return point.x, point.y, -self._altitude_m, point_done

    def adjust_for_snow_depth(self, depth_mm: float) -> None:
        """
        Адаптировать параметры обдува под глубину снега.

        Глубже снег → ниже зависание, дольше обдув, больше мощность.
        """
        if depth_mm < 50:
            self._altitude_m = 2.0
            self._throttle_pct = 70.0
        elif depth_mm < 100:
            self._altitude_m = 1.5
            self._throttle_pct = 85.0
        else:
            self._altitude_m = 1.0
            self._throttle_pct = 95.0

        logger.info(
            "HOVER_BLOW адаптация: снег %.0f мм → высота %.1f м, газ %.0f%%",
            depth_mm, self._altitude_m, self._throttle_pct,
        )

