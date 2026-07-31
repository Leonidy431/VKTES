"""
Интерфейс MAVLink / DroneKit — связь с полётным контроллером (Cube Orange+)
и наземной станцией.

Обеспечивает:
- Подключение к ArduPilot через MAVLink
- Отправку команд управления (ARM, MODE, GUIDED waypoints)
- Приём телеметрии (GPS, барометр, напряжение, ток)
- Управление режимом Ground Steering (руление через разнотяг моторов)
"""

import logging
from dataclasses import dataclass

from . import config

logger = logging.getLogger(__name__)


@dataclass
class DroneTelemetry:
    """Телеметрия дрона."""
    lat: float = 0.0             # градусы
    lon: float = 0.0             # градусы
    alt_m: float = 0.0           # м — высота над точкой взлёта
    heading_deg: float = 0.0     # градусы — курс
    ground_speed_ms: float = 0.0 # м/с — скорость над землёй
    battery_voltage: float = 0.0 # В — напряжение бортовой шины
    battery_current: float = 0.0 # А — ток потребления
    armed: bool = False
    mode: str = "STABILIZE"
    gps_fix: int = 0             # 0=нет, 3=3D fix


@dataclass
class GroundStationTelemetry:
    """Телеметрия наземной станции (через кабель)."""
    supercap_voltage: float = 0.0    # В
    supercap_current: float = 0.0    # А
    supercap_energy_kj: float = 0.0  # кДж
    supercap_temp_c: float = 20.0    # °C
    charger_active: bool = False
    boost_active: bool = False
    tether_connected: bool = False


class MAVLinkInterface:
    """
    Обёртка MAVLink для управления дроном Борей.

    В реальной реализации использует pymavlink или dronekit.
    Здесь — каркас API с заглушками.
    """

    def __init__(self, connection_string: str | None = None) -> None:
        self._connection_string = connection_string or config.MAVLINK_CONNECTION
        self._connected = False
        self._drone_telemetry = DroneTelemetry()
        self._gs_telemetry = GroundStationTelemetry()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def drone(self) -> DroneTelemetry:
        return self._drone_telemetry

    @property
    def ground_station(self) -> GroundStationTelemetry:
        return self._gs_telemetry

    def connect(self) -> bool:
        """Установить соединение с полётным контроллером."""
        logger.info("Подключение к MAVLink: %s", self._connection_string)
        try:
            # Заглушка: pymavlink.mavutil.mavlink_connection(...)
            self._connected = True
            logger.info("MAVLink подключён")
            return True
        except Exception:  # pragma: no cover
            # Defensive: the stub above cannot actually raise; kept for when
            # this is replaced with a real pymavlink connection call.
            logger.exception("Ошибка подключения MAVLink")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Отключиться от полётного контроллера."""
        self._connected = False
        logger.info("MAVLink отключён")

    # ------------------------------------------------------------------
    # Команды управления полётом
    # ------------------------------------------------------------------

    def arm(self) -> bool:
        """Армирование моторов."""
        if not self._connected:
            logger.error("arm(): нет подключения")
            return False
        logger.info("ARM: включение моторов")
        # MAV_CMD_COMPONENT_ARM_DISARM
        self._drone_telemetry.armed = True
        return True

    def disarm(self) -> bool:
        """Дизармирование моторов."""
        logger.info("DISARM: выключение моторов")
        self._drone_telemetry.armed = False
        return True

    def set_mode(self, mode: str) -> bool:
        """
        Установить режим полёта ArduPilot.

        Поддерживаемые режимы:
        - STABILIZE: ручное управление
        - GUIDED: автоматический полёт по точкам
        - LAND: автоматическая посадка
        - LOITER: удержание позиции
        """
        logger.info("Установка режима: %s", mode)
        self._drone_telemetry.mode = mode
        return True

    def takeoff(self, altitude_m: float) -> bool:
        """Вертикальный взлёт на заданную высоту."""
        if not self._drone_telemetry.armed:
            logger.error("takeoff(): дрон не армирован")
            return False
        logger.info("Взлёт на высоту %.1f м", altitude_m)
        return True

    def land(self) -> bool:
        """Автоматическая посадка."""
        logger.info("Посадка")
        return self.set_mode("LAND")

    def goto(self, lat: float, lon: float, alt: float, speed: float = 1.0) -> bool:
        """Перелёт в указанную точку (GUIDED)."""
        logger.info("GOTO: (%.6f, %.6f) alt=%.1f м, speed=%.1f м/с",
                     lat, lon, alt, speed)
        return True

    def goto_local(self, x: float, y: float, z: float, speed: float = 0.5) -> bool:
        """
        Движение в локальных координатах (NED) — для работы на крыше.

        x: метры вперёд (North)
        y: метры вправо (East)
        z: метры вниз (Down, отрицательное = вверх)
        """
        logger.info("GOTO_LOCAL: (%.2f, %.2f, %.2f) speed=%.1f м/с", x, y, z, speed)
        return True

    def set_ground_steering(self, throttle_pct: float, yaw_rate_dps: float) -> bool:
        """
        Режим Ground Steering — руление на земле через разнотяг моторов.

        throttle_pct: 0–100% — общая мощность (горизонтальная тяга)
        yaw_rate_dps: градусов/с — скорость разворота (>0 = вправо)

        Используется в режиме BULLDOZER.
        """
        logger.debug("Ground Steering: throttle=%.0f%%, yaw=%.1f°/с",
                      throttle_pct, yaw_rate_dps)
        return True

    # ------------------------------------------------------------------
    # Команды наземной станции
    # ------------------------------------------------------------------

    def send_gs_command(self, command: str) -> bool:
        """
        Отправить команду наземной станции через MAVLink.

        Команды:
        - START_CHARGE: начать зарядку ионисторов
        - STOP_CHARGE: остановить зарядку
        - ENABLE_BOOST: включить HV Boost Converter
        - DISABLE_BOOST: выключить Boost
        - EMERGENCY_STOP: аварийное отключение
        """
        logger.info("Команда наземной станции: %s", command)
        return True

    # ------------------------------------------------------------------
    # Обновление телеметрии
    # ------------------------------------------------------------------

    def update_telemetry(self) -> None:
        """
        Прочитать и обновить телеметрию дрона и наземной станции.

        Вызывается в главном цикле с частотой TELEMETRY_RATE_HZ.
        """
        # Заглушка: в реальной реализации — парсинг MAVLink-сообщений
        pass
