"""
Тепловизионный анализатор снега (Thermal Analyzer).

Использует тепловизионную камеру (FLIR Lepton 3.5) и AI-ускоритель
Hailo-8L для классификации снежного покрова и обнаружения скрытых
препятствий на крыше.

Возможности:
1. Классификация типа снега по тепловой сигнатуре:
   - Свежий рыхлый (POWDER):  -15…-3 °C, однородная текстура
   - Слежавшийся (SETTLED):   -8…-1 °C, плотная текстура
   - Мокрый (WET):            -3…+1 °C, неоднородная текстура
   - Наледь (ICE):            -5…0 °C, гладкая сигнатура

2. Обнаружение скрытых препятствий:
   - Трубы отопления:         ΔT ≥ 5 °C (теплее снега)
   - Вентиляционные шахты:    ΔT ≥ 10 °C
   - Края кровли:             ΔT по границе «крыша/воздух»

3. Оценка плотности снега (по тепловой инерции)

Hailo-8L: 13 TOPS, <2.5 Вт, вывод за <50 мс на кадр.
Модель: YOLOv8-seg обученная на тепловых данных зимних крыш.
"""

import logging
import math
from dataclasses import dataclass, field

from . import config
from .tilt_controller import SnowType

logger = logging.getLogger(__name__)


@dataclass
class ThermalPixel:
    """Одна точка тепловой карты."""
    x: int                   # пиксель
    y: int                   # пиксель
    temperature_c: float     # °C
    classification: str = "" # "snow", "ice", "pipe", "vent", "edge", "clear"


@dataclass
class ThermalRegion:
    """Регион с однородной тепловой сигнатурой."""
    label: str               # Тип: "powder", "settled", "wet", "ice", "obstacle"
    center_x: float          # м (в координатах крыши)
    center_y: float          # м
    area_m2: float           # м²
    mean_temp_c: float       # °C — средняя температура
    std_temp_c: float        # °C — стандартное отклонение
    snow_type: SnowType = SnowType.UNKNOWN
    confidence: float = 0.0  # 0–1


@dataclass
class HiddenObstacle:
    """Скрытое под снегом препятствие (обнаружено тепловизором)."""
    x: float                 # м
    y: float                 # м
    radius: float            # м — оценочный радиус
    obstacle_type: str       # "pipe", "vent", "electrical", "unknown"
    delta_temp_c: float      # °C — разница с окружающим снегом
    confidence: float = 0.0


@dataclass
class SnowAssessment:
    """Комплексная оценка снежного покрова."""
    dominant_type: SnowType
    mean_depth_mm: float
    mean_density_kg_m3: float
    coverage_percent: float                    # % площади покрытой снегом
    regions: list[ThermalRegion] = field(default_factory=list)
    hidden_obstacles: list[HiddenObstacle] = field(default_factory=list)
    recommended_mode: str = "BULLDOZER"        # "BULLDOZER" | "HOVER_BLOW" | "SKIP"
    recommended_tilt_deg: float = 20.0


class ThermalAnalyzer:
    """
    Анализатор тепловизионных данных с AI-ускорением (Hailo).

    Пайплайн:
    1. Захват кадра FLIR Lepton (160×120, 9 FPS)
    2. Передача на Hailo-8L для сегментации (YOLOv8-seg)
    3. Постобработка: классификация регионов по температуре
    4. Формирование SnowAssessment для Planner и TiltController
    """

    def __init__(self) -> None:
        self._hailo_ready = False
        self._last_assessment: SnowAssessment | None = None
        self._thermal_map: list[list[float]] = []
        self._frame_count: int = 0
        self._ambient_temp_c: float = -10.0

    @property
    def is_ready(self) -> bool:
        return self._hailo_ready

    @property
    def last_assessment(self) -> SnowAssessment | None:
        return self._last_assessment

    def initialize(self) -> bool:
        """
        Инициализация тепловизора и Hailo.

        В реальной реализации:
        - Открыть FLIR Lepton через SPI/I2C
        - Загрузить HEF-модель на Hailo-8L
        - Калибровка FFC (Flat Field Correction)
        """
        logger.info(
            "Инициализация: %s + %s (%d TOPS)",
            config.THERMAL_CAMERA_MODEL,
            config.HAILO_MODEL,
            config.HAILO_TOPS,
        )
        # Заглушка
        self._hailo_ready = True
        return True

    def process_frame(
        self,
        thermal_frame: list[list[float]] | None = None,
        lidar_depth_mm: float = 0.0,
        ambient_temp_c: float = -10.0,
    ) -> SnowAssessment:
        """
        Обработать один тепловой кадр.

        thermal_frame: матрица температур 160×120 (°C)
        lidar_depth_mm: глубина снега от LiDAR (для корреляции)
        ambient_temp_c: температура воздуха (от метеодатчика)
        """
        self._frame_count += 1
        self._ambient_temp_c = ambient_temp_c

        if thermal_frame:
            self._thermal_map = thermal_frame

        # 1. Сегментация через Hailo (заглушка)
        regions = self._segment_thermal_map(thermal_frame)

        # 2. Классификация каждого региона
        for region in regions:
            region.snow_type = self._classify_region(region)

        # 3. Поиск скрытых препятствий
        hidden = self._detect_hidden_obstacles(thermal_frame, ambient_temp_c)

        # 4. Определение доминантного типа снега
        dominant = self._get_dominant_type(regions)

        # 5. Оценка плотности
        density = self._estimate_density(dominant, ambient_temp_c)

        # 6. Рекомендация режима
        mode, tilt = self._recommend_strategy(dominant, lidar_depth_mm, density)

        assessment = SnowAssessment(
            dominant_type=dominant,
            mean_depth_mm=lidar_depth_mm,
            mean_density_kg_m3=density,
            coverage_percent=self._estimate_coverage(regions),
            regions=regions,
            hidden_obstacles=hidden,
            recommended_mode=mode,
            recommended_tilt_deg=tilt,
        )

        self._last_assessment = assessment

        if self._frame_count % 50 == 0:
            logger.info(
                "Thermal #%d: тип=%s, глубина=%.0fмм, плотность=%.0f кг/м³, "
                "режим=%s, скрытых препятствий=%d",
                self._frame_count, dominant.value, lidar_depth_mm,
                density, mode, len(hidden),
            )

        return assessment

    def _segment_thermal_map(
        self, thermal_frame: list[list[float]] | None,
    ) -> list[ThermalRegion]:
        """
        Сегментация тепловой карты (через Hailo YOLOv8-seg).

        В реальной реализации:
        1. Нормализация кадра → тензор 160×120×1
        2. Инференс Hailo-8L → маски сегментации
        3. Постобработка масок → ThermalRegion[]
        """
        # Заглушка: возвращаем один регион на весь кадр
        if not thermal_frame:
            return [ThermalRegion(
                label="full_frame",
                center_x=0, center_y=0,
                area_m2=100.0,
                mean_temp_c=self._ambient_temp_c + 2,
                std_temp_c=1.5,
                confidence=0.8,
            )]

        # Реальная логика: кластеризация по температуре
        all_temps = [t for row in thermal_frame for t in row]
        if not all_temps:
            return []

        mean_t = sum(all_temps) / len(all_temps)
        std_t = math.sqrt(sum((t - mean_t) ** 2 for t in all_temps) / len(all_temps))

        return [ThermalRegion(
            label="main",
            center_x=0, center_y=0,
            area_m2=100.0,
            mean_temp_c=mean_t,
            std_temp_c=std_t,
            confidence=0.85,
        )]

    def _classify_region(self, region: ThermalRegion) -> SnowType:
        """
        Классификация региона по тепловой сигнатуре.

        Использует диапазоны температур из config:
        - POWDER:  -15…-3 °C, низкое std (однородный)
        - WET:     -3…+1 °C, высокое std (неоднородный)
        - ICE:     -5…0 °C, очень низкое std (гладкий)
        - SETTLED: промежуточный
        """
        t = region.mean_temp_c
        std = region.std_temp_c

        fresh_lo, fresh_hi = config.THERMAL_SNOW_TEMP_FRESH_C
        wet_lo, wet_hi = config.THERMAL_SNOW_TEMP_WET_C

        if fresh_lo <= t <= fresh_hi and std < 2.0:
            region.confidence = 0.9
            return SnowType.POWDER

        if wet_lo <= t <= wet_hi:
            if std < 0.5:
                region.confidence = 0.85
                return SnowType.ICE
            region.confidence = 0.8
            return SnowType.WET

        if fresh_hi < t < wet_hi and std < 3.0:  # pragma: no cover
            # Unreachable while THERMAL_SNOW_TEMP_FRESH_C[1] == THERMAL_SNOW_TEMP_WET_C[0]:
            # every t in (fresh_hi, wet_hi) already satisfies the wet_lo<=t<=wet_hi
            # check above, which returns first. Kept for when the config ranges diverge.
            region.confidence = 0.7
            return SnowType.SETTLED

        region.confidence = 0.5
        return SnowType.UNKNOWN

    def _detect_hidden_obstacles(
        self,
        thermal_frame: list[list[float]] | None,
        ambient_temp: float,
    ) -> list[HiddenObstacle]:
        """
        Обнаружить скрытые под снегом объекты по тепловой аномалии.

        Трубы отопления: ΔT ≥ 5°C теплее окружающего снега
        Вентиляция:      ΔT ≥ 10°C
        """
        if not thermal_frame:
            return []

        obstacles: list[HiddenObstacle] = []
        rows = len(thermal_frame)
        cols = len(thermal_frame[0]) if rows > 0 else 0

        if rows == 0 or cols == 0:
            return []

        # Средняя температура кадра
        all_temps = [t for row in thermal_frame for t in row]
        mean_temp = sum(all_temps) / len(all_temps)

        # Поиск горячих точек
        for r in range(0, rows, 4):  # Шаг 4 пикселя для скорости
            for c in range(0, cols, 4):
                t = thermal_frame[r][c]
                delta = t - mean_temp

                if delta >= config.THERMAL_VENT_DELTA_C:
                    obstacles.append(HiddenObstacle(
                        x=c / cols * 10.0,  # Нормализация в метры (условно)
                        y=r / rows * 10.0,
                        radius=0.5,
                        obstacle_type="vent",
                        delta_temp_c=delta,
                        confidence=min(1.0, delta / 15.0),
                    ))
                elif delta >= config.THERMAL_PIPE_DELTA_C:
                    obstacles.append(HiddenObstacle(
                        x=c / cols * 10.0,
                        y=r / rows * 10.0,
                        radius=0.3,
                        obstacle_type="pipe",
                        delta_temp_c=delta,
                        confidence=min(1.0, delta / 10.0),
                    ))

        if obstacles:
            logger.info(
                "Обнаружено %d скрытых препятствий (трубы: %d, вентиляция: %d)",
                len(obstacles),
                sum(1 for o in obstacles if o.obstacle_type == "pipe"),
                sum(1 for o in obstacles if o.obstacle_type == "vent"),
            )

        return obstacles

    def _get_dominant_type(self, regions: list[ThermalRegion]) -> SnowType:
        """Определить преобладающий тип снега по площади."""
        if not regions:
            return SnowType.UNKNOWN

        type_area: dict[SnowType, float] = {}
        for r in regions:
            type_area[r.snow_type] = type_area.get(r.snow_type, 0) + r.area_m2

        return max(type_area, key=lambda snow_type: type_area[snow_type])

    @staticmethod
    def _estimate_density(snow_type: SnowType, ambient_temp: float) -> float:
        """Оценка плотности снега (кг/м³) по типу и температуре."""
        base = {
            SnowType.POWDER: 100.0,
            SnowType.SETTLED: 350.0,
            SnowType.WET: 500.0,
            SnowType.ICE: 800.0,
            SnowType.UNKNOWN: 300.0,
        }
        density = base.get(snow_type, 300.0)

        # Коррекция: чем ближе к 0°C, тем плотнее
        if ambient_temp > -3:
            density *= 1.2
        elif ambient_temp < -15:
            density *= 0.8

        return density

    @staticmethod
    def _estimate_coverage(regions: list[ThermalRegion]) -> float:
        """Оценка процента покрытия снегом."""
        total_area = sum(r.area_m2 for r in regions)
        snow_area = sum(
            r.area_m2 for r in regions
            if r.snow_type != SnowType.UNKNOWN
        )
        if total_area <= 0:
            return 0.0
        return (snow_area / total_area) * 100.0

    @staticmethod
    def _recommend_strategy(
        snow_type: SnowType,
        depth_mm: float,
        density: float,
    ) -> tuple[str, float]:
        """
        Рекомендация стратегии очистки.

        Возвращает (режим, угол_наклона).
        """
        # Свежий рыхлый снег ≤ 150 мм → HOVER_BLOW (обдув без контакта)
        if snow_type == SnowType.POWDER and depth_mm <= config.HOVER_BLOW_MAX_SNOW_DEPTH_MM:
            return "HOVER_BLOW", 0.0

        # Свежий рыхлый снег > 150 мм → BULLDOZER с малым углом
        if snow_type == SnowType.POWDER:
            return "BULLDOZER", 15.0

        # Слежавшийся → BULLDOZER с большим углом
        if snow_type == SnowType.SETTLED:
            return "BULLDOZER", 23.0

        # Мокрый → BULLDOZER на максимуме + импульсный режим
        if snow_type == SnowType.WET:
            if depth_mm > 200:
                return "SKIP", 0.0  # Не справимся
            return "BULLDOZER", 28.0

        # Лёд → пропустить (дрон не справится)
        if snow_type == SnowType.ICE:
            return "SKIP", 0.0

        return "BULLDOZER", 20.0
