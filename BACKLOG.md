# BACKLOG.md — Boreas BlueOS

Рабочий бэклог задач. Формат: статус, краткое описание, ссылка на файл/строку,
приоритет. Завершённые пункты не удаляются — переносятся в раздел «Done»,
чтобы сохранить историю решений (см. также `state_journal.md` для журнала
по итерациям).

## In Progress / Todo

| # | Приоритет | Задача | Где | Заметка |
|---|---|---|---|---|
| B-1 | Средний | `_burst_mode` (рекомендация `BurstVibrator.recommend_mode()`) вычисляется в `_tick_bulldozer()`, но не применяется — `BurstMode.RAMP` всегда переключается на `PULSED` при завершении разгона (`BurstVibrator._compute_ramp()`), независимо от рекомендации. Из-за этого `BurstMode.HAMMER` (для WET/ICE снега) недостижим через штатный путь BULLDOZER. | `blueos/main.py:414`, `blueos/burst_vibrator.py:_compute_ramp()` | Нужно решить: либо `_compute_ramp()` принимает целевой пост-рамп режим параметром, либо `_tick_bulldozer()` вызывает `set_mode(_burst_mode)` напрямую после рампы. Требует новых тестов на HAMMER-путь через `BoreasController`. |
| B-2 | Низкий | `RoofBoundary.contains()`/`.area()` в `perception.py` дублируют логику `point_in_polygon`/`polygon_area` из `blueos/utils.py` вместо того чтобы использовать их. `planner.py` тоже не использует `utils.py`. Только `hover_blow.py` мигрирован (см. CLAUDE.md known issues #1). | `blueos/perception.py` (класс `RoofBoundary`), `blueos/planner.py` | Рефакторинг: `RoofBoundary.contains()` → `point_in_polygon(x, y, self.vertices)`, `.area()` → `polygon_area(self.vertices)`. Тесты уже покрывают оба поведения 1:1, так что рефакторинг безопасен, но требует прогона полного набора после изменения. |
| B-3 | Низкий | Docstring-аудит: часть публичных методов (`__init__` большинства классов, некоторые properties) не имеют docstring — стиль в проекте смешанный (модули и большинство методов документированы подробно на русском, но не 100% методов). | Весь `blueos/` | Не блокирует функциональность; см. `docs/API_REFERENCE.md` для текущего состояния документированности по модулям. |
| B-4 | Низкий | Нет `Dockerfile` / контейнеризации — `validation_protocol.md` содержит раздел-заглушку "Чек-лист перед сборкой контейнера", который активируется только когда появится `Dockerfile`. | — | Дождаться явного запроса на контейнеризацию, не начинать превентивно. |
| B-5 | Информационный | KiCad: пользователь запросил начать разработку корпуса и разъёмов под "стандартные выбранные платы" в KiCad, с привязкой к 12-фазному ТЗ. Требуется уточнение конкретного списка плат/разъёмов перед началом (см. известные из CLAUDE.md компоненты: Cube Orange+, FLIR Lepton 3.5 breakout, Hailo-8L M.2, Maxwell BMOD0165 P048 ×3, BCAP3000 ×6, LTC4357 ORing) — нет инструмента для рендеринга/валидации `.kicad_pcb` в этой среде, только ручное создание текстовых файлов формата KiCad, что рискованно без возможности открыть и проверить в самом KiCad. | — | Ждёт уточняющего вопроса пользователю о финальном перечне плат/разъёмов, либо явного согласия работать "вслепую" с пониманием риска невалидных файлов. |

## Done

| # | Задача | Итерация |
|---|---|---|
| D-1 | Покрытие тестами `blueos/` доведено с ~31% до 100% (395 тестов): написаны тесты для `utils.py`, `burst_vibrator.py`, `power_manager.py`, `mavlink_interface.py`, `perception.py`, `planner.py`, `hover_blow.py`, `thermal_analyzer.py`, `main.py` (`BoreasController`), плюс добор пробелов в `backup_power.py`, `safety_monitor.py`, `tilt_controller.py`, `state_machine.py`. | Итерация 1 |
| D-2 | Найден и исправлен реальный баг: `SURVEY → RETREAT` отсутствовал в `TRANSITIONS` (`state_machine.py`) — неудачная генерация плана в SURVEY навсегда подвешивала контроллер. | Итерация 1 |
| D-3 | `# pragma: no cover` с обоснованием на 2 доказанно недостижимых ветках: `thermal_analyzer._classify_region()` (SETTLED, из-за пересечения диапазонов конфига) и `tilt_controller.compute_optimal_angle()` (vertical-thrust guard, физически недостижим при текущих константах). Плюс 2 defensive `except Exception` (`mavlink_interface.connect()`, main `__main__` guard). | Итерация 1 |
| D-4 | Создан автономный протокол работы: `state_journal.md`, `validation_protocol.md`, `.claudeignore`, `context_map.json`, `docs/ai_autonomy_lifehacks.md` (98 практик + золотое правило), раздел "Autonomous Operation Protocol" в `CLAUDE.md`. | Итерация 1 |
| D-5 | Код приведён в соответствие с PEP8: `ruff check` — 0 замечаний (было 22, включая 2 реальные "мёртвые" переменные — см. B-1 для одной из них, вторая была безопасным упрощением). | Итерация 2 |
| D-6 | Типизация: `mypy --strict --ignore-missing-imports` — 0 ошибок (было 7: отсутствующие type-параметры у `list`/`dict`, неаннотированные параметры функций, некорректный тип для `max(key=...)`). | Итерация 2 |
| D-7 | CI-порог покрытия поднят с 70% до 99% (`--cov-fail-under=99` в `.github/workflows/ci.yml`), `CLAUDE.md` обновлён. | Итерация 2 |
| D-8 | Создана `docs/API_REFERENCE.md` — публичный API всех 14 модулей `blueos/` (классы, методы, свойства, что документировано / что нет). | Итерация 2 |
