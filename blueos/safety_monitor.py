"""
Монитор электробезопасности (Safety Monitor).

Реализует многоуровневую защиту силовой цепи 400В DC:

1. GFCI (УЗО) — детектор тока утечки на землю (≤30 мА / 30 мс)
2. IMD  — мониторинг сопротивления изоляции кабеля (≥100 кОм)
3. AFCI — детектор электрической дуги (10–100 кГц)
4. Power Dump — цепь аварийного сброса энергии на землю
5. Контактор — дугогасящий DC-размыкатель

Архитектура основана на патентах привязных дронов:
- US 12,404,036: GFCI + Power Dump для тросовых дронов
- US 11,376,987: Safe powering с мониторингом изоляции

Конкуренты и альтернативы:
- Elistair Safe-T 2: Dynamic Voltage Optimization (DVO),
  UPS-батарея, кевларовый трос, IP54, электронный crowbar
- Альтернатива 200В DC: кабель ≤20 м, потери выше, но безопаснее
- Альтернатива 120В DC: кабель ≤10 м, станция на крыше
"""

import enum
import logging
import time
from dataclasses import dataclass

from . import config

logger = logging.getLogger(__name__)


class SafetyState(enum.Enum):
    OK = "OK"
    WARNING = "WARNING"
    FAULT = "FAULT"
    SHUTDOWN = "SHUTDOWN"


class FaultType(enum.Enum):
    NONE = "NONE"
    GROUND_FAULT = "GROUND_FAULT"           # Ток утечки (GFCI)
    INSULATION_DEGRADED = "INSULATION_DEGRADED"  # Ухудшение изоляции
    INSULATION_FAILURE = "INSULATION_FAILURE"     # Пробой изоляции
    ARC_DETECTED = "ARC_DETECTED"           # Электрическая дуга
    VOLTAGE_IMBALANCE = "VOLTAGE_IMBALANCE" # Дисбаланс жил кабеля
    OVERCURRENT = "OVERCURRENT"             # Превышение тока
    OVERVOLTAGE = "OVERVOLTAGE"             # Превышение напряжения
    TETHER_DISCONNECT = "TETHER_DISCONNECT" # Обрыв кабеля
    CONTACTOR_FAILURE = "CONTACTOR_FAILURE" # Отказ контактора
    GROUND_ROD_MISSING = "GROUND_ROD_MISSING"  # Нет заземления


@dataclass
class SafetyTelemetry:
    """Данные датчиков безопасности."""
    leakage_current_ma: float = 0.0       # мА — ток утечки на землю
    insulation_resistance_kohm: float = 999.0  # кОм — сопротивление изоляции
    arc_frequency_khz: float = 0.0        # кГц — частота помех (дуга)
    arc_energy_level: float = 0.0         # 0–1 — уровень энергии дуги
    line_voltage_positive: float = 0.0    # В — напряжение + жилы
    line_voltage_negative: float = 0.0    # В — напряжение − жилы
    line_current_a: float = 0.0           # А — ток в линии
    tether_continuity: bool = True        # Целостность кабеля
    ground_rod_connected: bool = True     # Заземление подключено
    contactor_closed: bool = False        # Контактор замкнут
    shield_current_ma: float = 0.0        # мА — ток в экране кабеля


@dataclass
class SafetyEvent:
    """Событие безопасности для журнала."""
    timestamp: float
    fault_type: FaultType
    severity: SafetyState
    value: float
    threshold: float
    message: str
    action_taken: str


class SafetyMonitor:
    """
    Многоуровневый монитор электробезопасности.

    Частота опроса: SAFETY_CHECK_RATE_HZ (20 Гц = каждые 50 мс).
    При обнаружении fault — немедленное отключение (<30 мс).
    """

    def __init__(self) -> None:
        self._state = SafetyState.OK
        self._fault = FaultType.NONE
        self._telemetry = SafetyTelemetry()
        self._events: list[SafetyEvent] = []
        self._power_enabled = False
        self._gfci_armed = True
        self._afci_armed = config.SAFETY_ARC_DETECT_ENABLED
        self._last_check_time: float = 0.0
        self._warning_count: int = 0
        self._contactor_commanded = False

    @property
    def state(self) -> SafetyState:
        return self._state

    @property
    def fault(self) -> FaultType:
        return self._fault

    @property
    def is_power_safe(self) -> bool:
        """Безопасно ли подавать питание?"""
        return self._state in (SafetyState.OK, SafetyState.WARNING)

    @property
    def events(self) -> list[SafetyEvent]:
        return self._events

    @property
    def telemetry(self) -> SafetyTelemetry:
        return self._telemetry

    def pre_power_check(self) -> tuple[bool, str]:
        """
        Проверка перед подачей 400В в кабель.

        Вызывается ПЕРЕД замыканием контактора.
        Возвращает (safe, reason).
        """
        checks: list[tuple[bool, str]] = []

        # 1. Заземляющий штырь
        if config.SAFETY_GROUND_ROD_REQUIRED:
            ok = self._telemetry.ground_rod_connected
            checks.append((ok, "Заземление" if ok else "НЕТ ЗАЗЕМЛЕНИЯ"))

        # 2. Целостность кабеля
        ok = self._telemetry.tether_continuity
        checks.append((ok, "Кабель цел" if ok else "ОБРЫВ КАБЕЛЯ"))

        # 3. Сопротивление изоляции
        r = self._telemetry.insulation_resistance_kohm
        ok = r >= config.SAFETY_INSULATION_MIN_KOHM
        checks.append((ok, f"Изоляция {r:.0f} кОм" if ok else f"ИЗОЛЯЦИЯ {r:.0f} кОм < {config.SAFETY_INSULATION_MIN_KOHM} кОм"))

        # 4. Отсутствие тока утечки (при выключенном питании)
        leak = self._telemetry.leakage_current_ma
        ok = leak < 1.0
        checks.append((ok, f"Утечка {leak:.1f} мА" if ok else f"УТЕЧКА {leak:.1f} мА (питание выкл!)"))

        all_ok = all(c[0] for c in checks)
        report = " | ".join(c[1] for c in checks)

        if all_ok:
            logger.info("Pre-power check PASSED: %s", report)
        else:
            logger.critical("Pre-power check FAILED: %s", report)
            self._record_event(
                FaultType.NONE, SafetyState.FAULT, 0, 0,
                f"Pre-power check failed: {report}",
                "Подача питания заблокирована",
            )

        return all_ok, report

    def enable_power(self) -> bool:
        """Замкнуть контактор (подать 400В в кабель)."""
        ok, reason = self.pre_power_check()
        if not ok:
            return False

        self._contactor_commanded = True
        self._power_enabled = True
        self._gfci_armed = True
        logger.info("КОНТАКТОР ЗАМКНУТ — 400В DC в кабеле")
        return True

    def disable_power(self, reason: str = "штатное отключение") -> None:
        """Разомкнуть контактор (снять 400В)."""
        self._contactor_commanded = False
        self._power_enabled = False
        logger.info("КОНТАКТОР РАЗОМКНУТ — %s", reason)

    def emergency_shutdown(self, fault: FaultType, message: str) -> None:
        """
        Аварийное отключение.

        1. Разомкнуть контактор
        2. Активировать Power Dump (сброс остаточной энергии на землю)
        3. Записать событие
        """
        self._state = SafetyState.SHUTDOWN
        self._fault = fault
        self._power_enabled = False
        self._contactor_commanded = False

        action = "Контактор разомкнут"
        if config.SAFETY_POWER_DUMP_ENABLED:
            action += " + Power Dump активирован"

        self._record_event(fault, SafetyState.SHUTDOWN, 0, 0, message, action)
        logger.critical("АВАРИЙНОЕ ОТКЛЮЧЕНИЕ: %s | %s | %s", fault.value, message, action)

    def update(self, telemetry: SafetyTelemetry) -> SafetyState:
        """
        Обновить данные датчиков и выполнить все проверки.

        Вызывается с частотой SAFETY_CHECK_RATE_HZ (20 Гц).
        """
        self._telemetry = telemetry
        self._last_check_time = time.monotonic()

        if not self._power_enabled:
            return self._state

        # Проверки в порядке приоритета (от самых критичных)
        fault = self._check_gfci(telemetry)
        if fault:
            return self._state

        fault = self._check_arc(telemetry)
        if fault:
            return self._state

        fault = self._check_insulation(telemetry)
        if fault:
            return self._state

        fault = self._check_voltage_imbalance(telemetry)
        if fault:
            return self._state

        fault = self._check_overcurrent(telemetry)
        if fault:
            return self._state

        fault = self._check_tether_continuity(telemetry)
        if fault:
            return self._state

        # Всё чисто
        if self._state == SafetyState.WARNING:
            self._warning_count -= 1
            if self._warning_count <= 0:
                self._state = SafetyState.OK
                self._fault = FaultType.NONE

        return self._state

    def reset_fault(self) -> bool:
        """
        Сброс аварии (требует ручного подтверждения оператора).

        Возвращает True если сброс успешен.
        """
        if self._state != SafetyState.SHUTDOWN:
            return True

        ok, _ = self.pre_power_check()
        if ok:
            self._state = SafetyState.OK
            self._fault = FaultType.NONE
            self._warning_count = 0
            logger.info("Авария сброшена оператором")
            return True

        logger.warning("Сброс аварии невозможен — pre-power check не пройден")
        return False

    # ------------------------------------------------------------------
    # Проверки датчиков
    # ------------------------------------------------------------------

    def _check_gfci(self, t: SafetyTelemetry) -> bool:
        """УЗО: ток утечки на землю."""
        if t.leakage_current_ma >= config.SAFETY_GFCI_TRIP_MA:
            self.emergency_shutdown(
                FaultType.GROUND_FAULT,
                f"Ток утечки {t.leakage_current_ma:.1f} мА ≥ {config.SAFETY_GFCI_TRIP_MA} мА",
            )
            return True

        if t.leakage_current_ma > config.SAFETY_GFCI_TRIP_MA * 0.5:
            self._set_warning(
                FaultType.GROUND_FAULT,
                t.leakage_current_ma,
                config.SAFETY_GFCI_TRIP_MA,
                f"Повышенный ток утечки: {t.leakage_current_ma:.1f} мА",
            )
            return False

        return False

    def _check_arc(self, t: SafetyTelemetry) -> bool:
        """AFCI: детектор электрической дуги."""
        if not self._afci_armed:
            return False

        freq_lo, freq_hi = config.SAFETY_ARC_FREQ_RANGE_KHZ
        in_range = freq_lo <= t.arc_frequency_khz <= freq_hi

        if in_range and t.arc_energy_level > 0.7:
            self.emergency_shutdown(
                FaultType.ARC_DETECTED,
                f"Дуга: {t.arc_frequency_khz:.0f} кГц, энергия {t.arc_energy_level:.2f}",
            )
            return True

        if in_range and t.arc_energy_level > 0.3:
            self._set_warning(
                FaultType.ARC_DETECTED,
                t.arc_energy_level, 0.7,
                f"Подозрение на дугу: {t.arc_frequency_khz:.0f} кГц",
            )

        return False

    def _check_insulation(self, t: SafetyTelemetry) -> bool:
        """IMD: сопротивление изоляции кабеля."""
        if t.insulation_resistance_kohm < config.SAFETY_INSULATION_MIN_KOHM:
            self.emergency_shutdown(
                FaultType.INSULATION_FAILURE,
                f"Изоляция {t.insulation_resistance_kohm:.0f} кОм < {config.SAFETY_INSULATION_MIN_KOHM} кОм",
            )
            return True

        if t.insulation_resistance_kohm < config.SAFETY_INSULATION_WARN_KOHM:
            self._set_warning(
                FaultType.INSULATION_DEGRADED,
                t.insulation_resistance_kohm,
                config.SAFETY_INSULATION_WARN_KOHM,
                f"Ухудшение изоляции: {t.insulation_resistance_kohm:.0f} кОм",
            )

        return False

    def _check_voltage_imbalance(self, t: SafetyTelemetry) -> bool:
        """Дисбаланс напряжения между жилами."""
        imbalance = abs(t.line_voltage_positive - abs(t.line_voltage_negative))
        if imbalance > config.SAFETY_VOLTAGE_IMBALANCE_MAX_V * 3:
            self.emergency_shutdown(
                FaultType.VOLTAGE_IMBALANCE,
                f"Дисбаланс {imbalance:.1f} В (критический)",
            )
            return True

        if imbalance > config.SAFETY_VOLTAGE_IMBALANCE_MAX_V:
            self._set_warning(
                FaultType.VOLTAGE_IMBALANCE,
                imbalance,
                config.SAFETY_VOLTAGE_IMBALANCE_MAX_V,
                f"Дисбаланс напряжения: {imbalance:.1f} В",
            )

        return False

    def _check_overcurrent(self, t: SafetyTelemetry) -> bool:
        """Превышение тока в линии."""
        max_current = config.TETHER_MAX_CURRENT * 1.2  # 20% запас
        if t.line_current_a > max_current:
            self.emergency_shutdown(
                FaultType.OVERCURRENT,
                f"Ток {t.line_current_a:.1f} А > {max_current:.1f} А",
            )
            return True
        return False

    def _check_tether_continuity(self, t: SafetyTelemetry) -> bool:
        """Обрыв кабеля."""
        if not t.tether_continuity:
            self.emergency_shutdown(
                FaultType.TETHER_DISCONNECT,
                "Обрыв кабеля (потеря continuity)",
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Внутренние
    # ------------------------------------------------------------------

    def _set_warning(
        self, fault: FaultType, value: float, threshold: float, message: str,
    ) -> None:
        if self._state == SafetyState.SHUTDOWN:
            return
        self._state = SafetyState.WARNING
        self._fault = fault
        self._warning_count = max(self._warning_count, 30)  # ~1.5 с при 20 Гц
        self._record_event(fault, SafetyState.WARNING, value, threshold, message, "Мониторинг")
        logger.warning("SAFETY WARNING: %s", message)

    def _record_event(
        self,
        fault: FaultType,
        severity: SafetyState,
        value: float,
        threshold: float,
        message: str,
        action: str,
    ) -> None:
        event = SafetyEvent(
            timestamp=time.monotonic(),
            fault_type=fault,
            severity=severity,
            value=value,
            threshold=threshold,
            message=message,
            action_taken=action,
        )
        self._events.append(event)
        # Ограничиваем журнал
        if len(self._events) > 1000:
            self._events = self._events[-500:]


def get_voltage_alternatives() -> list[dict]:
    """
    Альтернативные схемы напряжения тросового питания.

    Основано на анализе конкурентов:
    - Elistair Safe-T 2: DVO (динамическая оптимизация), 2.2 кВт, <400В
    - Патент US 11,376,987: GFCI + Power Dump на DC
    - Патент US 12,404,036: GFCI + AFCI + заземление экрана
    """
    return [
        {
            "voltage": 400,
            "cable_length_max_m": 50,
            "cable_section_mm2": 6,
            "current_a": 37.5,
            "losses_v": 20,
            "safety_class": "ВЫСОКОЕ НАПРЯЖЕНИЕ",
            "requires": ["GFCI 30мА", "IMD", "AFCI", "Power Dump",
                         "Дугогасящий контактор", "Заземление"],
            "pros": "Тонкий лёгкий кабель, низкие потери",
            "cons": "Летальное напряжение, сложная защита",
            "reference": "Elistair Orion (≥400В, серверный rack)",
        },
        {
            "voltage": 200,
            "cable_length_max_m": 20,
            "cable_section_mm2": 10,
            "current_a": 75,
            "losses_v": 15,
            "safety_class": "ОПАСНОЕ НАПРЯЖЕНИЕ",
            "requires": ["GFCI 30мА", "Контактор", "Заземление"],
            "pros": "Проще защита, станция на крыше, кабель 20 м",
            "cons": "Толстый кабель, станцию нужно поднимать на крышу",
            "reference": "Elistair Safe-T 2 (DVO, до 200В эфф.)",
        },
        {
            "voltage": 120,
            "cable_length_max_m": 10,
            "cable_section_mm2": 16,
            "current_a": 125,
            "losses_v": 8,
            "safety_class": "НИЗКОЕ НАПРЯЖЕНИЕ (условно безопасное)",
            "requires": ["Контактор", "Предохранитель"],
            "pros": "Безопасно для человека, простая защита",
            "cons": "Очень толстый и тяжёлый кабель, только рядом со станцией",
            "reference": "CyPhy Works PARC (низковольтный тросовый)",
        },
        {
            "voltage": 48,
            "cable_length_max_m": 5,
            "cable_section_mm2": 35,
            "current_a": 312,
            "losses_v": 5,
            "safety_class": "БЕЗОПАСНОЕ (SELV)",
            "requires": ["Предохранитель"],
            "pros": "Полностью безопасно, нет риска поражения",
            "cons": "Кабель как сварочный, только для станции на крыше рядом с дроном",
            "reference": "Прямое подключение к банку ионисторов",
        },
    ]
