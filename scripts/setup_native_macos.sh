#!/usr/bin/env bash
# setup_native_macos.sh — paper-1 sim stack (macOS native) one-shot installer.
#
# Target host: Mac mini M4 (Apple Silicon, arm64), macOS 14+.
# Installs Gazebo Harmonic (Homebrew osrf/simulation) + PX4-Autopilot main
# native build + PX4 venv with required Python deps + project venv ($REPO_ROOT/.venv
# per ADR-0004) + macOS-specific patches needed for the PX4-gz-sim pair to
# function on Apple Silicon (Ogre/protobuf workarounds).
#
# This script is for the *SITL/gz execution* track per ADR-0008. The ROS 2 /
# CI portability track stays in Docker — see docker/README.md.
#
# Idempotent — safe to re-run. Each step checks existing state first.
#
# Env overrides
#   PX4_DIR       PX4 source tree (default $HOME/PX4-Autopilot)
#   PROJECT_PY    Python interpreter for $REPO_ROOT/.venv (default Homebrew python@3.11
#                 — 2026-05-25 잠금: ML wheel 가용성 + LTS 안정성. PX4 toolchain venv 는
#                 별개로 python@3.14 사용)
#   RUN_BUILD     1 to trigger the first `make px4_sitl` build (default 0 —
#                 first build takes ~10–15 min; opt in deliberately)
#   LOG_FILE      Log path (default $REPO_ROOT/setup_native_macos.log)
#   VERBOSE       1 enables bash xtrace
#
# Usage
#   ./scripts/setup_native_macos.sh           # install + patch, no build
#   RUN_BUILD=1 ./scripts/setup_native_macos.sh   # also trigger first build

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
RUN_BUILD="${RUN_BUILD:-0}"
LOG_FILE="${LOG_FILE:-$REPO_ROOT/setup_native_macos.log}"

# Suppress auto-update mid-script (it races with brew list checks).
export HOMEBREW_NO_AUTO_UPDATE=1

mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

#-------------------------------------------------------------------------------
# Logging
#-------------------------------------------------------------------------------
ts()      { date '+%Y-%m-%d %H:%M:%S'; }
log()     { printf '[%s] [setup_macos] %s\n' "$(ts)" "$*"; }
warn()    { printf '[%s] [setup_macos] WARN: %s\n' "$(ts)" "$*"; }
die()     { printf '[%s] [setup_macos] ERROR: %s\n' "$(ts)" "$*"; exit 1; }
section() { printf '\n[%s] ========== %s ==========\n' "$(ts)" "$*"; }

on_err() {
  local rc=$?
  printf '\n[%s] [setup_macos] FAILED rc=%d at line %d: %s\n' \
    "$(ts)" "$rc" "${BASH_LINENO[0]}" "$BASH_COMMAND"
  printf '[setup_macos] Full log: %s\n' "$LOG_FILE"
  printf '[setup_macos] When reporting, share the last ~50 lines of that file.\n'
  sync; sleep 0.5
  exit "$rc"
}
trap on_err ERR

on_exit() {
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '\n[%s] [setup_macos] SUCCESS. Full log: %s\n' "$(ts)" "$LOG_FILE"
  fi
}
trap on_exit EXIT

[ "${VERBOSE:-0}" = "1" ] && set -x

# macOS 26+ (CLT 21) workaround: libc++ headers moved into the SDK but clang
# still searches the non-SDK path first and finds only __cxx_version there.
# Auto-detect and inject CPLUS_INCLUDE_PATH so all child processes (make, cmake,
# ninja, c++) see the correct headers without manual intervention.
_clt_cxx="/Library/Developer/CommandLineTools/usr/include/c++/v1"
_sdk_cxx="$(xcrun --show-sdk-path)/usr/include/c++/v1"
if [ ! -f "$_clt_cxx/cstdlib" ] && [ -f "$_sdk_cxx/cstdlib" ]; then
  export CPLUS_INCLUDE_PATH="${_sdk_cxx}${CPLUS_INCLUDE_PATH:+:${CPLUS_INCLUDE_PATH}}"
  log "macOS CLT c++ headers not in $_clt_cxx — CPLUS_INCLUDE_PATH set to $_sdk_cxx"
fi
unset _clt_cxx _sdk_cxx

#-------------------------------------------------------------------------------
# Preflight
#-------------------------------------------------------------------------------
section "Preflight"

log "macOS: $(sw_vers -productVersion) build $(sw_vers -buildVersion)"
log "Arch:  $(uname -m)"
log "Repo:  $REPO_ROOT"
log "PX4:   $PX4_DIR"

[ "$(uname -s)" = "Darwin" ]  || die "macOS only (uname=$(uname -s))."
[ "$(uname -m)" = "arm64" ]   || die "Apple Silicon (arm64) only (uname -m=$(uname -m))."

command -v brew >/dev/null || die "Homebrew not found. Install from https://brew.sh first."
BREW_PREFIX="$(brew --prefix)"
log "Homebrew: $BREW_PREFIX"

xcode-select -p >/dev/null 2>&1 || die "Xcode Command Line Tools not found. Run: xcode-select --install"
log "Xcode CLT: $(xcode-select -p)"

# Raise file descriptor limit for the build step (PX4 + gz deps open many files).
ulimit -n 65535 || warn "ulimit -n 65535 failed (current: $(ulimit -n)) — brew installs may hit limits."
log "ulimit -n: $(ulimit -n)"

#-------------------------------------------------------------------------------
# Step 1: Homebrew tap + dependencies
#-------------------------------------------------------------------------------
section "Step 1/6: Homebrew dependencies"

if brew tap | grep -qx "osrf/simulation"; then
  log "tap osrf/simulation: already added"
else
  log "Adding tap osrf/simulation..."
  brew tap osrf/simulation
fi

brew_install_if_missing() {
  local pkg="$1"
  local check="${2:-$1}"
  if brew list "$check" >/dev/null 2>&1; then
    log "  $pkg: already installed ($(brew list --versions "$check" | head -1))"
  else
    log "  Installing $pkg ..."
    brew install "$pkg"
  fi
}

# Core build deps (core Homebrew formulas)
brew_install_if_missing ninja
# python@3.14 = PX4 toolchain venv 용 (PX4 build-time tools: kconfiglib, jsonschema 등).
brew_install_if_missing python@3.14 python@3.14
# python@3.11 = 프로젝트 .venv 용 (2026-05-25 잠금, ML wheel sweet spot). PROJECT_PY 가
# 가리키는 인터프리터. 두 버전은 의도적으로 분리 — PX4 빌드 의존성 ≠ paper-1 코드 의존성.
brew_install_if_missing python@3.11 python@3.11
brew_install_if_missing opencv
brew_install_if_missing cmake
brew_install_if_missing pkgconf

# Gazebo Harmonic meta-formula. Note: gz-launch7 fails to build on macOS arm64
# (known abseil/protobuf incompatibility in upstream osrf/simulation tap) — we
# do not need gz-launch (we invoke `gz sim` directly), so partial install of
# the meta-formula is acceptable. We verify the 16 actual deps separately below.
if brew list --formula 2>/dev/null | grep -qx "gz-harmonic"; then
  log "  gz-harmonic: already installed"
else
  log "  Installing osrf/simulation/gz-harmonic (gz-launch7 sub-install may fail, ignored)..."
  brew install osrf/simulation/gz-harmonic || warn "gz-harmonic meta-install reported failure (likely gz-launch7) — verifying core deps below."
fi

# Verify the 15 gz-* deps gz-harmonic should have pulled in.
# pkgconf is a core Homebrew formula (already handled above).
log "Verifying gz-harmonic core deps..."
GZ_REQUIRED=(
  gz-cmake3 gz-common5 gz-fuel-tools9 gz-gui8 gz-math7 gz-msgs10
  gz-physics7 gz-plugin2 gz-rendering8 gz-sensors8 gz-sim8 gz-tools2
  gz-transport13 gz-utils2 sdformat14
)
MISSING_GZ=()
for pkg in "${GZ_REQUIRED[@]}"; do
  if brew list "$pkg" >/dev/null 2>&1; then
    log "  ✓ $pkg ($(brew list --versions "$pkg" | head -1))"
  else
    MISSING_GZ+=("$pkg")
    warn "  ✗ $pkg MISSING"
  fi
done
if [ ${#MISSING_GZ[@]} -gt 0 ]; then
  log "Installing missing gz deps directly: ${MISSING_GZ[*]}"
  for pkg in "${MISSING_GZ[@]}"; do
    # After `brew tap osrf/simulation`, the short name resolves to the tap formula.
    brew install "$pkg" || die "Failed to install $pkg directly."
  done
fi

#-------------------------------------------------------------------------------
# Step 2: PX4 clone
#-------------------------------------------------------------------------------
section "Step 2/6: PX4-Autopilot source"

if [ -d "$PX4_DIR/.git" ]; then
  log "PX4 already cloned at $PX4_DIR"
  log "  HEAD: $(git -C "$PX4_DIR" log -1 --oneline 2>/dev/null || echo 'unknown')"
else
  if [ -e "$PX4_DIR" ]; then
    die "$PX4_DIR exists but is not a git repo. Move it aside or set PX4_DIR=<other path>."
  fi
  log "Cloning PX4-Autopilot main into $PX4_DIR (depth=1 + submodules)..."
  git clone --recursive --depth 1 --branch main https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR"
fi

#-------------------------------------------------------------------------------
# Step 3: PX4 Python venv + requirements
#-------------------------------------------------------------------------------
section "Step 3/6: PX4 venv"

PX4_PY="$BREW_PREFIX/opt/python@3.14/bin/python3.14"
[ -x "$PX4_PY" ] || die "Homebrew python@3.14 not found at $PX4_PY"

PX4_VENV="$PX4_DIR/.venv"
if [ -d "$PX4_VENV" ]; then
  log "PX4 venv already exists at $PX4_VENV"
else
  log "Creating PX4 venv with $PX4_PY ..."
  "$PX4_PY" -m venv "$PX4_VENV"
fi

log "Installing PX4 Python requirements (this can take a minute)..."
# shellcheck disable=SC1091
source "$PX4_VENV/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "$PX4_DIR/Tools/setup/requirements.txt"
deactivate
log "PX4 venv ready. Python: $("$PX4_VENV/bin/python3" --version)"

#-------------------------------------------------------------------------------
# Step 4: Project Python venv ($REPO_ROOT/.venv) — ADR-0004 정책
#-------------------------------------------------------------------------------
# ADR-0004: 모든 프로젝트 Python은 $REPO_ROOT/.venv 안에서 실행. PX4 toolchain
# venv($PX4_DIR/.venv, python@3.14)와는 별개 — 그쪽은 PX4 빌드/런타임 의존성
# (kconfiglib, jsonschema, pymavlink 등) 전용, 이쪽은 우리 프로젝트 코드
# 전용(pytest, torch, ultralytics 등).
#
# 2026-05-25 잠금: 프로젝트 .venv = python@3.11 (ML wheel 가용성 + LTS 안정성
# sweet spot). install_ovd.sh 가 venv 가 3.11 이 아니면 backup 후 재생성하므로
# 본 스크립트와 정책 일관. memory: project-python-version-311.
#
# --system-site-packages는 정책 표준이지만, macOS native에는 시스템 ROS 2
# Python(rclpy 등)이 없어 사실상 Homebrew Python의 site-packages만 가시.
# 미래에 native ROS 2가 들어오면 자동으로 가시화되도록 플래그는 유지.
# ROS 2 노드 자체는 Docker 컨테이너 안 venv에서 동작 (docker/Dockerfile §5).
section "Step 4/6: Project venv ($REPO_ROOT/.venv)"

PROJECT_PY="${PROJECT_PY:-$BREW_PREFIX/opt/python@3.11/bin/python3.11}"
[ -x "$PROJECT_PY" ] || die "Project Python not found at $PROJECT_PY (override with PROJECT_PY=)"

PROJECT_VENV="$REPO_ROOT/.venv"
if [ -d "$PROJECT_VENV" ]; then
  log "Project venv already exists at $PROJECT_VENV"
else
  log "Creating project venv with $PROJECT_PY (--system-site-packages) ..."
  "$PROJECT_PY" -m venv --system-site-packages "$PROJECT_VENV"
fi

# shellcheck disable=SC1091
source "$PROJECT_VENV/bin/activate"
pip install --quiet --upgrade pip
if [ -f "$REPO_ROOT/requirements-dev.txt" ]; then
  log "Installing requirements-dev.txt into $PROJECT_VENV ..."
  pip install --quiet -r "$REPO_ROOT/requirements-dev.txt"
fi
deactivate
log "Project venv ready. Python: $("$PROJECT_VENV/bin/python3" --version)"

#-------------------------------------------------------------------------------
# Step 5: macOS-specific patches (idempotent)
#-------------------------------------------------------------------------------
section "Step 5/6: macOS-specific PX4 patches + ADR-0012 airframe overlay"

# Patch 1: gz_bridge CMakeLists — silence -Werror=deprecated-declarations
# (protobuf 35 deprecated Resize → fatal in PX4's gz_bridge build).
P1_FILE="$PX4_DIR/src/modules/simulation/gz_bridge/CMakeLists.txt"
P1_FLAG='add_compile_options(-Wno-error=deprecated-declarations)'
if [ ! -f "$P1_FILE" ]; then
  die "Patch target missing: $P1_FILE (PX4 source tree changed?)"
fi
if grep -qF "$P1_FLAG" "$P1_FILE"; then
  log "Patch 1 (gz_bridge -Wno-error): already applied"
else
  log "Patch 1: prepending '$P1_FLAG' to $P1_FILE"
  # Prepend as new first line.
  tmp="$(mktemp)"
  {
    printf '%s\n' "$P1_FLAG"
    cat "$P1_FILE"
  } > "$tmp"
  mv "$tmp" "$P1_FILE"
fi

# Patch 2 + 3: server.config render_engine + disable Gst/OpticalFlow plugins
P2_FILE="$PX4_DIR/src/modules/simulation/gz_bridge/server.config"
[ -f "$P2_FILE" ] || die "Patch target missing: $P2_FILE"

# Patch 2: ogre2 → ogre (Sensors-side render engine; ogre2 stalls on Apple Silicon).
if grep -qF '<render_engine>ogre</render_engine>' "$P2_FILE" \
   && ! grep -qF '<render_engine>ogre2</render_engine>' "$P2_FILE"; then
  log "Patch 2 (render_engine ogre2 → ogre): already applied"
else
  log "Patch 2: rewriting <render_engine>ogre2</render_engine> → ogre in $P2_FILE"
  # macOS sed -i needs an explicit backup suffix; we clean up after.
  sed -i.bak 's|<render_engine>ogre2</render_engine>|<render_engine>ogre</render_engine>|g' "$P2_FILE"
  rm -f "$P2_FILE.bak"
fi

# Patch 3: comment out libGstCameraSystem.so and libOpticalFlowSystem.so plugin
# lines (former doesn't build on macOS, latter builds as .dylib not .so so the
# plugin loader can't find it). Idempotent via marker comment.
P3_MARKER='<!-- macOS patch: disabled —'
if grep -qF "$P3_MARKER" "$P2_FILE"; then
  log "Patch 3 (Gst/OpticalFlow plugins disabled): already applied"
else
  log "Patch 3: commenting libGstCameraSystem / libOpticalFlowSystem plugins in $P2_FILE"
  # PX4 server.config uses self-closing tags (/>), not paired </plugin>.
  # Use sed line-level wrapping — each plugin is on a single line.
  sed -i.bak \
    -e '/filename="libGstCameraSystem\.so"/s|.*|<!-- macOS patch: disabled — does not build on macOS\n&\n-->|' \
    -e '/filename="libOpticalFlowSystem\.so"/s|.*|<!-- macOS patch: disabled — builds as .dylib, plugin loader expects .so\n&\n-->|' \
    "$P2_FILE"
  rm -f "$P2_FILE.bak"
  if ! grep -qF "$P3_MARKER" "$P2_FILE"; then
    warn "Patch 3: no Gst/OpticalFlow plugin patterns found in server.config — PX4 upstream may have changed. Skipped (likely fine)."
  fi
fi

# Patch 4 + 5 + 6: ADR-0012 px4vision_indoor airframe overlay wiring
# (idempotent — symlink -sfn + CMakeLists check before insert).
#
# Until 2026-05-26 the overlay wiring was documented only in progress notes,
# so a fresh setup_native_macos.sh run left the PX4 tree missing the custom
# airframe and `make px4_sitl gz_px4vision_indoor` failed with
# `ninja: unknown target 'gz_px4vision_indoor'`. We absorb the three manual
# steps (airframe symlink, model symlink, CMakeLists registration) here so
# the installer is self-sufficient.
P4_AIRFRAME_NAME='22000_gz_px4vision_indoor'
P4_AIRFRAME_SRC="$REPO_ROOT/sim/px4_overlay/$P4_AIRFRAME_NAME"
P4_AIRFRAME_DST="$PX4_DIR/ROMFS/px4fmu_common/init.d-posix/airframes/$P4_AIRFRAME_NAME"
P5_MODEL_NAME='px4vision_indoor'
P5_MODEL_SRC="$REPO_ROOT/sim/models/$P5_MODEL_NAME"
P5_MODEL_DST="$PX4_DIR/Tools/simulation/gz/models/$P5_MODEL_NAME"
P6_CMAKE_FILE="$PX4_DIR/ROMFS/px4fmu_common/init.d-posix/airframes/CMakeLists.txt"
P6_NEEDLE='# [22000, 22999] Reserve for custom models'

# Patch 4: airframe overlay symlink.
if [ ! -f "$P4_AIRFRAME_SRC" ]; then
  die "Patch 4: overlay source missing — $P4_AIRFRAME_SRC (repo state corrupted?)"
fi
if [ -L "$P4_AIRFRAME_DST" ] && [ "$(readlink "$P4_AIRFRAME_DST")" = "$P4_AIRFRAME_SRC" ]; then
  log "Patch 4 (airframe symlink $P4_AIRFRAME_NAME): already in place"
else
  log "Patch 4: linking $P4_AIRFRAME_NAME → repo overlay"
  ln -sfn "$P4_AIRFRAME_SRC" "$P4_AIRFRAME_DST"
fi

# Patch 5: model symlink.
if [ ! -d "$P5_MODEL_SRC" ]; then
  die "Patch 5: model dir missing — $P5_MODEL_SRC (repo state corrupted?)"
fi
if [ -L "$P5_MODEL_DST" ] && [ "$(readlink "$P5_MODEL_DST")" = "$P5_MODEL_SRC" ]; then
  log "Patch 5 (model symlink $P5_MODEL_NAME): already in place"
else
  log "Patch 5: linking $P5_MODEL_NAME → repo model dir"
  ln -sfn "$P5_MODEL_SRC" "$P5_MODEL_DST"
fi

# Patch 6: CMakeLists 등록 — '# [22000, 22999] Reserve for custom models' 아래
# 다음 줄에 '22000_gz_px4vision_indoor' 삽입. tab + 이름 한 줄이 PX4 upstream
# 의 컬럼 정렬 관습.
[ -f "$P6_CMAKE_FILE" ] || die "Patch 6: CMakeLists missing — $P6_CMAKE_FILE"
if grep -qF "$P4_AIRFRAME_NAME" "$P6_CMAKE_FILE"; then
  log "Patch 6 (CMakeLists $P4_AIRFRAME_NAME entry): already present"
else
  if ! grep -qF "$P6_NEEDLE" "$P6_CMAKE_FILE"; then
    die "Patch 6: needle '$P6_NEEDLE' not found in $P6_CMAKE_FILE — PX4 upstream changed?"
  fi
  log "Patch 6: inserting '$P4_AIRFRAME_NAME' under '$P6_NEEDLE'"
  # Use python for safe single-replace; sed multiline is brittle on macOS.
  PY_FILE="$P6_CMAKE_FILE" PY_NEEDLE="$P6_NEEDLE" PY_NAME="$P4_AIRFRAME_NAME" \
    python3 -c '
import os
path = os.environ["PY_FILE"]
needle = os.environ["PY_NEEDLE"]
name = os.environ["PY_NAME"]
src = open(path).read()
repl = needle + "\n\t" + name
assert src.count(needle) == 1, f"needle occurs {src.count(needle)}× — manual review needed"
open(path, "w").write(src.replace(needle, repl, 1))
'
fi

#-------------------------------------------------------------------------------
# Step 6: Verify
#-------------------------------------------------------------------------------
section "Step 6/6: Verify install"

log "gz version:    $(gz sim --version 2>&1 | head -1)"
log "PX4 path:      $PX4_DIR"
log "PX4 venv:      $PX4_VENV ($("$PX4_VENV/bin/python3" --version 2>&1))"
log "Project venv:  $PROJECT_VENV ($("$PROJECT_VENV/bin/python3" --version 2>&1))"

# Airframe overlay sanity — verify Patch 4/5/6 results visible to PX4 build.
if [ -L "$P4_AIRFRAME_DST" ] \
   && [ -L "$P5_MODEL_DST" ] \
   && grep -qF "$P4_AIRFRAME_NAME" "$P6_CMAKE_FILE"; then
  log "airframe:   $P4_AIRFRAME_NAME wired into PX4 tree (ADR-0012)"
else
  warn "airframe:   $P4_AIRFRAME_NAME wiring incomplete — re-run setup or check Patch 4/5/6 output"
fi

# OpenCV sanity (PX4 optical_flow plugin builds against this even though we
# disable the plugin at runtime — cmake still needs it during configure).
if brew list --formula 2>/dev/null | grep -qx "opencv"; then
  log "opencv:     $(brew list --versions opencv | head -1)"
fi

#-------------------------------------------------------------------------------
# Optional: first build
#-------------------------------------------------------------------------------
if [ "$RUN_BUILD" = "1" ]; then
  section "Optional: first PX4 SITL build (~10–15 min on first run)"
  cd "$PX4_DIR"
  # Use the venv's python so PX4's build-time tools (kconfiglib, jsonschema) resolve.
  PATH="$PX4_VENV/bin:$PATH" make px4_sitl
  log "PX4 SITL binary: $PX4_DIR/build/px4_sitl_default/bin/px4"
else
  section "First build SKIPPED (RUN_BUILD=0)"
  log "To trigger the first PX4 SITL build (~10–15 min), run:"
  log "  cd $PX4_DIR && PATH=$PX4_VENV/bin:\$PATH make px4_sitl"
  log "Or re-run this script with RUN_BUILD=1."
fi

#-------------------------------------------------------------------------------
# Next steps
#-------------------------------------------------------------------------------
section "Next steps"
cat <<EOF
Native macOS sim stack ready.

Two venvs are now in place (intentionally separate):
  - PX4 toolchain:  $PX4_VENV
  - Project (ADR-0004): $PROJECT_VENV

To run the livingroom × x500 SITL scene (after first build):
  Terminal 1: $REPO_ROOT/scripts/run_native_sitl_livingroom.sh
              (auto-activates $PX4_VENV)
  Terminal 2: GZ_IP=127.0.0.1 gz sim -g

For any project Python work (pytest, eval scripts, etc.):
  source $PROJECT_VENV/bin/activate

Stack policy: SITL/gz runs on macOS native (this script).
              ROS 2 nodes (sim_user_marker, ros_gz bridges) run in Docker —
              see docker/README.md. Cross-host data path = uXRCE-DDS over
              UDP 8888 (ADR-0010), Mac-side agent built by
              scripts/build_microxrce_agent_macos.sh.

See: docs/handover/decisions/0008-paper1-gui-path-native-macos.md
     docs/handover/decisions/0010-e1-uxrce-dds-bridge.md
     docs/handover/progress/2026-05-22-sim-d3-d5-native-macos-pivot.md
EOF
