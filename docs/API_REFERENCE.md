# API Reference — blueos/

Публичный API всех 14 модулей пакета `blueos`. Сгенерировано вручную по
факту прочтения исходников (не автогенерация — при добавлении/изменении
публичных методов держите это в синхронизации, см. `validation_protocol.md`
раздел «Документация»). Приватные методы (`_prefixed`) не документируются
здесь — см. docstring в самом коде.

Условные обозначения: ✅ — есть docstring, ⚠️ — docstring отсутствует или
формальный (обычно потому, что имя метода самодостаточно, см. B-3 в
`BACKLOG.md`).

---

## `blueos.config`

Только константы, без логики. Полный список — см. `CLAUDE.md` раздел
«Key constants» для safety-critical значений; остальные см. исходник.
Разделы: энергоустановка, лётная платформа, tilt controller, snow resistance,
burst vibrator, blade, шасси, навигация, HOVER_BLOW, электробезопасность,
тепловизор, backup power, пороги/таймауты, geofence, MAVLink.

## `blueos.utils`

Общие геометрические функции без состояния.

| Функция | Docstring | Описание |
|---|---|---|
| `point_in_polygon(x, y, vertices)` | ✅ | Ray casting — точка внутри полигона? |
| `in_obstacle_zone(x, y, obstacles, buffer_m=0.0)` | ✅ | Точка в буферной зоне препятствия (ox, oy, radius)? |
| `polygon_area(vertices)` | ✅ | Формула Шнурка. |
| `polygon_centroid(vertices)` | ✅ | Центр масс полигона. |
| `bounding_box(vertices)` | ✅ | `(x_min, y_min, x_max, y_max)`. |

## `blueos.state_machine`

| Символ | Docstring | Описание |
|---|---|---|
| `State` (enum) | ⚠️ | 7 значений: IDLE/SURVEY/TRANSIT/BULLDOZER/HOVER_BLOW/RETREAT/EMERGENCY. |
| `TRANSITIONS` | ⚠️ | `dict[State, set[State]]` — единственный источник истины о допустимых переходах. |
| `StateListener` | ⚠️ | Type alias: `Callable[[State, State], None]`. |
| `StateMachine.state` (property) | ⚠️ | Текущее состояние. |
| `StateMachine.time_in_state` (property) | ⚠️ | Секунд с момента входа в состояние. |
| `StateMachine.history` (property) | ⚠️ | `list[(timestamp, old, new)]`. |
| `StateMachine.add_listener(callback)` | ⚠️ | Подписаться на переходы. |
| `StateMachine.transition_to(new_state) -> bool` | ✅ (inline logic) | Переход с валидацией по `TRANSITIONS`; `False` если недопустим. |
| `StateMachine.force_emergency()` | ⚠️ | Безусловный переход в EMERGENCY, минуя валидацию. |

## `blueos.power_manager`

| Символ | Docstring | Описание |
|---|---|---|
| `PowerState` (enum) | ⚠️ | CHARGING/READY/DISCHARGING/LOW_POWER/CRITICAL. |
| `PowerManager.state/voltage/energy_kj/energy_percent` (properties) | mixed | Телеметрия наземной суперконденсаторной станции. |
| `PowerManager.estimated_burst_time_sec` (property) | ✅ | `energy_kj / MAX_THRUST_KW`. |
| `PowerManager.is_burst_ready` (property) | ✅ | `voltage >= VOLTAGE_BURST_READY`. |
| `PowerManager.update_telemetry(voltage, current, temperature) -> PowerState` | ✅ | Пересчёт энергии (E=½CV²) и состояния. |
| `PowerManager.start_charge()` / `.start_discharge()` | ✅ | Явные переходы CHARGING/DISCHARGING. |
| `PowerManager.discharge_elapsed_sec` / `.charge_elapsed_sec` (properties) | ✅ | Таймеры текущего цикла. |

## `blueos.perception`

| Символ | Docstring | Описание |
|---|---|---|
| `Obstacle` (dataclass) | ✅ | `x, y, radius, label`. |
| `RoofBoundary` (dataclass) | ✅ | Полигон крыши; `.is_valid`, `.contains(x,y)`, `.area()`. См. BACKLOG B-2 (дублирует utils.py). |
| `SnowLayer` (dataclass) | ✅ | `x, y, depth_mm, surface`. |
| `PerceptionModule.roof/obstacles/snow_grid` (properties) | ⚠️ | Текущее состояние восприятия. |
| `PerceptionModule.process_survey_frame(image, lidar_points, drone_position)` | ✅ | Заглушка CV-пайплайна (edge detection, object detection). |
| `PerceptionModule.detect_roof_boundary(lidar_points) -> RoofBoundary` | ✅ | Заглушка (RANSAC/Convex Hull в реальной реализации). |
| `PerceptionModule.detect_obstacles(lidar_points) -> list[Obstacle]` | ✅ | Заглушка. |
| `PerceptionModule.estimate_snow_depth(lidar_points, reference_surface_z) -> list[SnowLayer]` | ✅ | Реальная логика (не заглушка) — разница высот. |
| `PerceptionModule.is_point_safe(x, y) -> bool` | ✅ | Внутри крыши и вне буферных зон препятствий. |

## `blueos.planner`

| Символ | Docstring | Описание |
|---|---|---|
| `Waypoint` / `Lane` / `CleaningPlan` (dataclasses) | ✅ | Точка / полоса / полный план очистки. |
| `PlannerModule.plan` (property) | ⚠️ | Последний сгенерированный план. |
| `PlannerModule.generate_plan() -> CleaningPlan` | ✅ | Boustrophedon-покрытие от `PerceptionModule`. |
| `PlannerModule.get_next_lane(completed_lanes) -> Lane \| None` | ✅ | Следующая невыполненная полоса. |

## `blueos.mavlink_interface`

| Символ | Docstring | Описание |
|---|---|---|
| `DroneTelemetry` / `GroundStationTelemetry` (dataclasses) | ✅ | Снимки телеметрии дрона / наземной станции. |
| `MAVLinkInterface.connect() / .disconnect()` | ✅ | Заглушка pymavlink/dronekit. |
| `.arm() / .disarm() / .set_mode(mode) / .takeoff(alt) / .land()` | ✅ | Команды ArduPilot. |
| `.goto(lat, lon, alt, speed)` / `.goto_local(x, y, z, speed)` | ✅ | GUIDED-навигация (глобальная/NED). |
| `.set_ground_steering(throttle_pct, yaw_rate_dps)` | ✅ | Руление разнотягом моторов в BULLDOZER. |
| `.send_gs_command(command)` | ✅ | Команды наземной станции (START_CHARGE и т.д.). |
| `.update_telemetry()` | ✅ | Заглушка парсинга MAVLink-сообщений. |

## `blueos.tilt_controller`

| Символ | Docstring | Описание |
|---|---|---|
| `SnowType` (enum) | ⚠️ | POWDER/SETTLED/WET/ICE/UNKNOWN — используется и `thermal_analyzer.py`. |
| `TiltController.current_angle/target_angle` (properties) | ⚠️ | Текущий / целевой угол наклона. |
| `.horizontal_thrust_kgf / .vertical_thrust_kgf / .can_takeoff` (properties) | ✅ | Проекции тяги, безопасность взлёта. |
| `.set_target(angle_deg)` | ✅ | Клампится к `[TILT_MIN_DEG, TILT_MAX_DEG]`. |
| `.get_thrust_components(angle_deg) -> (h, v)` | ✅ | Тяга при произвольном угле (не только текущем). |
| `.set_snow_type(snow_type)` | ✅ | Устанавливает target по базовому углу для типа снега. |
| `.compute_optimal_angle(snow_type, snow_depth_mm, ground_speed_ms, motor_current_a) -> float` | ✅ | Полный адаптивный расчёт (глубина + stall boost + safety guard). |
| `.update(dt) -> float` | ✅ | Плавное движение к target с ограничением `TILT_STEP_DEG_PER_SEC`. |
| `.prepare_for_flight() -> float` | ✅ | Возврат target в 0°. |
| `.estimate_resistance(snow_type, depth_mm) -> float` | ✅ | Сопротивление снега (кгс). |
| `.can_handle_snow(snow_type, depth_mm) -> bool` | ✅ | Хватит ли макс. горизонтальной тяги с запасом 20%. |

## `blueos.burst_vibrator`

| Символ | Docstring | Описание |
|---|---|---|
| `BurstMode` (enum) | ⚠️ | CONTINUOUS/PULSED/HAMMER/RAMP. |
| `BurstVibrator.mode/is_enabled/pulse_count` (properties) | ⚠️ | Текущий режим и счётчик импульсов. |
| `.set_mode(mode)` | ✅ | Переключение (no-op если уже в этом режиме). |
| `.get_throttle() -> float` | ✅ | Текущий throttle (0–100%) с учётом фазы цикла. |
| `.get_visibility_window() -> bool` | ✅ | «Окно видимости» во второй половине фазы OFF. |
| `.reset()` | ✅ | Сброс счётчиков и фазы цикла. |
| `.recommend_mode(snow_type_str, depth_mm) -> BurstMode` | ✅ | См. BACKLOG B-1 — рекомендация сейчас не применяется в `main.py`. |

## `blueos.safety_monitor`

**SAFETY-CRITICAL** — см. `CLAUDE.md` раздел «Critical safety rules» перед
любым изменением.

| Символ | Docstring | Описание |
|---|---|---|
| `SafetyState` / `FaultType` (enums) | ⚠️ | OK/WARNING/FAULT/SHUTDOWN; 10 типов неисправностей. |
| `SafetyTelemetry` / `SafetyEvent` (dataclasses) | ✅ | Снимок датчиков / запись в журнале событий. |
| `SafetyMonitor.state/fault/last_fault/is_power_safe/events/telemetry` (properties) | mixed | Текущее состояние и история. |
| `.pre_power_check() -> (bool, str)` | ✅ | 4 проверки перед подачей 400В (заземление, кабель, изоляция, утечка). |
| `.enable_power() / .disable_power(reason)` | ✅ | Замыкание/размыкание контактора. |
| `.emergency_shutdown(fault, message)` | ✅ | Контактор разомкнут первым (см. CLAUDE.md), затем Power Dump + журнал. |
| `.update(telemetry) -> SafetyState` | ✅ | Полный проход всех проверок (GFCI→AFCI→IMD→imbalance→overcurrent→continuity). |
| `.reset_fault() -> bool` | ✅ | Требует прохождения `pre_power_check()` — не сбрасывает вслепую. |
| `get_voltage_alternatives() -> list[dict]` (module function) | ✅ | Справочные данные по альтернативным напряжениям (400/200/120/48В). |

## `blueos.thermal_analyzer`

| Символ | Docstring | Описание |
|---|---|---|
| `ThermalPixel` / `ThermalRegion` / `HiddenObstacle` / `SnowAssessment` (dataclasses) | ✅ | Точка / регион / скрытое препятствие / итоговая оценка. |
| `ThermalAnalyzer.is_ready/last_assessment` (properties) | ⚠️ | Готовность Hailo, последняя оценка. |
| `.initialize() -> bool` | ✅ | Заглушка загрузки HEF-модели + FFC-калибровка. |
| `.process_frame(thermal_frame, lidar_depth_mm, ambient_temp_c) -> SnowAssessment` | ✅ | Полный пайплайн: сегментация → классификация → скрытые препятствия → рекомендация. |

Внутренние методы (`_segment_thermal_map`, `_classify_region`,
`_detect_hidden_obstacles`, `_get_dominant_type`, `_estimate_density`,
`_estimate_coverage`, `_recommend_strategy`) — см. известную мёртвую ветку
SETTLED в `_classify_region()` (CLAUDE.md known issues).

## `blueos.backup_power`

| Символ | Docstring | Описание |
|---|---|---|
| `BackupState` / `BackupType` (enums) | ⚠️ | STANDBY/ACTIVE/CHARGING/DEPLETED/FAULT; LIPO/SUPERCAP/HYBRID. |
| `BackupPowerManager.state/backup_type/voltage/capacity_percent/weight_kg` (properties) | ⚠️ | Снимок состояния резерва. |
| `.is_ready / .emergency_time_remaining_sec / .is_emergency_time_critical` (properties) | ✅ | Готовность и обратный отсчёт (вычисляется из реальной ёмкости, не фикс. константы — см. CLAUDE.md known issues #2). |
| `.update_telemetry(voltage, current, temperature, tether_connected) -> BackupState` | ✅ | Детекция обрыва кабеля → автопереключение. |
| `.request_emergency_landing() -> bool` | ✅ | Сигнал контроллеру о необходимости аварийной посадки. |
| `.get_status_report() -> dict` | ✅ | Полная сводка для телеметрии/логов. |

## `blueos.hover_blow`

| Символ | Docstring | Описание |
|---|---|---|
| `BlowPoint` / `HoverBlowPlan` (dataclasses) | ✅ | Точка обдува / план (с `.is_complete`, `.progress_percent`, `.current_point`). |
| `HoverBlowController.is_active/plan/throttle/altitude` (properties) | ⚠️ | Текущее состояние контроллера. |
| `.generate_plan(roof_vertices, obstacles) -> HoverBlowPlan` | ✅ | Сетка точек внутри полигона, буструфедон-порядок, минус буферные зоны. |
| `.start() / .stop()` | ✅ | Запуск/остановка выполнения плана. |
| `.update(dt) -> (x, y, z, point_done)` | ✅ | Тик выполнения — таймер dwell на точке, переход к следующей. |
| `.adjust_for_snow_depth(depth_mm)` | ✅ | Подбор высоты/throttle по глубине снега (3 диапазона). |

## `blueos.main` — `BoreasController`

Оркестратор всех 11 модулей + FSM. Полный список `_tick_*` методов
соответствует состояниям `State` 1:1 (`_tick_idle`, `_tick_survey`,
`_tick_transit`, `_tick_bulldozer`, `_tick_hover_blow`, `_tick_retreat`,
`_handle_emergency`). Публичный API:

| Метод | Docstring | Описание |
|---|---|---|
| `BoreasController(connection_string, simulate)` | ✅ | Конструктор — создаёт все 11 под-модулей. |
| `.start()` | ✅ | Инициализация тепловизора → MAVLink connect → pre-power check → главный цикл. |
| `.stop()` | ✅ | Безопасное отключение питания, disarm, disconnect. |
| `main()` (module function, CLI entry) | ✅ | `argparse` + сигнал-хендлеры SIGINT/SIGTERM → `BoreasController.start()`. |

---

## Как поддерживать этот файл в актуальном состоянии

Согласно `validation_protocol.md` (раздел «Документация»): при добавлении
нового публичного метода/класса в `blueos/*.py` — добавь строку в
соответствующую таблицу выше в том же PR/коммите. Не обязательно
документировать приватные (`_prefixed`) методы здесь — они документируются
docstring'ами в самом коде.
