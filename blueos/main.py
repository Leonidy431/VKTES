"""
BlueOS Main — точка входа модуля полуавтоматического управления Борей (Boreas).

Главный цикл:
1. Подключение к полётному контроллеру (MAVLink)
2. Мониторинг энергосистемы
3. Выполнение плана очистки по циклам: Зарядка → Облёт → Толкание → Отход
4. Обработка аварийных ситуаций

Запуск:
    python -m blueos.main [--connection udp:127.0.0.1:14550] [--simulate]
"""

import argparse
import logging
import signal
import sys
import time

from . import config
from .state_machine import StateMachine, State
from .power_manager import PowerManager, PowerState
from .perception import PerceptionModule
from .planner import PlannerModule, Lane
from .mavlink_interface import MAVLinkInterface

logger = logging.getLogger("blueos")

# ---------------------------------------------------------------------------
# Конфигурация логирования
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class BoreasController:
    """
    Главный контроллер дрона Борей.

    Координирует работу всех модулей:
    - StateMachine: управление режимами
    - PowerManager: энергосистема
    - PerceptionModule: зрение
    - PlannerModule: маршруты
    - MAVLinkInterface: связь с ArduPilot
    """

    def __init__(self, connection_string: str | None = None, simulate: bool = False):
        self._simulate = simulate
        self._running = False
        self._completed_lanes = 0

        # Инициализация модулей
        self.sm = StateMachine()
        self.power = PowerManager()
        self.perception = PerceptionModule()
        self.planner = PlannerModule(self.perception)
        self.mav = MAVLinkInterface(connection_string)

        # Слушатель переходов состояний
        self.sm.add_listener(self._on_state_change)

    def start(self) -> None:
        """Запуск главного цикла."""
        logger.info("=" * 60)
        logger.info("  БОРЕЙ (Boreas) BlueOS v%s", "0.1.0")
        logger.info("  Режим: %s", "СИМУЛЯЦИЯ" if self._simulate else "БОЕВОЙ")
        logger.info("=" * 60)

        # Подключение к MAVLink
        if not self._simulate:
            if not self.mav.connect():
                logger.critical("Не удалось подключиться к полётному контроллеру")
                sys.exit(1)

        self._running = True
        self._main_loop()

    def stop(self) -> None:
        """Остановка контроллера."""
        logger.info("Остановка контроллера Борей...")
        self._running = False
        if self.mav.is_connected:
            self.mav.disarm()
            self.mav.disconnect()

    # ------------------------------------------------------------------
    # Главный цикл
    # ------------------------------------------------------------------

    def _main_loop(self) -> None:
        """
        Главный цикл управления.

        Частота: TELEMETRY_RATE_HZ (10 Гц по умолчанию).
        """
        dt = 1.0 / config.TELEMETRY_RATE_HZ

        while self._running:
            try:
                # 1. Обновить телеметрию
                self._update_telemetry()

                # 2. Проверить аварийные условия
                if self._check_emergency():
                    continue

                # 3. Выполнить логику текущего состояния
                self._tick()

                # 4. Пауза до следующего цикла
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

        # Обновить энергосистему
        gs = self.mav.ground_station
        self.power.update_telemetry(
            voltage=gs.supercap_voltage,
            current=gs.supercap_current,
            temperature=gs.supercap_temp_c,
        )

    def _check_emergency(self) -> bool:
        """
        Проверить аварийные условия.

        Возвращает True если произошёл переход в EMERGENCY.
        """
        # Критический уровень энергии
        if self.power.state == PowerState.CRITICAL:
            logger.critical(
                "АВАРИЙНЫЙ уровень напряжения: %.1fВ", self.power.voltage
            )
            self.sm.force_emergency()
            self._handle_emergency()
            return True

        # Потеря связи с MAVLink
        if not self._simulate and not self.mav.is_connected:
            logger.critical("Потеря связи MAVLink")
            self.sm.force_emergency()
            self._handle_emergency()
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
        elif state == State.RETREAT:
            self._tick_retreat()
        elif state == State.EMERGENCY:
            self._handle_emergency()

    # ------------------------------------------------------------------
    # Логика каждого состояния
    # ------------------------------------------------------------------

    def _tick_idle(self) -> None:
        """
        Состояние IDLE: ожидание и зарядка.

        - Отправить команду START_CHARGE на наземную станцию
        - Ждать пока PowerManager не сообщит READY
        - Перейти в SURVEY (если план не построен) или TRANSIT
        """
        if self.power.state == PowerState.CHARGING:
            # Ждём зарядки — логируем прогресс раз в 10 секунд
            if int(self.sm.time_in_state) % 10 == 0 and self.sm.time_in_state > 1:
                logger.info(
                    "Зарядка: %.0f%% (%.1fВ, %.0f с)",
                    self.power.energy_percent,
                    self.power.voltage,
                    self.power.charge_elapsed_sec,
                )
            return

        if self.power.state == PowerState.READY:
            if self.planner.plan is None or self.planner.plan.lane_count == 0:
                # Нет плана — нужно сделать облёт
                logger.info("План отсутствует — переход в SURVEY")
                self.sm.transition_to(State.SURVEY)
            else:
                # Есть план — переход к следующей дорожке
                logger.info("Заряд готов — переход в TRANSIT")
                self.sm.transition_to(State.TRANSIT)

    def _tick_survey(self) -> None:
        """
        Состояние SURVEY: облёт крыши и построение карты.

        1. Взлёт на SURVEY_ALTITUDE_M
        2. Облёт периметра с камерой и LiDAR
        3. Построение плана очистки
        4. Переход в TRANSIT (или RETREAT если план пуст)
        """
        if self.sm.time_in_state < 2.0:
            # Первые 2 секунды — взлёт
            if self.sm.time_in_state < 0.2:
                logger.info("SURVEY: взлёт на %.0f м", config.SURVEY_ALTITUDE_M)
                self.mav.arm()
                self.mav.set_mode("GUIDED")
                self.mav.takeoff(config.SURVEY_ALTITUDE_M)
            return

        # Заглушка: в реальной реализации — облёт и сбор данных
        # После сбора данных — генерация плана
        plan = self.planner.generate_plan()

        if plan.lane_count > 0:
            logger.info(
                "SURVEY завершён: %d дорожек, ~%.0f мин",
                plan.lane_count, plan.estimated_total_time_min,
            )
            self.sm.transition_to(State.TRANSIT)
        else:
            logger.warning("SURVEY: не удалось построить план — RETREAT")
            self.sm.transition_to(State.RETREAT)

    def _tick_transit(self) -> None:
        """
        Состояние TRANSIT: перелёт к началу следующей дорожки.

        1. Получить следующую дорожку из PlannerModule
        2. Перелететь к начальной точке (GUIDED)
        3. Посадка на крышу (снижение до контакта с лыжами)
        4. Переход в BULLDOZER
        """
        lane = self.planner.get_next_lane(self._completed_lanes)

        if lane is None:
            logger.info("TRANSIT: все дорожки пройдены — RETREAT")
            self.sm.transition_to(State.RETREAT)
            return

        if self.sm.time_in_state < 1.0:
            # Перелёт к начальной точке
            wp = lane.waypoints[0]
            logger.info("TRANSIT: перелёт к дорожке #%d (%.1f, %.1f)",
                        self._completed_lanes + 1, wp.x, wp.y)
            self.mav.goto_local(wp.x, wp.y, -config.TRANSIT_ALTITUDE_M)
            return

        # Заглушка: ожидание прибытия + посадка
        logger.info("TRANSIT: посадка на крышу — переход в BULLDOZER")
        self.mav.land()
        self.sm.transition_to(State.BULLDOZER)

    def _tick_bulldozer(self) -> None:
        """
        Состояние BULLDOZER: активная уборка снега.

        Винты на 90–100% → горизонтальная тяга → толкание к краю.
        Ground Steering через разнотяг моторов.
        """
        elapsed = self.sm.time_in_state

        # Проверка лимита времени
        if elapsed > config.BURST_MAX_DURATION_SEC:
            logger.info("BULLDOZER: таймаут %.0f с — RETREAT", elapsed)
            self._completed_lanes += 1
            self.sm.transition_to(State.RETREAT)
            return

        # Проверка энергии
        if self.power.state == PowerState.LOW_POWER:
            logger.warning(
                "BULLDOZER: низкий заряд (%.1fВ) — RETREAT",
                self.power.voltage,
            )
            self._completed_lanes += 1
            self.sm.transition_to(State.RETREAT)
            return

        # Управление Ground Steering
        lane = self.planner.get_next_lane(self._completed_lanes)
        if lane and len(lane.waypoints) >= 2:
            # Движение по дорожке — полный газ, прямой курс
            self.mav.set_ground_steering(throttle_pct=95.0, yaw_rate_dps=0.0)

        if elapsed < 0.5:
            logger.info(
                "BULLDOZER: начало уборки | V=%.1fВ, E=%.0f кДж (%.0f с макс)",
                self.power.voltage,
                self.power.energy_kj,
                self.power.estimated_burst_time_sec,
            )

    def _tick_retreat(self) -> None:
        """
        Состояние RETREAT: возврат на точку зарядки.

        1. Взлёт (если на крыше)
        2. Перелёт к точке зарядки
        3. Посадка
        4. Начало зарядки → переход в IDLE
        """
        if self.sm.time_in_state < 0.5:
            logger.info("RETREAT: возврат на зарядку")
            self.mav.set_mode("GUIDED")
            self.mav.takeoff(config.TRANSIT_ALTITUDE_M)
            return

        # Заглушка: перелёт + посадка
        if self.sm.time_in_state > 3.0:
            logger.info("RETREAT: посадка, начало зарядки")
            self.mav.land()
            self.mav.disarm()
            self.mav.send_gs_command("START_CHARGE")
            self.power.start_charge()
            self.sm.transition_to(State.IDLE)

    def _handle_emergency(self) -> None:
        """Обработка аварийного состояния."""
        if self.sm.time_in_state < 0.5:
            logger.critical("EMERGENCY: зависание / экстренная посадка")
            self.mav.set_mode("LOITER")
            self.mav.send_gs_command("EMERGENCY_STOP")
        # Оператор должен вмешаться вручную

    # ------------------------------------------------------------------
    # Слушатель переходов
    # ------------------------------------------------------------------

    def _on_state_change(self, old_state: State, new_state: State) -> None:
        """Реакция на переход состояний."""
        logger.info(
            "Состояние: %s → %s (V=%.1fВ, E=%.0f%%)",
            old_state.value, new_state.value,
            self.power.voltage, self.power.energy_percent,
        )

        # При входе в IDLE — начать зарядку
        if new_state == State.IDLE:
            self.mav.send_gs_command("START_CHARGE")
            self.power.start_charge()

        # При входе в BULLDOZER — включить Boost и начать разряд
        if new_state == State.BULLDOZER:
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

    # Обработка сигналов для корректного завершения
    def signal_handler(sig, frame):
        logger.info("Получен сигнал %s — завершение", sig)
        controller.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    controller.start()


if __name__ == "__main__":
    main()
