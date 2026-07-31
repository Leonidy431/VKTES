"""
BlueOS Main — точка входа модуля полуавтоматического управления Борей (Boreas).

Главный цикл:
1. Подключение к полётному контроллеру (MAVLink)
2. Проверка электробезопасности (SafetyMonitor)
3. Мониторинг энергосистемы + резервного питания
4. Тепловизионный анализ снега (Hailo AI)
5. Адаптивное управление углом наклона и импульсами тяги
6. Выполнение плана очистки: Зарядка → Облёт → HOVER_BLOW/BULLDOZER → Отход
7. Обработка аварийных ситуаций

Запуск:
    python -m blueos.main [--connection udp:127.0.0.1:14550] [--simulate]
"""

import argparse
import logging
import signal
import sys
import time

from . import config
from .backup_power import BackupPowerManager, BackupState
from .burst_vibrator import BurstMode, BurstVibrator
from .hover_blow import HoverBlowController
from .mavlink_interface import MAVLinkInterface
from .perception import PerceptionModule
from .planner import PlannerModule
from .power_manager import PowerManager, PowerState
from .safety_monitor import SafetyMonitor, SafetyState, SafetyTelemetry
from .state_machine import State, StateMachine
from .thermal_analyzer import ThermalAnalyzer
from .tilt_controller import SnowType, TiltController

logger = logging.getLogger("blueos")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class BoreasController:
    """
    Главный контроллер дрона Борей.

    Координирует работу всех модулей:
    - StateMachine:          управление режимами
    - PowerManager:          энергосистема (ионисторы)
    - BackupPowerManager:    резервное питание (LiPo/ионистор)
    - SafetyMonitor:         электробезопасность 400В
    - TiltController:        угол наклона рамы
    - BurstVibrator:         импульсный режим тяги
    - ThermalAnalyzer:       тепловизор + Hailo AI
    - PerceptionModule:      камера + LiDAR
    - PlannerModule:         маршруты
    - HoverBlowController:   режим обдува
    - MAVLinkInterface:      связь с ArduPilot
    """

    def __init__(self, connection_string: str | None = None, simulate: bool = False):
        self._simulate = simulate
        self._running = False
        self._completed_lanes = 0

        # Основные модули
        self.sm = StateMachine()
        self.power = PowerManager()
        self.perception = PerceptionModule()
        self.planner = PlannerModule(self.perception)
        self.mav = MAVLinkInterface(connection_string)

        # Новые модули
        self.tilt = TiltController()
        self.burst = BurstVibrator()
        self.safety = SafetyMonitor()
        self.thermal = ThermalAnalyzer()
        self.backup = BackupPowerManager()
        self.hover_blow = HoverBlowController()

        # Слушатель переходов состояний
        self.sm.add_listener(self._on_state_change)

        # Текущая рекомендация от тепловизора
        self._recommended_mode: str = "BULLDOZER"
        self._current_snow_type: SnowType = SnowType.UNKNOWN
        self._current_snow_depth_mm: float = 0.0

    def start(self) -> None:
        """Запуск главного цикла."""
        logger.info("=" * 60)
        logger.info("  БОРЕЙ (Boreas) BlueOS v%s", "0.2.0")
        logger.info("  Режим: %s", "СИМУЛЯЦИЯ" if self._simulate else "БОЕВОЙ")
        logger.info("  Модули: Tilt | Burst | Safety | Thermal | Backup | HoverBlow")
        logger.info("=" * 60)

        # Инициализация тепловизора
        self.thermal.initialize()

        # Подключение к MAVLink
        if not self._simulate:
            if not self.mav.connect():
                logger.critical("Не удалось подключиться к полётному контроллеру")
                sys.exit(1)

        # Pre-power check перед подачей 400В
        if not self._simulate:
            safe, report = self.safety.pre_power_check()
            if not safe:
                logger.critical("Электробезопасность: %s", report)
                logger.critical("Подача питания заблокирована. Устраните неисправности.")
                sys.exit(2)
            self.safety.enable_power()

        self._running = True
        self._main_loop()

    def stop(self) -> None:
        """Остановка контроллера."""
        logger.info("Остановка контроллера Борей...")
        self._running = False

        # Безопасное отключение
        self.safety.disable_power("штатное завершение")

        if self.mav.is_connected:
            self.mav.disarm()
            self.mav.disconnect()

    # ------------------------------------------------------------------
    # Главный цикл
    # ------------------------------------------------------------------

    def _main_loop(self) -> None:
        dt = 1.0 / config.TELEMETRY_RATE_HZ

        while self._running:
            try:
                # 1. Обновить всю телеметрию
                self._update_telemetry()

                # 2. Проверить электробезопасность (приоритет!)
                if self._check_safety():
                    time.sleep(dt)
                    continue

                # 3. Проверить аварийные условия
                if self._check_emergency():
                    time.sleep(dt)
                    continue

                # 4. Обновить угол наклона (плавное изменение)
                self.tilt.update(dt)

                # 5. Выполнить логику текущего состояния
                self._tick()

                time.sleep(dt)

            except KeyboardInterrupt:
                logger.info("Прерывание оператором (Ctrl+C)")
                self.stop()
                break
            except Exception:
                logger.exception("Критическая ошибка в главном цикле")
                self.sm.force_emergency()

    def _update_telemetry(self) -> None:
        """Обновить все данные телеметрии."""
        if not self._simulate:
            self.mav.update_telemetry()

        # Энергосистема
        gs = self.mav.ground_station
        self.power.update_telemetry(
            voltage=gs.supercap_voltage,
            current=gs.supercap_current,
            temperature=gs.supercap_temp_c,
        )

        # Электробезопасность
        safety_data = SafetyTelemetry(
            tether_continuity=gs.tether_connected,
        )
        self.safety.update(safety_data)

        # Резервное питание
        self.backup.update_telemetry(
            voltage=0.0,  # Заглушка: реальные данные от датчика
            current=0.0,
            temperature=20.0,
            tether_connected=gs.tether_connected,
        )

    def _check_safety(self) -> bool:
        """Проверить электробезопасность. Возвращает True при fault."""
        if self.safety.state == SafetyState.SHUTDOWN:
            logger.critical(
                "SAFETY SHUTDOWN: %s", self.safety.fault.value,
            )
            self.sm.force_emergency()
            return True

        if self.safety.state == SafetyState.WARNING:
            logger.warning(
                "SAFETY WARNING: %s (V+=%.0f В-=%.0f утечка=%.1f мА изол=%.0f кОм)",
                self.safety.fault.value,
                self.safety.telemetry.line_voltage_positive,
                self.safety.telemetry.line_voltage_negative,
                self.safety.telemetry.leakage_current_ma,
                self.safety.telemetry.insulation_resistance_kohm,
            )

        return False

    def _check_emergency(self) -> bool:
        """Проверить аварийные условия."""
        # Критический уровень энергии основной шины
        if self.power.state == PowerState.CRITICAL:
            logger.critical("АВАРИЙНЫЙ уровень напряжения: %.1fВ", self.power.voltage)
            self.sm.force_emergency()
            self._handle_emergency()
            return True

        # Резервное питание активировано (обрыв кабеля)
        if self.backup.state == BackupState.ACTIVE:
            logger.critical(
                "НА РЕЗЕРВЕ: осталось %.0f с — АВАРИЙНАЯ ПОСАДКА",
                self.backup.emergency_time_remaining_sec,
            )
            self.sm.force_emergency()
            return True

        # Резервное питание разряжено
        if self.backup.state == BackupState.DEPLETED:
            logger.critical("РЕЗЕРВ РАЗРЯЖЕН — немедленная посадка")
            self.sm.force_emergency()
            return True

        # Потеря связи
        if not self._simulate and not self.mav.is_connected:
            logger.critical("Потеря связи MAVLink")
            self.sm.force_emergency()
            return True

        return False

    def _tick(self) -> None:
        """Выполнить логику текущего состояния."""
        state = self.sm.state

        if state == State.IDLE:
            self._tick_idle()
        elif state == State.SURVEY:
            self._tick_survey()
        elif state == State.TRANSIT:
            self._tick_transit()
        elif state == State.BULLDOZER:
            self._tick_bulldozer()
        elif state == State.HOVER_BLOW:
            self._tick_hover_blow()
        elif state == State.RETREAT:
            self._tick_retreat()
        elif state == State.EMERGENCY:
            self._handle_emergency()

    # ------------------------------------------------------------------
    # IDLE
    # ------------------------------------------------------------------

    def _tick_idle(self) -> None:
        if self.power.state == PowerState.CHARGING:
            if int(self.sm.time_in_state) % 10 == 0 and self.sm.time_in_state > 1:
                logger.info(
                    "Зарядка: %.0f%% (%.1fВ, %.0f с) | Резерв: %s %.0f%%",
                    self.power.energy_percent,
                    self.power.voltage,
                    self.power.charge_elapsed_sec,
                    self.backup.state.value,
                    self.backup.capacity_percent,
                )
            return

        if self.power.state == PowerState.READY:
            if self.planner.plan is None or self.planner.plan.lane_count == 0:
                logger.info("План отсутствует — переход в SURVEY")
                self.sm.transition_to(State.SURVEY)
            else:
                logger.info("Заряд готов — переход в TRANSIT")
                self.sm.transition_to(State.TRANSIT)

    # ------------------------------------------------------------------
    # SURVEY (с тепловизором)
    # ------------------------------------------------------------------

    def _tick_survey(self) -> None:
        if self.sm.time_in_state < 2.0:
            if self.sm.time_in_state < 0.2:
                logger.info("SURVEY: взлёт на %.0f м", config.SURVEY_ALTITUDE_M)
                self.tilt.prepare_for_flight()
                self.mav.arm()
                self.mav.set_mode("GUIDED")
                self.mav.takeoff(config.SURVEY_ALTITUDE_M)
            return

        # Тепловизионный анализ
        assessment = self.thermal.process_frame(
            lidar_depth_mm=self._current_snow_depth_mm,
            ambient_temp_c=-10.0,
        )

        self._current_snow_type = assessment.dominant_type
        self._recommended_mode = assessment.recommended_mode

        # Передать скрытые препятствия в PerceptionModule
        for hidden in assessment.hidden_obstacles:
            from .perception import Obstacle
            self.perception._obstacles.append(Obstacle(
                x=hidden.x,
                y=hidden.y,
                radius=hidden.radius + config.OBSTACLE_BUFFER_M,
                label=hidden.obstacle_type,
            ))

        # Генерация плана
        plan = self.planner.generate_plan()

        if plan.lane_count > 0:
            logger.info(
                "SURVEY завершён: %d дорожек, ~%.0f мин | "
                "Снег: %s, глубина ~%.0f мм, рекомендация: %s",
                plan.lane_count, plan.estimated_total_time_min,
                assessment.dominant_type.value,
                assessment.mean_depth_mm,
                self._recommended_mode,
            )

            # Генерация плана HOVER_BLOW если рекомендовано
            if self._recommended_mode == "HOVER_BLOW":
                self.hover_blow.generate_plan(
                    self.perception.roof.vertices,
                    [(o.x, o.y, o.radius) for o in self.perception.obstacles],
                )
                self.hover_blow.adjust_for_snow_depth(assessment.mean_depth_mm)

            self.sm.transition_to(State.TRANSIT)
        else:
            logger.warning("SURVEY: не удалось построить план — RETREAT")
            self.sm.transition_to(State.RETREAT)

    # ------------------------------------------------------------------
    # TRANSIT
    # ------------------------------------------------------------------

    def _tick_transit(self) -> None:
        # Выбор режима: HOVER_BLOW или BULLDOZER
        if self._recommended_mode == "HOVER_BLOW" and self.hover_blow.plan:
            target_state = State.HOVER_BLOW
        elif self._recommended_mode == "SKIP":
            logger.warning("TRANSIT: снег слишком тяжёлый — пропуск")
            self.sm.transition_to(State.RETREAT)
            return
        else:
            target_state = State.BULLDOZER

        lane = self.planner.get_next_lane(self._completed_lanes)
        if lane is None:
            logger.info("TRANSIT: все дорожки пройдены — RETREAT")
            self.sm.transition_to(State.RETREAT)
            return

        if self.sm.time_in_state < 1.0:
            wp = lane.waypoints[0]
            logger.info(
                "TRANSIT: перелёт к дорожке #%d (%.1f, %.1f) → %s",
                self._completed_lanes + 1, wp.x, wp.y, target_state.value,
            )
            self.tilt.prepare_for_flight()
            self.mav.goto_local(wp.x, wp.y, -config.TRANSIT_ALTITUDE_M)
            return

        # Посадка (только для BULLDOZER)
        if target_state == State.BULLDOZER:
            logger.info("TRANSIT: посадка на крышу → BULLDOZER")
            self.mav.land()

        self.sm.transition_to(target_state)

    # ------------------------------------------------------------------
    # BULLDOZER (с управлением углом и импульсами)
    # ------------------------------------------------------------------

    def _tick_bulldozer(self) -> None:
        elapsed = self.sm.time_in_state

        # Таймаут
        if elapsed > config.BURST_MAX_DURATION_SEC:
            logger.info("BULLDOZER: таймаут %.0f с — RETREAT", elapsed)
            self._completed_lanes += 1
            self.sm.transition_to(State.RETREAT)
            return

        # Энергия
        if self.power.state == PowerState.LOW_POWER:
            logger.warning("BULLDOZER: низкий заряд (%.1fВ) — RETREAT", self.power.voltage)
            self._completed_lanes += 1
            self.sm.transition_to(State.RETREAT)
            return

        # Инициализация при входе в режим
        if elapsed < 0.2:
            # Установить угол наклона по типу снега
            self.tilt.set_snow_type(self._current_snow_type)

            # Выбрать режим импульсов
            # TODO(backlog): _burst_mode вычисляется, но не применяется — RAMP
            # всегда переключается на PULSED (см. BurstVibrator._compute_ramp),
            # поэтому HAMMER для WET/ICE сейчас недостижим через этот путь.
            # См. BACKLOG.md.
            _burst_mode = self.burst.recommend_mode(
                self._current_snow_type.value,
                self._current_snow_depth_mm,
            )
            self.burst.set_mode(BurstMode.RAMP)  # Всегда начинаем с RAMP
            self.burst.reset()

            # Проверка: справится ли дрон?
            if not self.tilt.can_handle_snow(self._current_snow_type, self._current_snow_depth_mm):
                logger.warning(
                    "BULLDOZER: снег %s %.0f мм — тяги не хватит! Попытка с макс. углом.",
                    self._current_snow_type.value, self._current_snow_depth_mm,
                )

            logger.info(
                "BULLDOZER: старт | Снег=%s, Угол=%.0f°, Fh=%.1f кгс, "
                "V=%.1fВ, E=%.0f кДж",
                self._current_snow_type.value,
                self.tilt.target_angle,
                self.tilt.horizontal_thrust_kgf,
                self.power.voltage,
                self.power.energy_kj,
            )
            return

        # Адаптивное управление углом (побочный эффект — обновляет self.tilt.target_angle,
        # возвращаемое значение не нужно здесь, читается через свойства ниже)
        self.tilt.compute_optimal_angle(
            snow_type=self._current_snow_type,
            snow_depth_mm=self._current_snow_depth_mm,
            ground_speed_ms=self.mav.drone.ground_speed_ms,
            motor_current_a=self.mav.drone.battery_current,
        )

        # Импульсный throttle
        throttle = self.burst.get_throttle()

        # Ground Steering с текущим throttle
        self.mav.set_ground_steering(throttle_pct=throttle, yaw_rate_dps=0.0)

        # Тепловизионный кадр в «окне видимости» (когда пропеллерная пыль оседает)
        if self.burst.get_visibility_window():
            self.thermal.process_frame(
                lidar_depth_mm=self._current_snow_depth_mm,
            )

        # Логирование каждые 5 секунд
        if int(elapsed) % 5 == 0 and elapsed > 1:
            logger.info(
                "BULLDOZER: %.0fс | Угол=%.0f° Fh=%.1f кгс | "
                "Газ=%.0f%% (%s) | V=%.1fВ",
                elapsed,
                self.tilt.current_angle,
                self.tilt.horizontal_thrust_kgf,
                throttle,
                self.burst.mode.value,
                self.power.voltage,
            )

    # ------------------------------------------------------------------
    # HOVER_BLOW
    # ------------------------------------------------------------------

    def _tick_hover_blow(self) -> None:
        elapsed = self.sm.time_in_state

        # Таймаут
        if elapsed > config.BURST_MAX_DURATION_SEC:
            logger.info("HOVER_BLOW: таймаут — RETREAT")
            self.hover_blow.stop()
            self.sm.transition_to(State.RETREAT)
            return

        # Энергия
        if self.power.state == PowerState.LOW_POWER:
            logger.warning("HOVER_BLOW: низкий заряд — RETREAT")
            self.hover_blow.stop()
            self.sm.transition_to(State.RETREAT)
            return

        # Инициализация
        if elapsed < 0.2:
            self.hover_blow.start()
            self.tilt.prepare_for_flight()
            logger.info(
                "HOVER_BLOW: старт | Высота=%.1f м, Газ=%.0f%%, "
                "Точек=%d, V=%.1fВ",
                self.hover_blow.altitude,
                self.hover_blow.throttle,
                len(self.hover_blow.plan.points) if self.hover_blow.plan else 0,
                self.power.voltage,
            )
            return

        # Обновить состояние обдува
        dt = 1.0 / config.TELEMETRY_RATE_HZ
        x, y, z, point_done = self.hover_blow.update(dt)

        if not self.hover_blow.is_active:
            # План обдува завершён
            logger.info("HOVER_BLOW: план завершён — RETREAT")
            self._completed_lanes += 1
            self.sm.transition_to(State.RETREAT)
            return

        # Перемещение к текущей точке обдува
        self.mav.goto_local(x, y, z, speed=1.0)

        # Логирование
        if self.hover_blow.plan and int(elapsed) % 5 == 0 and elapsed > 1:
            logger.info(
                "HOVER_BLOW: %.0f%% | точка %d/%d | V=%.1fВ",
                self.hover_blow.plan.progress_percent,
                self.hover_blow.plan.current_index + 1,
                len(self.hover_blow.plan.points),
                self.power.voltage,
            )

    # ------------------------------------------------------------------
    # RETREAT
    # ------------------------------------------------------------------

    def _tick_retreat(self) -> None:
        if self.sm.time_in_state < 0.5:
            logger.info("RETREAT: возврат на зарядку")
            self.tilt.prepare_for_flight()
            self.burst.set_mode(BurstMode.CONTINUOUS)
            self.mav.set_mode("GUIDED")
            self.mav.takeoff(config.TRANSIT_ALTITUDE_M)
            return

        if self.sm.time_in_state > 3.0:
            logger.info("RETREAT: посадка, начало зарядки")
            self.mav.land()
            self.mav.disarm()
            self.mav.send_gs_command("START_CHARGE")
            self.power.start_charge()
            self.sm.transition_to(State.IDLE)

    # ------------------------------------------------------------------
    # EMERGENCY
    # ------------------------------------------------------------------

    def _handle_emergency(self) -> None:
        if self.sm.time_in_state < 0.5:
            # Определить тип аварии
            on_backup = self.backup.state == BackupState.ACTIVE
            safety_fault = self.safety.state == SafetyState.SHUTDOWN

            if safety_fault:
                logger.critical(
                    "EMERGENCY (электробезопасность): %s — посадка",
                    self.safety.fault.value,
                )
                self.safety.disable_power("EMERGENCY")

            if on_backup:
                logger.critical(
                    "EMERGENCY (резерв): %.0f с осталось — экстренная посадка",
                    self.backup.emergency_time_remaining_sec,
                )

            self.tilt.prepare_for_flight()
            self.burst.set_mode(BurstMode.CONTINUOUS)
            self.hover_blow.stop()
            self.mav.set_mode("LAND")
            self.mav.send_gs_command("EMERGENCY_STOP")

    # ------------------------------------------------------------------
    # Слушатель переходов
    # ------------------------------------------------------------------

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        logger.info(
            "Состояние: %s → %s (V=%.1fВ, E=%.0f%%, Safety=%s, Backup=%s)",
            old_state.value, new_state.value,
            self.power.voltage, self.power.energy_percent,
            self.safety.state.value, self.backup.state.value,
        )

        if new_state == State.IDLE:
            self.mav.send_gs_command("START_CHARGE")
            self.power.start_charge()

        if new_state == State.BULLDOZER:
            self.mav.send_gs_command("ENABLE_BOOST")
            self.power.start_discharge()

        if new_state == State.HOVER_BLOW:
            self.mav.send_gs_command("ENABLE_BOOST")
            self.power.start_discharge()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BlueOS — модуль управления дроном Борей (Boreas)",
    )
    parser.add_argument(
        "--connection",
        default=config.MAVLINK_CONNECTION,
        help="Строка подключения MAVLink (по умолчанию: %(default)s)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Режим симуляции (без реального MAVLink)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Уровень логирования (по умолчанию: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format=LOG_FORMAT)

    controller = BoreasController(
        connection_string=args.connection,
        simulate=args.simulate,
    )

    def signal_handler(sig: int, frame: object) -> None:
        logger.info("Получен сигнал %s — завершение", sig)
        controller.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    controller.start()


if __name__ == "__main__":  # pragma: no cover
    main()
