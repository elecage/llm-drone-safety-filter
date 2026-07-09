# llm-drone-safety-filter

Reference implementation for the paper **"A Confidence-Modulated Safety-Filter
Architecture for Reliable LLM-in-the-Loop Drone Control"** (H.-m. Shim and
S. T. Woo).

The system pilots an assistive drone for users with quadriplegia: a large
language model (LLM) interprets voice intent in a slow semantic layer, while a
deterministic real-time **safety layer** beneath it guarantees physical safety
regardless of what the LLM outputs. The safety layer adjusts the radius of a
user avoidance region by the *intent-interpretation confidence*, monotonically
non-increasing in the confidence and never below a deterministically fixed
minimum margin (the **monotonicity-floor invariant**), so that the LLM's
influence can only increase conservatism.

This repository contains the code needed to reproduce the simulation study. It
is a code-availability companion; internal design records, the manuscript, and
figures live in a separate private repository.

## Architecture

Three deterministic tiers below the semantic–intent layer:

- **Tier 0** — firmware failsafe (PX4): geofence, speed/altitude clamps, RTL.
- **Tier 1** — real-time reactive safety filter (`safety/tier1`): rate limiter,
  confidence-modulated safety-margin mapping, CBF-QP. Runs at a fixed period,
  independent of LLM latency.
- **Tier 2** — plan-level runtime verification gate (`safety/tier2_gate`):
  command contract, temporal-logic specifications, confidence thresholds.

The semantic–intent layer (`intent/`) comprises an open-vocabulary detector, a
prompt composer, and a swappable *intent interpreter* (cloud or edge LLM). The
safety layer depends only on the fixed action-call format and three raw
confidence signals, so the *intent interpreter* is replaceable without touching
the safety layer.

## Repository layout

| Path | Contents |
|---|---|
| `safety/` | Tier 1 filter (CBF-QP), Tier 2 gate, confidence estimator |
| `intent/` | *Intent interpreter* interface, open-vocabulary detector, confidence signals, prompt composer |
| `sim/` | PX4/Gazebo ROS 2 packages: scenarios, waypoint/offboard players, user marker |
| `eval/` | Evaluation harness: baselines, fault injection, metrics, runner |
| `scripts/` | Install and run scripts (native macOS SITL, STT/TTS, experiment grid) |
| `docker/` | `linux/arm64` container for the ROS 2 + Gazebo stack |
| `data/` | Aggregated trial-level data underlying the figures and tables |

> **Note on assets.** Gazebo world models (human avatars, drone airframe, mug,
> wheelchair) are third-party assets and are **not** redistributed here. Obtain
> them from Gazebo Fuel (or your own source) and place them under `sim/models/`
> before running the simulation.

## Software stack

- Ubuntu 22.04, ROS 2 Humble, Gazebo Harmonic, PX4 (main)
- `ros_gz` (`ros-humble-ros-gzharmonic`, OSRF apt)
- Python 3.11 in a project virtual environment
- Open-vocabulary detection: YOLO-World (ultralytics `yolov8s-worldv2`)
- Speech-to-text: Whisper large-v3

Two execution tracks are supported: **native macOS** (Apple Silicon; SITL +
Gazebo Harmonic via Homebrew, PX4 built from source) and a **Docker
`linux/arm64`** container for the ROS 2 + `ros_gz` stack. See `docker/README.md`.

## Setup

All Python runs use a project virtual environment created with
`--system-site-packages` so that ROS 2's `rclpy` is visible:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
# optional components
pip install -r requirements-ovd.txt          # open-vocabulary detector
pip install -r requirements-calibration.txt  # confidence calibration analysis
```

Native macOS SITL toolchain (PX4 + Gazebo Harmonic):

```bash
scripts/setup_native_macos.sh
```

## Running

Bring up the living-room SITL scene (two-terminal pattern: headless PX4 server +
Gazebo GUI):

```bash
scripts/run_native_sitl_livingroom.sh
```

Voice pipeline (Whisper STT → intent interpretation → drone):

```bash
scripts/run_stt.sh      # speech-to-text
scripts/run_tts.sh      # text-to-speech prompts
```

Reproduce the evaluation grid (scenarios × baselines × fault channels ×
interpreters):

```bash
scripts/run_full_experiment.sh          # full matrix
python scripts/run_grid.py --help       # single-grid options
```

Baselines B0–B4 (no filter / static min / static max / confidence-modulated /
+ prompt composer / + gate) and the four fault channels (hallucination,
adversarial, cognitive lapse, attribute false-detection) are defined in `eval/`.

## Citation

If you use this code, please cite the paper:

```bibtex
@article{shim_confidence_modulated,
  title   = {A Confidence-Modulated Safety-Filter Architecture for
             Reliable LLM-in-the-Loop Drone Control},
  author  = {Shim, Hyeon-min and Woo, Seong Tak},
  note    = {Code: https://github.com/elecage/llm-drone-safety-filter}
}
```

## License

Apache License 2.0 (see `LICENSE`). Third-party Gazebo assets referenced above
are not covered by this license and are not distributed here.
