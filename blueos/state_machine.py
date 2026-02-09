"""
Конечный автомат (State Machine) режимов работы дрона Борей.

Состояния:
    IDLE       — Ожидание на земле, зарядка ионисторов
    SURVEY     — Облёт крыши, построение карты поверхности
    TRANSIT    — Перелёт к точке начала очередной дорожки
    BULLDOZER  — Активная уборка снега (толкание к краю)
    RETREAT    — Возврат на точку зарядки
    EMERGENCY  — Аварийный режим (отстыковка отвала, зависание)
"""

import enum
import logging
import time

logger = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "IDLE"
    SURVEY = "SURVEY"
    TRANSIT = "TRANSIT"
    BULLDOZER = "BULLDOZER"
    RETREAT = "RETREAT"
    EMERGENCY = "EMERGENCY"


# Допустимые переходы между состояниями
TRANSITIONS: dict[State, set[State]] = {
    State.IDLE: {State.SURVEY, State.TRANSIT, State.EMERGENCY},
    State.SURVEY: {State.TRANSIT, State.IDLE, State.EMERGENCY},
    State.TRANSIT: {State.BULLDOZER, State.RETREAT, State.EMERGENCY},
    State.BULLDOZER: {State.RETREAT, State.TRANSIT, State.EMERGENCY},
    State.RETREAT: {State.IDLE, State.EMERGENCY},
    State.EMERGENCY: {State.IDLE},
}


class StateMachine:
    """Конечный автомат режимов работы Борея."""

    def __init__(self) -> None:
        self._state = State.IDLE
        self._state_enter_time = time.monotonic()
        self._listeners: list = []

    @property
    def state(self) -> State:
        return self._state

    @property
    def time_in_state(self) -> float:
        """Время (с) пребывания в текущем состоянии."""
        return time.monotonic() - self._state_enter_time

    def add_listener(self, callback) -> None:
        """Добавить слушателя переходов. callback(old_state, new_state)."""
        self._listeners.append(callback)

    def transition_to(self, new_state: State) -> bool:
        """
        Попытка перехода в новое состояние.

        Возвращает True если переход выполнен, False если переход недопустим.
        """
        if new_state == self._state:
            return True

        allowed = TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            logger.warning(
                "Переход %s → %s недопустим. Допустимые: %s",
                self._state.value,
                new_state.value,
                [s.value for s in allowed],
            )
            return False

        old_state = self._state
        self._state = new_state
        self._state_enter_time = time.monotonic()

        logger.info("Переход: %s → %s", old_state.value, new_state.value)

        for cb in self._listeners:
            try:
                cb(old_state, new_state)
            except Exception:
                logger.exception("Ошибка в listener при переходе состояний")

        return True

    def force_emergency(self) -> None:
        """Принудительный переход в EMERGENCY из любого состояния."""
        old = self._state
        self._state = State.EMERGENCY
        self._state_enter_time = time.monotonic()
        logger.critical("EMERGENCY из состояния %s", old.value)
        for cb in self._listeners:
            try:
                cb(old, State.EMERGENCY)
            except Exception:
                logger.exception("Ошибка в listener при EMERGENCY")
