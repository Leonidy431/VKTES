"""
Менеджер энергии (Power Manager) — управление циклами заряд/разряд
суперконденсаторной наземной станции.

Функции:
- Мониторинг напряжения банка ионисторов в реальном времени
- Оценка оставшейся энергии и времени работы
- Автоматический переход между фазами (Charge → Burst → Low Power)
- Аварийное прерывание при просадке ниже порога
"""

import enum
import logging
import time

from . import config

logger = logging.getLogger(__name__)


class PowerState(enum.Enum):
    """Состояние энергосистемы."""
    CHARGING = "CHARGING"          # Зарядка ионисторов
    READY = "READY"                # Готов к циклу BURST
    DISCHARGING = "DISCHARGING"    # Разряд (активная работа)
    LOW_POWER = "LOW_POWER"        # Низкий заряд — только зависание
    CRITICAL = "CRITICAL"          # Аварийный уровень


class PowerManager:
    """
    Менеджер энергии наземной станции и бортовой шины.

    Принимает телеметрию от наземной станции через MAVLink
    и управляет переходами между фазами.
    """

    def __init__(self) -> None:
        self._state = PowerState.CHARGING
        self._voltage: float = 0.0             # В — текущее напряжение банка
        self._current: float = 0.0             # А — текущий ток
        self._temperature: float = 20.0        # °C — температура банка
        self._energy_kj: float = 0.0           # кДж — оставшаяся энергия
        self._charge_start_time: float = 0.0
        self._discharge_start_time: float = 0.0

    @property
    def state(self) -> PowerState:
        return self._state

    @property
    def voltage(self) -> float:
        return self._voltage

    @property
    def energy_kj(self) -> float:
        return self._energy_kj

    @property
    def energy_percent(self) -> float:
        """Процент оставшейся энергии (0–100)."""
        if config.SUPERCAP_MAX_ENERGY_KJ <= 0:
            return 0.0
        return min(100.0, (self._energy_kj / config.SUPERCAP_MAX_ENERGY_KJ) * 100.0)

    @property
    def estimated_burst_time_sec(self) -> float:
        """Оценка оставшегося времени работы в режиме BURST (с)."""
        if config.MAX_THRUST_KW <= 0:
            return 0.0
        return self._energy_kj / config.MAX_THRUST_KW  # кДж / кВт = с

    @property
    def is_burst_ready(self) -> bool:
        """Готов ли банк к циклу BURST."""
        return self._voltage >= config.VOLTAGE_BURST_READY

    def update_telemetry(
        self,
        voltage: float,
        current: float,
        temperature: float,
    ) -> PowerState:
        """
        Обновить данные телеметрии от наземной станции.

        Вызывается с частотой TELEMETRY_RATE_HZ.
        Возвращает текущее состояние энергосистемы.
        """
        self._voltage = voltage
        self._current = current
        self._temperature = temperature

        # Расчёт запасённой энергии: E = ½CV²
        self._energy_kj = (
            0.5 * config.SUPERCAP_CAPACITANCE * voltage * voltage
        ) / 1000.0  # Дж → кДж

        new_state = self._evaluate_state()
        if new_state != self._state:
            logger.info(
                "Энергосистема: %s → %s (V=%.1fВ, E=%.0fкДж)",
                self._state.value, new_state.value, voltage, self._energy_kj,
            )
            self._state = new_state

        return self._state

    def start_charge(self) -> None:
        """Начать цикл зарядки."""
        self._charge_start_time = time.monotonic()
        self._state = PowerState.CHARGING
        logger.info("Зарядка начата. V=%.1fВ", self._voltage)

    def start_discharge(self) -> None:
        """Начать цикл разряда (BURST)."""
        if not self.is_burst_ready:
            logger.warning(
                "Попытка BURST при V=%.1fВ < порога %.1fВ",
                self._voltage, config.VOLTAGE_BURST_READY,
            )
            return
        self._discharge_start_time = time.monotonic()
        self._state = PowerState.DISCHARGING
        logger.info("BURST начат. V=%.1fВ, E=%.0fкДж", self._voltage, self._energy_kj)

    @property
    def discharge_elapsed_sec(self) -> float:
        """Время (с) с начала текущего цикла разряда."""
        if self._discharge_start_time == 0:
            return 0.0
        return time.monotonic() - self._discharge_start_time

    @property
    def charge_elapsed_sec(self) -> float:
        """Время (с) с начала текущего цикла зарядки."""
        if self._charge_start_time == 0:
            return 0.0
        return time.monotonic() - self._charge_start_time

    def _evaluate_state(self) -> PowerState:
        """Определить состояние по текущему напряжению."""
        if self._voltage <= config.VOLTAGE_EMERGENCY:
            return PowerState.CRITICAL
        if self._voltage <= config.VOLTAGE_LOW_POWER:
            return PowerState.LOW_POWER
        if self._state == PowerState.DISCHARGING:
            return PowerState.DISCHARGING
        if self._voltage >= config.VOLTAGE_BURST_READY:
            return PowerState.READY
        return PowerState.CHARGING
