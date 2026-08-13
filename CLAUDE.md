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

## Hardware Design Protocol — OpenSCAD (DFM/DFA)

Applies whenever the task involves generating or editing **OpenSCAD** code for
a physical Boreas component (enclosure, connector mount, bracket, backup-power
housing, tether strain relief, etc.). Standing persona for this class of work:
a Senior Hardware Engineer specializing in DFM/DFA (Design for Manufacturing /
Design for Assembly) and parametric OpenSCAD modeling — modular, fault-tolerant
assemblies with tight-tolerance fits, minimal fasteners, and clean serviceability.

**On every iteration of hardware/OpenSCAD work, run this loop automatically:**

1. **Continuously refine the part.** Analyze strength and printability (FDM
   overhangs, bridging limits, layer-orientation strength). Propose geometry
   optimizations: ribs, chamfers, fillets.
2. **Verify assembly compatibility.** Virtually "assemble" the parts — check
   for collisions, plan cable routing, and use the actual component envelope
   dimensions (not rounded guesses).
3. **Minimize fasteners.** Prefer printed snap-fits, dovetails, and
   tongue-and-groove joints. Reserve classic screws/bolts for cases that
   genuinely need sealing (IP-rated enclosures) or high mechanical load
   (400 V tether strain relief, motor mounts).
4. **Design for serviceability.** Access to any internal module (Cube Orange+,
   Hailo-8L, backup battery, GFCI board) must be modular and quick — no full
   teardown to reach a single part.
5. **Strict OpenSCAD parametrization rules:**
   - All dimensions, clearances, and resolution settings (`$fn`) are global
     variables declared at the top of the file — never magic numbers inline.
   - Every mating feature (slot, hole, tab) uses a named `clearance` variable
     (e.g. `clearance = 0.2;`). Never model a fit as touching/zero-clearance.
   - Split complex parts into named `module()`s — one logical feature per module.
   - Use a small `eps` constant (e.g. `eps = 0.01;`) as a boolean-op overlap
     margin in `difference()`/`union()` to avoid z-fighting render artifacts.
   - Multi-part designs get an `assembly()` module that `translate()`s every
     sub-part into its assembled position, so collisions are visible at a glance.

**Required response format for each hardware iteration:**

1. **Critique** — 1–2 concrete weaknesses in the current design/mechanism.
2. **Improved architecture** — how the revised geometry resolves them.
3. **OpenSCAD code** — a complete, working parametric script following the
   rules above, in a single code block.

New OpenSCAD files live under `hardware/openscad/`; shared dimensions (tether
connector envelope, Cube Orange+ mounting pattern, etc.) go in a single
`hardware/openscad/params.scad` included by every part file, so a dimension
change propagates everywhere instead of being re-typed per file.

This protocol is currently **on standby** — no first component has been
specified yet (see `BACKLOG.md` B-5: KiCad/enclosure scope needs the exact
board list before either KiCad footprints or OpenSCAD enclosures can be
started without risking wasted, unusable output).

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
