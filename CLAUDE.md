# CLAUDE.md — Boreas BlueOS

Guide for AI assistants working on this repository.

## Project overview

Boreas is a semi-autonomous tethered octocopter (X8, 50 kgf thrust) that clears snow
from rooftops using a V-plow bulldozer blade and an optional hover-blow mode.
Power comes via a 400 V DC tether from a ground-based supercapacitor station (3× Maxwell
BMOD0165 P048, 495 F/48 V, ~570 kJ). The companion computer runs **BlueOS** — a Python 3.11
control module on a Cube Orange+ flight controller (ArduPilot).

## Autonomous Operation Protocol

This project treats autonomous AI work as requiring an explicit state/validation
harness, not just a knowledge base. Four files implement this, and every
session must use them:

| File | Purpose | When to touch it |
|---|---|---|
| `state_journal.md` | Append-only log of what was done, what's unresolved, and the single next step | End of every iteration — before moving to the next backlog item |
| `validation_protocol.md` | Hard checklist (lint → format → mypy → tests → safety tests → coverage) | Before considering ANY code change done |
| `.claudeignore` | Filters caches/binaries/artifacts out of what the agent reads | Passive — respect it, extend it if new artifact types appear |
| `context_map.json` | Static import-dependency graph between `blueos/*.py` modules | Consult before editing a widely-imported module (e.g. `config.py`, `tilt_controller.py`); regenerate if module imports change |

Full rationale and the underlying 98 practices + golden rule are in
`docs/ai_autonomy_lifehacks.md`. The one rule that overrides convenience:

> **Golden rule (Rule 99):** if your internal confidence that a change will
> work is below 90%, stop, write no code, and ask exactly one precise
> question instead of guessing. Do not "try and see" on safety-critical
> files (`safety_monitor.py`, anything touching the 400 V tether path).

## Architecture — 11 BlueOS modules

| Module | File | Role |
|---|---|---|
| StateMachine | `blueos/state_machine.py` | 7 states: IDLE/SURVEY/TRANSIT/BULLDOZER/HOVER_BLOW/RETREAT/EMERGENCY |
| PowerManager | `blueos/power_manager.py` | Supercap charge/discharge cycle management |
| PerceptionModule | `blueos/perception.py` | LiDAR + camera → roof polygon, obstacles, snow depth |
| PlannerModule | `blueos/planner.py` | Boustrophedon lane generation |
| MAVLinkInterface | `blueos/mavlink_interface.py` | ArduPilot GUIDED/POSHOLD, servo control |
| TiltController | `blueos/tilt_controller.py` | Adaptive frame tilt 0–30° at 5°/s |
| BurstVibrator | `blueos/burst_vibrator.py` | Pulsed thrust for compacted snow breakup |
| SafetyMonitor | `blueos/safety_monitor.py` | 400 V DC: GFCI/IMD/AFCI/Power Dump (20 Hz) |
| ThermalAnalyzer | `blueos/thermal_analyzer.py` | FLIR Lepton 3.5 + Hailo-8L snow classification |
| BackupPowerManager | `blueos/backup_power.py` | LiPo 6S / BCAP3000×6 / HYBRID; ORing <5 ms |
| HoverBlowController | `blueos/hover_blow.py` | Hover grid for powder snow, no roof contact |

Shared geometry (point_in_polygon, in_obstacle_zone, polygon_area) lives in `blueos/utils.py`.
All constants are in `blueos/config.py`.

## Critical safety rules

- **Never modify `safety_monitor.py`** without cross-checking against IEC 61010 / IEC 60479
  and verifying the GFCI 30 mA / 30 ms and IMD ≥100 kΩ thresholds are preserved.
- The `SafetyMonitor.emergency_shutdown()` path must keep the contactor-open call first —
  hardware interlock before software logging, always.
- `SafetyMonitor.reset_fault()` must require operator confirmation. Never auto-reset.
- The `SAFETY_CHECK_RATE_HZ = 20` (50 ms) is a hard real-time requirement. Do not add
  blocking I/O or inference calls inside `_check_safety()`.

## Development workflow

- Branch naming: `claude/<short-description>` or `dev/<feature>`.
- All changes must pass `ruff check` + `mypy --strict` before push.
- Run `pytest tests/ -m "not safety"` for fast feedback; run `pytest -m safety` separately
  for 400 V safety-critical tests.
- Mark new safety-critical tests with `@pytest.mark.safety`.
- **Git push budget: max 2 pushes per day.** Batch commits locally, run the full
  `validation_protocol.md` checklist once, then push. Do not push after every small edit —
  push only when a self-contained unit of work is validated and ready.
- **Never push or attempt a merge while any step of `validation_protocol.md` is red.**
  Fix ruff/mypy/test/coverage failures first; only a fully green run may be pushed.
- **Known git gotcha:** in a fresh container/session, the local clone can start with the
  remote configured but zero commits checked out (`git status` reports "No commits yet").
  This is not repo corruption — recover with
  `git fetch origin <branch> && git checkout -B <branch> origin/<branch>` before assuming
  anything is broken or re-doing work that already exists on the remote.
- This repository's remote may not have a `main`/`master` branch — verify with
  `git ls-remote origin` before assuming a merge target exists. If it doesn't, merging
  requires the operator to specify (or create) a target branch first.
- Never add `time.sleep()` to the main 10 Hz loop.
- Full step-by-step gate before calling any change done: `validation_protocol.md`.

## Key constants — do not change without updating README

```
TILT_MAX_DEG = 30.0               # °  — max tilt
TILT_STEP_DEG_PER_SEC = 5.0       # °/s — rate limit
SAFETY_GFCI_TRIP_MA = 30.0        # mA — GFCI trip threshold
SAFETY_GFCI_TRIP_TIME_MS = 30     # ms — trip time
SAFETY_INSULATION_MIN_KOHM = 100.0
HOVER_BLOW_ALTITUDE_M = 1.5       # m
BACKUP_EMERGENCY_FLIGHT_SEC = 45  # s  — LIPO/HYBRID budget
```

## Known issues

1. **`_point_in_polygon` duplication** — was present in `perception.py`, `planner.py`,
   and `hover_blow.py`. Fixed in v0.2.1: all three now import from `blueos/utils.py`.
   Check for further copies before adding new modules.

2. **BCAP3000×6 series capacity** — `C_series = C_single / N = 3000 / 6 = 500 F`,
   `V_max = 6 × 2.7 = 16.2 V`, energy ≈ 18.2 Wh → **~13 s** hover time (not 45 s).
   Fixed in v0.2.1: `BackupPowerManager._max_flight_sec` is now computed from actual
   capacity, not the fixed `BACKUP_EMERGENCY_FLIGHT_SEC` constant.

## Testing

```bash
# Fast unit tests (no safety marker)
pytest tests/ -m "not safety" --tb=short -q

# Safety-critical 400 V tests
pytest tests/test_safety_monitor.py -m safety -v

# Full suite with coverage
pytest tests/ --cov=blueos --cov-report=term-missing
```

Coverage target: ≥99 % on `blueos/` (currently 100%). CI gate enforced in
`.github/workflows/ci.yml`. A handful of genuinely unreachable defensive
branches are marked `# pragma: no cover` with an inline note explaining why
(e.g. dead code from overlapping config ranges, stub exception handlers) —
don't add a pragma to hide a real gap; only for lines that cannot execute
given the current config/architecture.

## Algorithm Selection & Validation — 12-Phase HLD

**Rule: Every algorithm implementation follows evidence-based selection via multi-expert consensus.**

When implementing algorithms (pathfinding, snow detection, tilt control, etc.):

### Phase 1–3: Research & Literature Review
- Search **PubMed** / **Google Scholar** for peer-reviewed papers on the problem
- Collect ≥5 papers from domain: robotics, autonomous systems, control theory, snow/ice physics
- Document findings in `docs/algorithm_research/{feature_name}.md`
  - Citations with DOI
  - Key parameters & thresholds from papers
  - Limitations noted by authors

### Phase 4–6: Candidate Algorithm Pool
- Identify ≥3 candidate algorithms from literature or well-known approaches
- Example: For snow depth estimation:
  - Candidate A: LiDAR height subtraction (simple, fast)
  - Candidate B: Convolutional depth estimation (accurate, slow)
  - Candidate C: Fusion of LiDAR + thermal + video (comprehensive, complex)

### Phase 7–9: Expert Evaluation Matrix (48 Parameters)
Assemble **N ≥ 4 domain specialists** (real or simulated AI agents). Score each candidate on:

**Core Performance (12 params):**
- Accuracy vs. ground truth (RMSE/MAE)
- Latency (ms)
- Throughput (ops/sec)
- CPU/RAM utilization (%)
- Power consumption (W)
- Noise robustness (dB SNR)
- Temperature range coverage (-40°C to +40°C)
- Tuning difficulty (1–10)
- Scalability (computational complexity)
- Reproducibility (variance across runs)
- Failure mode severity
- Recovery time

**Integration & Safety (12 params):**
- Real-time guarantees (hard/soft/none)
- Thread safety
- Memory leaks risk
- GFCI/electrical safety impact
- Hardware dependencies (list)
- Sensor availability
- Fallback behavior
- Cross-module coupling

**Operational (12 params):**
- Training data requirements (hours)
- Calibration time (minutes)
- Weather robustness (sleet, fog, wind)
- Maintenance frequency
- Debugging difficulty
- Field adaptability
- Cost of compute hardware
- Certification readiness (CE/IEC)

**Domain-Specific (12 params):**
For snow/tilt/thermal:
- Snow type sensitivity (powder/settled/wet/ice)
- Altitude/gravity effects
- Tether electromagnetic interference (EMI)
- Roof surface variation (metal/concrete/shingles)
- Wind loading interaction
- Thermal camera FOV limitations
- Hailo-8L latency constraints
- Drift over 8-hour flight

### Phase 10: Consensus Vote
- Each specialist assigns **score ∈ [0, 10]** per parameter
- Candidates ranked by **weighted mean**: favored parameters weighted higher
- **Tie-breaking rule**: Lower computational cost + smaller code footprint wins
- **Disagreement threshold**: If specialists diverge >3 points on any param, require discussion

### Phase 11: Proof of Concept (PoC)
- Implement top-2 candidates in isolated `blueos/experimental/{algo_name}.py`
- Run against test dataset (synthetic + real field data)
- Document performance vs. predicted matrix scores
- If reality diverges >15% from matrix, revisit Phase 7–9

### Phase 12: Deployment & Documentation
- Lock chosen algorithm in production code
- Store decision record: `docs/algorithm_decisions/{feature}.json`
  ```json
  {
    "feature": "snow_depth_estimation",
    "chosen_algorithm": "LiDAR + thermal fusion",
    "decision_date": "2026-07-24",
    "specialists": ["roboticist", "CV-expert", "embedded-systems", "snow-physicist"],
    "matrix_file": "docs/algorithm_research/snow_depth_matrix.csv",
    "papers_cited": ["https://doi.org/10.1016/...", "https://arxiv.org/..."],
    "scores": {"Candidate A": 7.2, "Candidate B": 8.1, "Candidate C": 9.4},
    "rationale": "Best balance of real-time + accuracy + power; lower EMI sensitivity"
  }
  ```

---

## Useful skills (alirezarezvani/claude-skills)

When working on specific areas of this codebase, these skills add targeted expertise:

| Area | Recommended skill |
|---|---|
| Safety-critical code review | `security-guidance` |
| Fault injection / chaos testing | `chaos-engineering` |
| Embedded Python optimisation | `karpathy-coder` |
| Reliability / SLO targets | `slo-architect` |
| CI/CD pipeline design | `workflow-builder` |
| Physics / domain validation | General research + `deep-research` |
| Algorithm evaluation & papers | `deep-research` (multi-modal sweep) |
| Expert consensus modeling | General-purpose agent (simulate N specialists) |



99 лайфхаков автономного кодирования
Результат синтеза дискуссии команды из 12 экспертов (Симпозиум
Перипатетиков) о том, чего не хватает Клоду для полной автономии сверх
ТЗ, backlog, `CLAUDE.md` и `.clauderc`. Отобраны по 7 параметрам:
сжатие контекста, детерминированность, самоисцеление, управление
состоянием, токен-эффективность, интеграция цепочек инструментов,
изоляция песочницы — по 14 правил на параметр (98) + 1 золотое
правило автономии (99).
Это операционный документ: правила здесь предполагается выполнять,
а не читать как манифест. Родственные артефакты, на которые эти
правила ссылаются: `state\\\_journal.md`, `validation\\\_protocol.md`,
`.claudeignore`, `context\\\_map.json`, `.clauderc`.
---
Параметр 1: Сжатие контекста (Context Density) — 1–14
Никогда не проси переписать весь файл — требуй выводить только diff или изменённые функции.
Используй XML-теги (`<core\\\_logic>`, `<UI>`) для структурирования `CLAUDE.md` — Клод читает XML надёжнее, чем Markdown с неоднозначной вложенностью.
Разделяй ТЗ на горячий (актуальный спринт) и холодный (общая архитектура) контекст.
Запрети Клоду извиняться и писать введения («Конечно, вот ваш код») — только код и сухие логи.
Регулярно запускай промпт: «Сделай summary нашего диалога в 500 токенах, я начну новую сессию с ним».
Храни громоздкие данные (сложные логи, дампы БД) в сжатом виде или передавай только заголовки.
Используй аббревиатуры для частых команд (например, `\\\[V]` — validate, `\\\[R]` — refactor). Пропиши это в `.clauderc`.
Выноси историю успешных слияний веток и настройки merge-guardrails в отдельный справочник, не держи их в активном ТЗ.
Передавай структуру проекта через `tree -I 'node\\\_modules|.git'`, а не копированием путей вручную.
Если контекст переполнен, требуй ответа в формате JSON — это заставляет модель сжимать смысл.
Ограничь глубину анализа старого кода тремя уровнями импортов (см. `context\\\_map.json`).
Требуй удалять комментарии в генерируемом коде, если они дублируют название очевидной функции.
Настрой скрипт, который перед отправкой контекста Клоду вырезает все пустые строки и лишние табы из логов.
Запрети анализировать файлы, которые не менялись более месяца, если задача не касается рефакторинга ядра.
Параметр 2: Детерминированность (Determinism) — 15–28
Всегда задавай роль до передачи задачи: «Ты — Senior Cloud Architect...».
Указывай конкретную версию языка и фреймворка (например, Python 3.11, FastAPI 0.110).
Требуй пошагового рассуждения (Chain of Thought) через тег `<thought\\\_process>` строго до написания кода.
Используй негативные промпты: чётко пиши, какие библиотеки и паттерны использовать нельзя.
Введи штрафные баллы в системном промпте: «Если ты используешь `any`/`Any` без обоснования — задача считается проваленной».
Заставляй Клода писать псевдокод перед боевой реализацией сложной логики.
Пропиши в `.clauderc` стиль именования переменных (`snake\\\_case` для Python, см. `CLAUDE.md`) без права на отклонение.
Требуй явно указывать сложность алгоритма (Big O) в комментариях к циклам, работающим с большими массивами.
Описывай краевые случаи (edge cases) в ТЗ списком Given-When-Then.
Запрети использование устаревших API (deprecated-библиотек, старых версий Docker-синтаксиса).
Укажи жёсткий формат вывода ошибок: `\\\[ERROR] -> \\\[CAUSE] -> \\\[SOLUTION]`.
Внедряй «стоп-слова», при генерации которых Клод должен прервать текущий подход и запросить помощь человека.
Требуй обязательного возврата к исходной архитектуре в `CLAUDE.md` перед принятием каждого архитектурного решения.
Требуй 100% покрытия типов в сигнатурах функций до реализации их тел.
Параметр 3: Самоисцеление (Error Recovery / Self-healing) — 29–42
Внедри промпт-рефлексию: «Прочти свой код. Найди в нём 3 потенциальные уязвимости. Исправь их».
Заставь Клода писать тесты на отказ (failure tests) до написания самого функционала.
При получении ошибки в консоли отправляй её Клоду вместе с командой: «Объясни причину, не предлагая код, затем предложи 2 пути решения».
Обучи Клода паттерну Circuit Breaker в контексте его собственной работы: если он 3 раза не угадал с кодом, он должен попросить дополнительный контекст.
Требуй от Клода создания fallback'ов для любых сетевых запросов.
Запрети «тихое падение» — все перехваченные исключения должны логироваться с контекстом.
В случае конфликта слияния (merge conflict) требуй пошаговый план разрешения, а не финальный файл.
При падении тестов заставляй ИИ анализировать стек трейс с конца (от самого глубокого вызова).
Создай алиас `debug\\\_deep`: команда для Клода развернуть одну строку кода в несколько с промежуточными логами.
Требуй, чтобы любые миграции БД или изменения схем сопровождались скриптами rollback (см. `validation\\\_protocol.md` §4).
Если Клод предлагает решение, которое ломает CI/CD пайплайн, он обязан сначала написать патч для пайплайна.
Настрой Клода на проверку race conditions при работе с асинхронными cron-задачами.
Обяжи агента проверять лимиты памяти (OOM) в контейнерах при генерации скриптов обработки больших данных.
Научи Клода распознавать циклические зависимости и превентивно выносить общую логику в абстракции.
Параметр 4: Управление состоянием (State Management) — 43–56
Клод должен начинать каждый ответ с тега `<current\\\_state>`, где описывает, на каком этапе бэклога находится.
Передавай `git status` перед каждой сложной задачей, чтобы ИИ видел несохранённые изменения.
Заведи концепцию Checkpoint — успешный билд. Если ИИ запутался, дай команду «Rollback to Checkpoint X».
Требуй от Клода вести `todo\\\_temp.md` для мелких подзадач внутри одной крупной фичи.
Запрети изменение глобального состояния (глобальных переменных) без явного разрешения в `CLAUDE.md`.
Обновляй `state\\\_journal.md` только после успешного прохождения всех линтеров (см. `validation\\\_protocol.md`).
Внедри команду `\\\[STATE DUMP]`, по которой Клод выводит все переменные окружения, которые, по его мнению, сейчас активны.
Требуй изоляции побочных эффектов (side effects) в отдельные, легко тестируемые модули.
Научи агента управлять состояниями конечных автоматов (FSM) при проектировании сложных UI или API.
При работе с облачными БД Клод должен явно описывать схему транзакции до её реализации.
Используй идемпотентные скрипты — если Клод запускает код дважды, система не должна сломаться.
Требуй логирования изменения состояний на уровне API-эндпоинтов для отслеживания цепочки событий.
В конце сессии Клод должен сгенерировать скрипт инициализации среды (bash), чтобы следующий сеанс стартовал с той же точки.
Разделяй состояния «Dev», «Test» и «Prod» — Клод должен всегда уточнять, в каком состоянии он оперирует.
Параметр 5: Токен-эффективность (Token Efficiency) — 57–70
Замени развёрнутые промпты на шорткоды: `Refactor(performance, CPU)` вместо длинных объяснений.
Используй `.claudeignore` максимально агрессивно (исключи svg, лок-файлы, csv-дампы).
Вместо передачи всего файла передавай только интерфейсы (types/schemas) и сигнатуры функций.
Приказывай вырезать из ответов сниппеты импортов, если они не менялись.
Используй английский язык для системных промптов и служебного ТЗ — он токенизируется в 2–3 раза плотнее кириллицы.
Требуй использовать тернарные операторы и короткие синтаксические конструкции там, где это не вредит читаемости.
Схлопывай повторяющиеся блоки кода в ТЗ через `... (аналогично блоку А) ...`.
При анализе JSON-данных скармливай Клоду только 2–3 объекта из массива, а не весь массив из тысяч строк.
Запрети генерацию `README.md`, пока проект не готов на 90% — это пустая трата токенов на промежуточных этапах.
Установи лимит длины ответа Клода для промежуточных шагов.
Если нужно найти баг в длинном логе — сжимай лог регулярками, удаляя таймстемпы и INFO-сообщения.
В `.clauderc` пропиши: «No pleasantries. Code only. Diff format».
Используй многоступенчатый промптинг: сначала спроси номера строк с ошибкой, затем запроси патч только для этих строк.
Передавай конфигурации инфраструктуры (Ansible, Docker Compose) в YAML — он занимает меньше токенов, чем JSON.
Параметр 6: Интеграция цепочек (Tool Chaining) — 71–84
Заставь Клода писать bash-скрипты для автоматизации своих же решений (например, скрипт сборки).
Если Клод использует стороннее API, он обязан сначала сгенерировать `curl`-запрос для его тестирования.
Внедри паттерн «проектирование через OpenAPI» — сначала контракт, потом код.
Требуй генерировать `.github/workflows` параллельно с написанием серверной логики для непрерывной интеграции.
Научи Клода выводить структурированные команды, если используется промежуточный скрипт для локального выполнения его кода.
Свяжи генерацию кода с профилировщиком: Клод пишет код → профилировщик выдаёт Flame Graph → Клод оптимизирует.
При работе с Docker Клод должен всегда собирать многоэтапные (multi-stage) сборки для лёгкости образов.
Запрети хардкод секретов — Клод обязан интегрировать вызовы к Secret Manager или `.env`.
Требуй генерации Postman-коллекций (или `.http`-файлов) для каждого нового эндпоинта.
Создай цепочку: Клод пишет парсер → парсер собирает данные → Клод анализирует аномалии в этих данных.
Для архитектуры микросервисов Клод должен сначала описать контракт (gRPC/REST), прежде чем трогать сервисы.
Обяжи Клода создавать healthcheck-эндпоинты для любых изолированных систем и виртуальных машин.
Интегрируй линтер в цикл: Клод пишет → скрипт линтует → лог возвращается Клоду без участия человека.
При развёртывании распределённых узлов (IoT, mesh-сети) требуй генерации конфигурации для каждого узла списком, а не по одному запросу на узел.
Параметр 7: Изоляция и безопасность (Security / Sandboxing) — 85–98
Агент всегда должен исходить из парадигмы Zero Trust — валидировать любые входящие данные.
Запрети использование `eval()` и динамического выполнения строк любой ценой.
Требуй настройки CORS и rate limiting при проектировании любых публичных API.
Клод должен всегда использовать параметризованные SQL-запросы или ORM для защиты от инъекций.
При работе с локальными скриптами (bash) требуй запуска в песочнице.
Запрети запуск процессов от имени root в генерируемых Dockerfile, кроме случаев крайней необходимости (`USER appuser`).
Требуй сканирования зависимостей на уязвимости (аудит lock-файлов в CI).
Заставь Клода маскировать PII (персональные данные) при написании систем логирования.
Внедри правило: токены и ключи API должны передаваться только через CI/CD переменные, а не прописываться в конфигах.
При обработке больших массивов информации требуй реализации защитных буферов от переполнения памяти.
Научи агента ограничивать права доступа к базам данных (Principle of Least Privilege).
Запрети Клоду использовать неподписанные или сомнительные Docker-образы из публичного реестра.
Требуй реализации Content Security Policy (CSP) при создании фронтенда.
При написании скриптов для парсинга сети требуй ротации User-Agent и IP, чтобы избежать бана, с обработкой HTTP 429.
Параметр 0 (Золотой Мета-Хак) — 99
Правило делегирования сомнений: если вероятность успешного выполнения кода, по внутренней оценке Клода, ниже 90% — Клод обязан остановиться, не писать код и задать ровно один точный, изолированный вопрос инженеру для прояснения слепой зоны. Автономия без тормозов — это саморазрушение.
---
> «Всякое искусство и всякое исследование, а равно и всякий поступок, и
> сознательный выбор, как принято считать, стремятся к определённому
> благу» (Аристотель, «Никомахова этика»).
Истинная автономия ИИ сегодня упирается в оба ограничения одновременно,
и в разной степени на разных горизонтах: в пределах одной итерации —
почти целиком в несовершенство промпт-каркасов и протоколов изоляции
(правила 1–98 этого документа устраняют именно это — детерминированный
формат вывода, явные границы контекста, чек-листы вместо интуиции). Но
на горизонте «держать архитектуру монорепозитория без единой ошибки на
протяжении сотен итераций подряд» упирается уже в саму модель: у неё
нет постоянной памяти между сессиями и нет способа быть уверенной в
собственной неуверенности лучше, чем описано в правиле 99 — отсюда и
необходимость внешнего протокола (`state\\\_journal.md`,
`validation\\\_protocol.md`), а не внутренней рефлексии как единственной
защиты.
