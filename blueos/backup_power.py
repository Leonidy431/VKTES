"""
Резервное питание (Backup Power Manager).

Управление аварийным источником питания для случая обрыва
или отключения тросового кабеля.

Схемы:
    LIPO      — LiPo 6S 5000 мА·ч (~0.8 кг, ~45 с зависания)
    SUPERCAP  — Модуль Maxwell BCAP3000 ×6 последовательно (16.2В)
    HYBRID    — LiPo + малый ионистор (LiPo для длительности,
                ионистор для мгновенного пика при переключении)

Переключение:
    Основное питание (400В → Vicor → 48В) подключено через
    ORing-диод (идеальный диод LTC4357). При падении напряжения
    на основной шине — автоматическое бесшовное переключение
    на резервный источник за <5 мс.
"""

import enum
import logging
import time

from . import config

logger = logging.getLogger(__name__)


class BackupState(enum.Enum):
    STANDBY = "STANDBY"            # Резерв заряжен и готов
    ACTIVE = "ACTIVE"              # Резерв питает дрон (обрыв кабеля)
    CHARGING = "CHARGING"          # Зарядка от основной шины
    DEPLETED = "DEPLETED"          # Разряжен
    FAULT = "FAULT"                # Неисправность


class BackupType(enum.Enum):
    LIPO = "LIPO"
    SUPERCAP = "SUPERCAP"
    HYBRID = "HYBRID"


class BackupPowerManager:
    """
    Менеджер резервного питания.

    Обеспечивает:
    - Мониторинг состояния резервного источника
    - Автоматическое переключение при обрыве кабеля
    - Обратный отсчёт оставшегося времени полёта
    - Команду на аварийную посадку при исчерпании резерва
    """

    def __init__(self, backup_type: str | None = None) -> None:
        self._type = BackupType(backup_type or config.BACKUP_TYPE)
        self._state = BackupState.STANDBY
        self._voltage: float = 0.0
        self._current: float = 0.0
        self._capacity_remaining_pct: float = 100.0
        self._health_pct: float = 100.0
        self._temperature_c: float = 20.0
        self._switchover_time: float = 0.0
        self._tether_was_connected: bool = True
        self._emergency_flight_start: float = 0.0
        self._last_health_check: float = 0.0

        # Расчёт параметров по типу
        if self._type == BackupType.LIPO:
            self._nominal_voltage = config.BACKUP_LIPO_CELLS * 3.7  # 22.2В
            self._max_voltage = config.BACKUP_LIPO_CELLS * 4.2       # 25.2В
            self._min_voltage = config.BACKUP_LIPO_CELLS * 3.3       # 19.8В
            self._capacity_wh = (
                config.BACKUP_LIPO_CAPACITY_MAH / 1000.0
                * self._nominal_voltage
            )  # ~111 Вт·ч
            self._weight_kg = config.BACKUP_LIPO_WEIGHT_KG

        elif self._type == BackupType.SUPERCAP:
            self._nominal_voltage = config.BACKUP_SUPERCAP_V * config.BACKUP_SUPERCAP_SERIES
            self._max_voltage = self._nominal_voltage
            self._min_voltage = self._nominal_voltage * 0.5
            cap_series = config.BACKUP_SUPERCAP_F / config.BACKUP_SUPERCAP_SERIES
            self._capacity_wh = (
                0.5 * cap_series * self._nominal_voltage ** 2
            ) / 3600.0
            self._weight_kg = 0.5 * config.BACKUP_SUPERCAP_SERIES

        else:  # HYBRID
            self._nominal_voltage = config.BACKUP_LIPO_CELLS * 3.7
            self._max_voltage = config.BACKUP_LIPO_CELLS * 4.2
            self._min_voltage = config.BACKUP_LIPO_CELLS * 3.3
            self._capacity_wh = (
                config.BACKUP_LIPO_CAPACITY_MAH / 1000.0
                * self._nominal_voltage
            )
            self._weight_kg = config.BACKUP_LIPO_WEIGHT_KG + 0.3  # +ионистор

    @property
    def state(self) -> BackupState:
        return self._state

    @property
    def backup_type(self) -> BackupType:
        return self._type

    @property
    def voltage(self) -> float:
        return self._voltage

    @property
    def capacity_percent(self) -> float:
        return self._capacity_remaining_pct

    @property
    def weight_kg(self) -> float:
        return self._weight_kg

    @property
    def is_ready(self) -> bool:
        """Резерв заряжен и готов к переключению."""
        return (
            self._state == BackupState.STANDBY
            and self._capacity_remaining_pct > 80.0
            and self._health_pct > 50.0
        )

    @property
    def emergency_time_remaining_sec(self) -> float:
        """Оставшееся время аварийного полёта (с)."""
        if self._state != BackupState.ACTIVE:
            return config.BACKUP_EMERGENCY_FLIGHT_SEC

        elapsed = time.monotonic() - self._emergency_flight_start
        remaining = config.BACKUP_EMERGENCY_FLIGHT_SEC - elapsed
        return max(0.0, remaining)

    @property
    def is_emergency_time_critical(self) -> bool:
        """Осталось менее 10 секунд аварийного полёта."""
        return (
            self._state == BackupState.ACTIVE
            and self.emergency_time_remaining_sec < 10.0
        )

    def update_telemetry(
        self,
        voltage: float,
        current: float,
        temperature: float,
        tether_connected: bool,
    ) -> BackupState:
        """
        Обновить телеметрию резервного источника.

        Вызывается каждый тик главного цикла.
        """
        self._voltage = voltage
        self._current = current
        self._temperature_c = temperature

        # Оценка оставшейся ёмкости
        if self._max_voltage > self._min_voltage:
            self._capacity_remaining_pct = max(0.0, min(100.0,
                (voltage - self._min_voltage)
                / (self._max_voltage - self._min_voltage) * 100.0
            ))

        # Детекция обрыва кабеля → переключение на резерв
        if self._tether_was_connected and not tether_connected:
            self._activate_backup("Обрыв кабеля")

        self._tether_was_connected = tether_connected

        # Если на резерве — следить за ёмкостью
        if self._state == BackupState.ACTIVE:
            if self._capacity_remaining_pct < 5.0:
                self._state = BackupState.DEPLETED
                logger.critical("Резерв РАЗРЯЖЕН — немедленная посадка!")

        # Периодическая проверка здоровья
        now = time.monotonic()
        if now - self._last_health_check > config.BACKUP_HEALTH_CHECK_INTERVAL_SEC:
            self._check_health()
            self._last_health_check = now

        return self._state

    def _activate_backup(self, reason: str) -> None:
        """Переключение на резервное питание."""
        self._state = BackupState.ACTIVE
        self._emergency_flight_start = time.monotonic()

        logger.critical(
            "ПЕРЕКЛЮЧЕНИЕ НА РЕЗЕРВ: %s | Тип=%s, V=%.1fВ, "
            "Ёмкость=%.0f%%, Время полёта=%.0f с",
            reason,
            self._type.value,
            self._voltage,
            self._capacity_remaining_pct,
            config.BACKUP_EMERGENCY_FLIGHT_SEC,
        )

    def _check_health(self) -> None:
        """Проверка состояния резервного источника."""
        issues: list[str] = []

        # Температура
        if self._type in (BackupType.LIPO, BackupType.HYBRID):
            if self._temperature_c > 45:
                issues.append(f"Перегрев LiPo: {self._temperature_c:.0f}°C")
                self._health_pct -= 10
            if self._temperature_c < -10:
                issues.append(f"Переохлаждение LiPo: {self._temperature_c:.0f}°C")
                self._health_pct -= 5

        # Напряжение
        if self._voltage < self._min_voltage and self._state == BackupState.STANDBY:
            issues.append(f"Низкое напряжение: {self._voltage:.1f}В")
            self._state = BackupState.CHARGING

        if issues:
            self._health_pct = max(0.0, self._health_pct)
            for issue in issues:
                logger.warning("Backup health: %s", issue)

    def request_emergency_landing(self) -> bool:
        """Запросить аварийную посадку (от BackupPower к MainController)."""
        if self._state == BackupState.ACTIVE:
            remaining = self.emergency_time_remaining_sec
            logger.critical(
                "BACKUP → MainController: АВАРИЙНАЯ ПОСАДКА! "
                "Осталось %.0f с на резерве",
                remaining,
            )
            return True
        return False

    def get_status_report(self) -> dict:
        """Сводка состояния резервного питания."""
        return {
            "type": self._type.value,
            "state": self._state.value,
            "voltage": round(self._voltage, 1),
            "capacity_pct": round(self._capacity_remaining_pct, 0),
            "health_pct": round(self._health_pct, 0),
            "weight_kg": self._weight_kg,
            "emergency_time_sec": round(self.emergency_time_remaining_sec, 0),
            "is_ready": self.is_ready,
            "temperature_c": round(self._temperature_c, 1),
        }
