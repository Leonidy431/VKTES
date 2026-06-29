# CLAUDE.md — Boreas BlueOS

Guide for AI assistants working on this repository.

## Project overview

Boreas is a semi-autonomous tethered octocopter (X8, 50 kgf thrust) that clears snow
from rooftops using a V-plow bulldozer blade and an optional hover-blow mode.
Power comes via a 400 V DC tether from a ground-based supercapacitor station (3× Maxwell
BMOD0165 P048, 495 F/48 V, ~570 kJ). The companion computer runs **BlueOS** — a Python 3.11
control module on a Cube Orange+ flight controller (ArduPilot).

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
- Never add `time.sleep()` to the main 10 Hz loop.

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

Coverage target: ≥70 % on `blueos/`. CI gate enforced in `.github/workflows/ci.yml`.

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
