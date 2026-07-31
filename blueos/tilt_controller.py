"""
Контроллер угла наклона рамы (Tilt Controller).

Управляет углом наклона рамы дрона для создания горизонтальной
составляющей тяги при работе в режиме BULLDOZER.

Физика:
    При суммарной статической тяге T = 50 кгс и наклоне α:
    - Горизонтальная сила: Fh = T × sin(α)
    - Вертикальная сила:   Fv = T × cos(α)

    α = 15° → Fh = 12.9 кгс (только свежий снег ≤20 см)
    α = 20° → Fh = 17.1 кгс (свежий снег 30 см)
    α = 25° → Fh = 21.1 кгс (лёгкий слежавшийся)
    α = 30° → Fh = 25.0 кгс (предел — взлёт затруднён)

Адаптация:
    Контроллер динамически подбирает угол наклона на основе:
    1. Типа снега (от тепловизора / Hailo)
    2. Текущего сопротивления (по скорости и потреблению тока)
    3. Доступной мощности (от PowerManager)
"""

import enum
import logging
import math

from . import config

logger = logging.getLogger(__name__)


class SnowType(enum.Enum):
    POWDER = "POWDER"        # Рыхлый, 200 кг/м³
    SETTLED = "SETTLED"      # Слежавшийся, 350 кг/м³
    WET = "WET"              # Мокрый, 500+ кг/м³
    ICE = "ICE"              # Наледь
    UNKNOWN = "UNKNOWN"


class TiltController:
    """
    Динамическое управление углом наклона рамы.

    Подбирает оптимальный угол для текущих условий:
    - Свежий снег → 15–20° (хватает тяги, экономим энергию)
    - Слежавшийся → 25° (нужна максимальная горизонтальная тяга)
    - Мокрый/лёд → 30° + импульсный режим (или отказ от BULLDOZER)
    """

    def __init__(self) -> None:
        self._current_angle_deg: float = config.TILT_MIN_DEG
        self._target_angle_deg: float = config.TILT_MIN_DEG
        self._snow_type: SnowType = SnowType.UNKNOWN
        self._resistance_kgf: float = 0.0
        self._ground_speed_ms: float = 0.0

    @property
    def current_angle(self) -> float:
        return self._current_angle_deg

    @property
    def target_angle(self) -> float:
        return self._target_angle_deg

    @property
    def horizontal_thrust_kgf(self) -> float:
        """Текущая горизонтальная составляющая тяги (кгс)."""
        rad = math.radians(self._current_angle_deg)
        return config.MAX_STATIC_THRUST_KGF * math.sin(rad)

    @property
    def vertical_thrust_kgf(self) -> float:
        """Текущая вертикальная составляющая тяги (кгс)."""
        rad = math.radians(self._current_angle_deg)
        return config.MAX_STATIC_THRUST_KGF * math.cos(rad)

    @property
    def can_takeoff(self) -> bool:
        """Достаточно ли вертикальной тяги для взлёта (>2:1)."""
        return self.vertical_thrust_kgf > config.DRONE_WEIGHT_KG * 2.0

    def set_target(self, angle_deg: float) -> None:
        """Установить целевой угол наклона (кламп к [TILT_MIN_DEG, TILT_MAX_DEG])."""
        self._target_angle_deg = max(
            config.TILT_MIN_DEG, min(config.TILT_MAX_DEG, angle_deg)
        )

    def get_thrust_components(self, angle_deg: float) -> tuple[float, float]:
        """Горизонтальная и вертикальная составляющие тяги при заданном угле (кгс)."""
        rad = math.radians(max(config.TILT_MIN_DEG, min(config.TILT_MAX_DEG, angle_deg)))
        return (
            config.MAX_STATIC_THRUST_KGF * math.sin(rad),
            config.MAX_STATIC_THRUST_KGF * math.cos(rad),
        )

    def set_snow_type(self, snow_type: SnowType) -> None:
        """Установить тип снега (от тепловизора / Hailo)."""
        self._snow_type = snow_type
        self._target_angle_deg = self._angle_for_snow_type(snow_type)
        logger.info(
            "Тип снега: %s → целевой угол %.0f° (Fh=%.1f кгс)",
            snow_type.value,
            self._target_angle_deg,
            config.MAX_STATIC_THRUST_KGF * math.sin(math.radians(self._target_angle_deg)),
        )

    def compute_optimal_angle(
        self,
        snow_type: SnowType,
        snow_depth_mm: float,
        ground_speed_ms: float,
        motor_current_a: float,
    ) -> float:
        """
        Рассчитать оптимальный угол наклона с учётом всех факторов.

        Алгоритм:
        1. Базовый угол по типу снега
        2. Коррекция по глубине
        3. Адаптация по скорости (если дрон замедляется — увеличить угол)
        4. Ограничение по безопасности взлёта
        """
        self._ground_speed_ms = ground_speed_ms
        self._snow_type = snow_type

        # 1. Базовый угол по типу снега
        base_angle = self._angle_for_snow_type(snow_type)

        # 2. Коррекция по глубине (больше снега — больше угол)
        depth_factor = min(1.5, snow_depth_mm / 200.0)
        angle = base_angle * depth_factor

        # 3. Адаптация по скорости: если дрон почти стоит — нужно больше тяги
        if ground_speed_ms < 0.1 and motor_current_a > 10.0:
            stall_boost = min(5.0, (10.0 - ground_speed_ms * 100) * 0.5)
            angle += stall_boost
            logger.warning(
                "Стагнация: скорость %.2f м/с, ток %.0f А → угол +%.1f°",
                ground_speed_ms, motor_current_a, stall_boost,
            )

        # 4. Ограничение
        angle = max(config.TILT_MIN_DEG, min(config.TILT_MAX_DEG, angle))

        # 5. Проверка безопасности взлёта при новом угле
        vertical = config.MAX_STATIC_THRUST_KGF * math.cos(math.radians(angle))
        if vertical < config.DRONE_WEIGHT_KG * 1.5:  # pragma: no cover
            # Unreachable with current config: angle is already clamped to
            # TILT_MAX_DEG (30°) above, and MAX_STATIC_THRUST_KGF*cos(30°)
            # (~43.3 kgf) exceeds DRONE_WEIGHT_KG*1.5 (~27.75 kgf) by a wide
            # margin. Kept as a defensive guard for future airframe/config changes.
            angle = config.TILT_MAX_DEG  # Уже на пределе
            logger.warning(
                "Вертикальная тяга на пределе: %.1f кгс при %.0f°",
                vertical, angle,
            )

        self._target_angle_deg = angle
        return angle

    def update(self, dt: float) -> float:
        """
        Обновить текущий угол наклона (плавное изменение).

        Вызывается каждый цикл главного цикла (dt = 1/TELEMETRY_RATE_HZ).
        Возвращает текущий угол.
        """
        if abs(self._current_angle_deg - self._target_angle_deg) < 0.1:
            self._current_angle_deg = self._target_angle_deg
            return self._current_angle_deg

        step = config.TILT_STEP_DEG_PER_SEC * dt
        if self._current_angle_deg < self._target_angle_deg:
            self._current_angle_deg = min(
                self._target_angle_deg,
                self._current_angle_deg + step,
            )
        else:
            self._current_angle_deg = max(
                self._target_angle_deg,
                self._current_angle_deg - step,
            )

        return self._current_angle_deg

    def prepare_for_flight(self) -> float:
        """Вернуть наклон в 0° для безопасного взлёта."""
        self._target_angle_deg = config.TILT_MIN_DEG
        logger.info("Подготовка к взлёту: наклон → 0°")
        return self._target_angle_deg

    def estimate_resistance(self, snow_type: SnowType, depth_mm: float) -> float:
        """
        Оценка сопротивления снега (кгс) для текущего отвала.

        Использует ширину отвала и удельное сопротивление для данного типа снега.
        """
        blade_width_m = config.BLADE_WIDTH_MM / 1000.0
        depth_factor = depth_mm / 300.0  # нормализация к 30 см

        resistance_per_m = {
            SnowType.POWDER: config.SNOW_RESISTANCE_POWDER_KGF_M,
            SnowType.SETTLED: config.SNOW_RESISTANCE_SETTLED_KGF_M,
            SnowType.WET: config.SNOW_RESISTANCE_WET_KGF_M,
            SnowType.ICE: config.SNOW_RESISTANCE_WET_KGF_M * 1.5,
            SnowType.UNKNOWN: config.SNOW_RESISTANCE_SETTLED_KGF_M,
        }

        r = resistance_per_m.get(snow_type, config.SNOW_RESISTANCE_SETTLED_KGF_M)
        self._resistance_kgf = r * blade_width_m * depth_factor
        return self._resistance_kgf

    def can_handle_snow(self, snow_type: SnowType, depth_mm: float) -> bool:
        """
        Может ли дрон справиться с данным снегом?

        Сравнивает максимальную горизонтальную тягу (при TILT_MAX_DEG)
        с расчётным сопротивлением.
        """
        resistance = self.estimate_resistance(snow_type, depth_mm)
        max_horizontal = config.MAX_STATIC_THRUST_KGF * math.sin(
            math.radians(config.TILT_MAX_DEG)
        )
        feasible = max_horizontal > resistance * 1.2  # запас 20%

        if not feasible:
            logger.warning(
                "Снег не по силам: %s, глубина %.0f мм, "
                "сопротивление %.1f кгс > макс. тяга %.1f кгс",
                snow_type.value, depth_mm, resistance, max_horizontal,
            )

        return feasible

    @staticmethod
    def _angle_for_snow_type(snow_type: SnowType) -> float:
        """Базовый угол наклона для данного типа снега."""
        angles = {
            SnowType.POWDER: 15.0,
            SnowType.SETTLED: 23.0,
            SnowType.WET: 28.0,
            SnowType.ICE: 30.0,
            SnowType.UNKNOWN: config.TILT_DEFAULT_BULLDOZER_DEG,
        }
        return angles.get(snow_type, config.TILT_DEFAULT_BULLDOZER_DEG)
