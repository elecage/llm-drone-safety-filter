#!/bin/bash
# run_native_sitl_livingroom.sh — PX4 SITL × livingroom_base.sdf on macOS host (native).
#
# Use case: D5 live smoke / 시각 검증 / 데모.
#
# Prerequisites (one-time):
#   - ~/PX4-Autopilot cloned with submodules, venv at ~/PX4-Autopilot/.venv
#   - Homebrew gz-sim8 + deps installed (`brew install osrf/simulation/gz-sim8`)
#   - This repo cloned at ~/LLM_Drone (or symlinked path adjusts itself)
#   - Patch applied to gz_bridge CMakeLists for protobuf-35 deprecation:
#     `add_compile_options(-Wno-error=deprecated-declarations)` near top.
#
# Defaults (macOS native α' 경로 기준):
#   - HEADLESS=1 (PX4가 gz GUI 미실행). GUI는 별 콘솔 Terminal.app에서
#       export GZ_IP=127.0.0.1
#       gz sim -g
#     로 따로 띄움 (--render-engine 미지정 = 기본 ogre2). PX4가 서버 측에
#     `GZ_IP=127.0.0.1`로 못 박으므로 GUI 클라이언트에도 동일 export 필요;
#     미설정 시 multicast 디스커버리가 서버를 못 찾아 GUI가
#     "requesting list of world names" 무한 polling → NSWindow 미생성
#     (실측 2026-05-22). **SSH 세션으론 띄울 수 없음** — macOS audit session
#     정책상 GUI 앱은 console GUI 세션을 가진 Terminal.app에서만 가능.
#   - Spawn pose = 드론 dock (0.5, -0.5, 0.15) → 사용자(0,-1.0)에서 xy ≈0.71 m
#     (ADR-0009 v2 레이아웃; r_min=0.7m와 간발 — Open O1).
#
# Stop: Ctrl-C → PX4 shutdown 시 gz_bridge 통해 gz 서버까지 정리.
#       잔재 시: pkill -f "gz sim".

set -euo pipefail
ulimit -n 65535

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"

if [ ! -d "$PX4_DIR" ]; then
  echo "ERROR: PX4-Autopilot not found at $PX4_DIR" >&2
  echo "       Run scripts/setup_native_macos.sh first (clones to ~/PX4-Autopilot)." >&2
  exit 1
fi

if [ ! -f "$PX4_DIR/.venv/bin/activate" ]; then
  echo "ERROR: PX4 venv not found at $PX4_DIR/.venv" >&2
  echo "       Run scripts/setup_native_macos.sh first (creates venv + installs PX4 Python deps)." >&2
  exit 1
fi

# World 측 env override 측 일반화 (default = livingroom_base) — 다른 world
# (예: yard_base) 측 사용 wrapper script (run_native_sitl_yard.sh) 측 본 변수
# 설정 후 본 script 측 호출. ADR-0008 D1 macOS native 측 livingroom 측 *유일*
# 검증 자리 → outdoor (yard_base) 측 *별 트랙* (sim 인프라 호환성 점검).
WORLD_NAME="${WORLD_NAME:-livingroom_base}"

# Symlink our world into PX4's lookup path. PX4 resolves world via
# ${PX4_GZ_WORLDS}/${PX4_GZ_WORLD}.sdf and rewrites PX4_GZ_WORLDS in gz_env.sh,
# so the symlink is the path of least friction.
if [ ! -f "$REPO_ROOT/sim/worlds/${WORLD_NAME}.sdf" ]; then
  echo "ERROR: world SDF not found — $REPO_ROOT/sim/worlds/${WORLD_NAME}.sdf" >&2
  echo "       Allowed: $(ls "$REPO_ROOT/sim/worlds/" | sed 's/\.sdf$//' | tr '\n' ' ')" >&2
  exit 1
fi
ln -sf "$REPO_ROOT/sim/worlds/${WORLD_NAME}.sdf" \
       "$PX4_DIR/Tools/simulation/gz/worlds/${WORLD_NAME}.sdf"

cd "$PX4_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate

export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH

# macOS 26+ (CLT 21) workaround: libc++ headers moved into SDK but clang still
# searches CLT path first. Auto-inject CPLUS_INCLUDE_PATH so PX4 incremental
# rebuild on launch (e.g. after airframe edit) doesn't fail with cstdlib not
# found. Same logic as setup_native_macos.sh:72.
_clt_cxx="/Library/Developer/CommandLineTools/usr/include/c++/v1"
_sdk_cxx="$(xcrun --show-sdk-path 2>/dev/null)/usr/include/c++/v1"
if [ -d "$_sdk_cxx" ] && [ ! -f "$_clt_cxx/cstdlib" ] && [ -f "$_sdk_cxx/cstdlib" ]; then
  export CPLUS_INCLUDE_PATH="${_sdk_cxx}${CPLUS_INCLUDE_PATH:+:${CPLUS_INCLUDE_PATH}}"
fi
unset _clt_cxx _sdk_cxx

export PX4_GZ_WORLD="$WORLD_NAME"
# PX4_SIM_MODEL 측 default = indoor (livingroom). outdoor wrapper 측 동일 모델
# 측 충분 (드론 자체 동일) 측 별 env override 측 책임 분리.
export PX4_SIM_MODEL="${PX4_SIM_MODEL:-gz_px4vision_indoor}"
# Spawn pose default = livingroom dock (0.5, -0.5, 0.15). yard wrapper 측 별
# 위치 override.
export PX4_GZ_MODEL_POSE="${PX4_GZ_MODEL_POSE:-0.5,-0.5,0.15,0,0,0}"
# macOS Apple Silicon 서버 render engine — **ogre2 (2026-06-11 재실측으로 변경)**:
#   - 종전 default = ogre(1) (2026-05-22 실측: ogre2 Sensors init 실패 → IMU 등
#     publish 0). 단 그 관측은 *렌더링 센서가 없던* 구성 기준.
#   - 전방 카메라 센서 추가(P1) 후 재실측 (gz-sim 8.11, SSH headless):
#       ogre(1) + 카메라 → Sensors 의 렌더링 씬 생성 시 RenderSystem_GL
#         Segmentation fault (서버 전체 사망).
#       ogre2 + 카메라 → 카메라 프레임 + IMU·baro·mag 모두 정상 publish,
#         /clock 진행, PX4 부팅 정상.
#   → 카메라가 모델에 포함된 현 구성에선 ogre2 가 유일 동작 조합. GUI 는
#     별 터미널 `gz sim -g` (기본 ogre2) 그대로.
# Linux/컨테이너 native 실행에선 unset 권장 (gz 기본 ogre2가 둘 다 작동).
export PX4_GZ_SIM_RENDER_ENGINE="${PX4_GZ_SIM_RENDER_ENGINE:-ogre2}"
export HEADLESS="${HEADLESS:-1}"

# 우리 리포의 local 모델(`sim/models/<name>/`)을 gz가 `model://<name>` URI로
# 풀 수 있게 GZ_SIM_RESOURCE_PATH에 추가. 현재 PatientWheelChair_visual 등.
# 기존 path는 PX4 wrapper(px4-rc.gzsim)가 설정한 값을 그대로 prepend로 보존.
export GZ_SIM_RESOURCE_PATH="$REPO_ROOT/sim/models${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"

# 모델 존재 가드 — livingroom world 가 include 하는 로컬 빌드 모델(sim/models, .gitignore
# 대상)이 없으면 gz 가 "Unable to find uri[model://...]" 로 world 로드 실패 → PX4 가
# "Waiting for Gazebo world" 30s 타임아웃(원인 안 보이는 cryptic 실패). 빌드 누락을
# *명확히* 알린다. coffee_mug = S5 머그 3개(ADR-0035, scripts/build_mug.py).
if [ ! -d "$REPO_ROOT/sim/models/coffee_mug" ]; then
  echo "ERROR: sim/models/coffee_mug 부재 — livingroom world 가 model://coffee_mug 를" >&2
  echo "       include 하므로 gz world 로드가 실패한다 (PX4 'Waiting for Gazebo world' 타임아웃)." >&2
  echo "       먼저 빌드: python3 scripts/build_mug.py   (Fuel 1회 다운로드, gz 필요)" >&2
  exit 1
fi

echo "============================================================"
echo "PX4 SITL × $WORLD_NAME (native macOS)"
echo "  PX4_DIR=$PX4_DIR"
echo "  world=$PX4_GZ_WORLD model=$PX4_SIM_MODEL pose=$PX4_GZ_MODEL_POSE"
echo "  HEADLESS=${HEADLESS:-unset (GUI will open)}"
echo "============================================================"
exec make px4_sitl gz_px4vision_indoor
