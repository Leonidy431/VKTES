"""
Импульсный режим тяги (Burst Vibrator).

Генерирует периодические импульсы мощности для:
1. Вибрационного разрушения слежавшегося снега перед отвалом
2. Снижения эффекта «пропеллерной метели» (whiteout)
3. Экономии энергии (средняя мощность ниже пиковой)

Принцип:
    Вместо постоянной тяги 95% — чередование импульсов:
    ON  (0.8 с, 100%) → OFF (0.3 с, 40%)

    Импульс создаёт ударную нагрузку на снежный пласт,
    разрушая его структуру. Пауза позволяет снежной пыли
    осесть, улучшая видимость камеры и LiDAR.

Режимы:
    CONTINUOUS — постоянная тяга (свежий рыхлый снег)
    PULSED     — импульсы ON/OFF (слежавшийся снег)
    HAMMER     — короткие мощные удары (наледь, корка)
    RAMP       — плавное нарастание (старт движения)
"""

import enum
import logging
import time

from . import config

logger = logging.getLogger(__name__)


class BurstMode(enum.Enum):
    CONTINUOUS = "CONTINUOUS"
    PULSED = "PULSED"
    HAMMER = "HAMMER"
    RAMP = "RAMP"


class BurstVibrator:
    """
    Генератор импульсного режима тяги.

    Выдаёт текущее значение throttle (0–100%) на каждом тике,
    формируя нужный паттерн импульсов.
    """

    def __init__(self) -> None:
        self._mode = BurstMode.CONTINUOUS
        self._enabled = config.BURST_VIBRATION_ENABLED
        self._cycle_start: float = 0.0
        self._pulse_count: int = 0
        self._total_pulses: int = 0
        self._ramp_progress: float = 0.0

        # Параметры текущего режима
        self._on_duration: float = config.BURST_PULSE_ON_SEC
        self._off_duration: float = config.BURST_PULSE_OFF_SEC
        self._throttle_high: float = config.BURST_PULSE_THROTTLE_HIGH
        self._throttle_low: float = config.BURST_PULSE_THROTTLE_LOW

    @property
    def mode(self) -> BurstMode:
        return self._mode

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def pulse_count(self) -> int:
        return self._total_pulses

    def set_mode(self, mode: BurstMode) -> None:
        """Установить режим импульсов."""
        if mode == self._mode:
            return

        self._mode = mode
        self._cycle_start = time.monotonic()
        self._pulse_count = 0

        if mode == BurstMode.CONTINUOUS:
            self._on_duration = 999.0
            self._off_duration = 0.0
            self._throttle_high = 95.0
            self._throttle_low = 95.0

        elif mode == BurstMode.PULSED:
            self._on_duration = config.BURST_PULSE_ON_SEC
            self._off_duration = config.BURST_PULSE_OFF_SEC
            self._throttle_high = config.BURST_PULSE_THROTTLE_HIGH
            self._throttle_low = config.BURST_PULSE_THROTTLE_LOW

        elif mode == BurstMode.HAMMER:
            # Короткие мощные удары с длинными паузами
            self._on_duration = 0.3
            self._off_duration = 0.7
            self._throttle_high = 100.0
            self._throttle_low = 30.0

        elif mode == BurstMode.RAMP:
            self._ramp_progress = 0.0
            self._throttle_high = 100.0
            self._throttle_low = 20.0

        logger.info(
            "Burst режим: %s (ON=%.1fс/%.0f%%, OFF=%.1fс/%.0f%%)",
            mode.value,
            self._on_duration, self._throttle_high,
            self._off_duration, self._throttle_low,
        )

    def get_throttle(self) -> float:
        """
        Получить текущее значение throttle (0–100%).

        Вызывается каждый тик главного цикла.
        """
        if not self._enabled or self._mode == BurstMode.CONTINUOUS:
            return self._throttle_high

        now = time.monotonic()

        if self._mode == BurstMode.RAMP:
            return self._compute_ramp(now)

        # Фазовое время внутри цикла ON+OFF
        cycle_duration = self._on_duration + self._off_duration
        elapsed = (now - self._cycle_start) % cycle_duration

        if elapsed < self._on_duration:
            # Фаза ON — проверяем начало нового импульса
            if elapsed < 0.05 and (now - self._cycle_start) > 0.1:
                self._pulse_count += 1
                self._total_pulses += 1
            return self._throttle_high
        else:
            # Фаза OFF — пониженная тяга (удержание на лыжах)
            return self._throttle_low

    def get_visibility_window(self) -> bool:
        """
        Сейчас «окно видимости»?

        В фазе OFF пропеллерный поток ослабевает, снежная пыль
        оседает — камера и LiDAR могут получить чистый кадр.
        """
        if not self._enabled or self._mode == BurstMode.CONTINUOUS:
            return False

        now = time.monotonic()
        cycle_duration = self._on_duration + self._off_duration
        elapsed = (now - self._cycle_start) % cycle_duration

        # Окно видимости — вторая половина фазы OFF
        off_start = self._on_duration
        off_mid = off_start + self._off_duration * 0.5
        return elapsed > off_mid

    def reset(self) -> None:
        """Сбросить счётчики."""
        self._cycle_start = time.monotonic()
        self._pulse_count = 0
        self._total_pulses = 0
        self._ramp_progress = 0.0

    def _compute_ramp(self, now: float) -> float:
        """Плавное нарастание мощности (режим RAMP)."""
        elapsed = now - self._cycle_start
        ramp_duration = 3.0  # с — время выхода на полную мощность

        self._ramp_progress = min(1.0, elapsed / ramp_duration)
        throttle = (
            self._throttle_low
            + (self._throttle_high - self._throttle_low) * self._ramp_progress
        )

        if self._ramp_progress >= 1.0 and self._mode == BurstMode.RAMP:
            logger.info("RAMP завершён → переключение на PULSED")
            self.set_mode(BurstMode.PULSED)

        return throttle

    def recommend_mode(self, snow_type_str: str, depth_mm: float) -> BurstMode:
        """
        Рекомендовать режим импульсов по типу снега.

        Вызывается планировщиком или тепловизором.
        """
        if snow_type_str == "POWDER" and depth_mm < 150:
            return BurstMode.CONTINUOUS
        elif snow_type_str == "POWDER":
            return BurstMode.PULSED
        elif snow_type_str == "SETTLED":
            return BurstMode.PULSED
        elif snow_type_str in ("WET", "ICE"):
            return BurstMode.HAMMER
        else:
            return BurstMode.PULSED
