#!/usr/bin/env bash
# install_jetson.sh — paper-1 sim stack one-shot installer.
#
# Target host: Jetson Orin Nano 8GB, JetPack 6.2.2, Ubuntu 22.04 LTS arm64.
# Installs ROS 2 Humble + Gazebo Harmonic + ros_gz Harmonic bridge + PX4 main,
# and sets up the project Python venv at $REPO_ROOT/.venv.
# Idempotent — safe to re-run.
#
# Logging
#   - Full stdout+stderr is tee'd (append) to $LOG_FILE
#     (default: $REPO_ROOT/install_jetson.log). Share this file when reporting
#     issues — it contains timestamps, section banners, system info, and the
#     exact failing command on error.
#   - VERBOSE=1 enables bash xtrace (set -x) for line-by-line command echo.
#
# Env overrides
#   PX4_DIR, VENV_DIR, ROS_GZ_FALLBACK_WS, LOG_FILE — all optional.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
ROS_GZ_FALLBACK_WS="${ROS_GZ_FALLBACK_WS:-$HOME/ros2_ws_ros_gz}"
LOG_FILE="${LOG_FILE:-$REPO_ROOT/install_jetson.log}"

# Redirect stdout+stderr through tee from this point on. All subsequent output
# (including apt, git, make, sudo prompts) is captured to the log file too.
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

#-------------------------------------------------------------------------------
# Logging helpers
#-------------------------------------------------------------------------------
ts()      { printf '%(%Y-%m-%d %H:%M:%S)T' -1; }
log()     { printf '[%s] [install_jetson] %s\n' "$(ts)" "$*"; }
warn()    { printf '[%s] [install_jetson] WARN: %s\n' "$(ts)" "$*"; }
die()     { printf '[%s] [install_jetson] ERROR: %s\n' "$(ts)" "$*"; exit 1; }
section() { printf '\n[%s] ========== %s ==========\n' "$(ts)" "$*"; }

on_err() {
  local rc=$?
  printf '\n[%s] [install_jetson] FAILED rc=%d at line %d: %s\n' \
    "$(ts)" "$rc" "${BASH_LINENO[0]}" "$BASH_COMMAND"
  printf '[install_jetson] Full log: %s\n' "$LOG_FILE"
  printf '[install_jetson] When reporting, share the last ~50 lines of that file.\n'
  # tee subprocess가 버퍼링한 출력을 로그 파일로 flush 할 시간 확보
  sync
  sleep 0.5
  exit "$rc"
}
trap on_err ERR

on_exit() {
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '\n[%s] [install_jetson] SUCCESS. Full log: %s\n' "$(ts)" "$LOG_FILE"
  fi
}
trap on_exit EXIT

[ "${VERBOSE:-0}" = "1" ] && set -x

#-------------------------------------------------------------------------------
# 0. Sanity
#-------------------------------------------------------------------------------
sanity() {
  section "sanity"
  [ "$EUID" -ne 0 ] || die "Do not run with sudo; the script invokes sudo internally. HOME=/root would break PX4 placement."
  command -v dpkg >/dev/null || die "dpkg missing — this script targets Linux (Ubuntu 22.04 arm64)."
  [ "$(dpkg --print-architecture)" = "arm64" ] || die "arch != arm64 (this script targets Jetson Orin Nano)."
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${VERSION_ID:-}" = "22.04" ] || die "Expected Ubuntu 22.04 (JetPack 6.2.x); got ${VERSION_ID:-unknown}."
  command -v sudo    >/dev/null || die "sudo required."
  command -v python3 >/dev/null || die "python3 required."
  log "OK — Ubuntu ${VERSION_ID} ${VERSION_CODENAME:-jammy} arm64, user $(whoami)."

  # MAXN_SUPER(25W+)·25W·심지어 15W에서도 build 도중 hard reset 보고됨 (PSU 또는 PMIC 한계).
  # 정책: 현재 모드가 7W(ID=3) 또는 15W(ID=0)이면 그대로 유지 (보수적 모드 신뢰).
  #       그 이외(25W·MAXN_SUPER)이면 15W로 자동 전환 (reboot 필요 없음).
  #       7W로의 전환은 reboot 필요하므로 자동화 안 함 — 사용자가 미리 reboot.
  if command -v nvpmodel >/dev/null 2>&1; then
    # `sudo nvpmodel -q` 출력 형식:
    #   NV Power Mode: 7W
    #   3
    # 두 번째 줄의 숫자 ID가 필요 (tail -1).
    local cur_mode
    cur_mode=$(sudo nvpmodel -q 2>/dev/null | tail -1 | tr -d ' ')
    case "$cur_mode" in
      0|3)
        log "Power mode ID=$cur_mode (15W 또는 7W) — 빌드 안정 모드, 유지."
        ;;
      *)
        log "Power mode currently ID=$cur_mode — switching to 15W(ID=0) for build stability."
        log "  (Build 끝난 후 원하면 \`sudo nvpmodel -m 2\` 로 MAXN_SUPER 복원.)"
        log "  (또는 더 안정적으로 7W를 쓰려면 \`sudo nvpmodel -m 3\` 후 reboot.)"
        # mode 0 ↔ mode 1·2 간 전환은 reboot 불필요. 그러나 mode 3 → 0 은 reboot 필요.
        # 위 case가 7W를 잡았어야 하니, 여기까지 오면 25W/MAXN_SUPER 케이스.
        sudo nvpmodel -m 0 || warn "nvpmodel switch to 15W failed (모드 전환에 reboot 필요할 수 있음). 현재 모드 그대로 진행."
        sleep 2
        ;;
    esac
  fi
}

#-------------------------------------------------------------------------------
# System info dump (helps remote debugging — capture environment up front).
#-------------------------------------------------------------------------------
dump_sysinfo() {
  section "system info"
  log "script:       $0 (pid $$)"
  log "user:         $(whoami)  HOME=$HOME"
  log "uname -a:     $(uname -a)"
  log "cpu:          $(nproc) cores"
  log "memory:       $(free -h | awk '/^Mem:/ {print $2 " total / " $7 " avail"}')"
  log "swap:         $(free -h | awk '/^Swap:/ {print $2 " total / " $3 " used"}')"
  log "disk /:       $(df -h /       | awk 'NR==2 {print $4 " free of " $2}')"
  log "disk \$HOME:   $(df -h \"$HOME\" | awk 'NR==2 {print $4 " free of " $2}')"
  if [ -f /etc/nv_tegra_release ]; then
    log "JetPack/L4T:  $(head -1 /etc/nv_tegra_release)"
  else
    warn "JetPack/L4T:  /etc/nv_tegra_release missing — is this really a Jetson?"
  fi
  log "REPO_ROOT:    $REPO_ROOT"
  log "PX4_DIR:      $PX4_DIR"
  log "VENV_DIR:     $VENV_DIR"
  log "ROS_GZ ws:    $ROS_GZ_FALLBACK_WS"
  log "LOG_FILE:     $LOG_FILE"

  # Connectivity probes — non-fatal, but apt/git/PX4 will likely fail without them.
  for host in packages.ros.org packages.osrfoundation.org raw.githubusercontent.com github.com; do
    if curl -sI --max-time 5 "https://$host" >/dev/null 2>&1; then
      log "network:      https://$host  OK"
    else
      warn "network:      https://$host  unreachable — downstream steps may fail."
    fi
  done

  # Existing relevant state (idempotent re-run inspection).
  log "existing ROS 2 install:  $([ -f /opt/ros/humble/setup.bash ] && echo yes || echo no)"
  log "existing Gazebo gz-harmonic pkg: $(dpkg -s gz-harmonic >/dev/null 2>&1 && echo installed || echo absent)"
  log "existing PX4 clone:      $([ -d "$PX4_DIR/.git" ] && echo yes || echo no)"
  log "existing project venv:   $([ -d "$VENV_DIR" ] && echo yes || echo no)"
}

#-------------------------------------------------------------------------------
# 1. Base packages
#-------------------------------------------------------------------------------
install_base() {
  section "base packages"

  # First update is lenient: NVIDIA L4T repo occasionally throws GPG warnings on
  # JetPack 6.2.x that are non-fatal. Don't let `set -e` kill us here.
  log "apt update (lenient on first pass)..."
  sudo apt-get update || warn "apt-get update returned non-zero; continuing (often harmless on JetPack)."

  log "Installing base build tools (+ python3-venv for project venv)..."
  sudo apt-get install -y \
    curl wget gnupg lsb-release ca-certificates \
    software-properties-common \
    build-essential cmake git \
    python3-pip python3-venv python3-dev
}

#-------------------------------------------------------------------------------
# 2. ROS 2 Humble
#-------------------------------------------------------------------------------
install_ros2() {
  section "ROS 2 Humble"

  if [ ! -f /usr/share/keyrings/ros-archive-keyring.gpg ]; then
    log "Adding ROS 2 apt repo (packages.ros.org, dearmored key)..."
    sudo add-apt-repository -y universe
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
      | sudo gpg --dearmor -o /usr/share/keyrings/ros-archive-keyring.gpg
    echo "deb [arch=arm64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu jammy main" \
      | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
    sudo apt-get update
  else
    log "ROS 2 apt repo present, skipping repo setup."
  fi

  log "Installing ROS 2 Humble (ros-base + rviz2 + dev tools)..."
  sudo apt-get install -y \
    ros-humble-ros-base \
    ros-humble-rviz2 \
    ros-humble-rmw-cyclonedds-cpp \
    ros-dev-tools \
    python3-rosdep \
    python3-vcstool

  if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
    log "Initializing rosdep..."
    sudo rosdep init
  fi
  log "rosdep update..."
  rosdep update
}

#-------------------------------------------------------------------------------
# 3. Gazebo Harmonic
#-------------------------------------------------------------------------------
install_gazebo() {
  section "Gazebo Harmonic"

  if dpkg -s gz-harmonic >/dev/null 2>&1; then
    log "Gazebo Harmonic already installed."
    return
  fi
  log "Adding OSRF gazebo apt repo + installing Harmonic..."
  if [ ! -f /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg ]; then
    sudo wget -q -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
      https://packages.osrfoundation.org/gazebo.gpg
  fi
  echo "deb [arch=arm64 signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable jammy main" \
    | sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y gz-harmonic
}

#-------------------------------------------------------------------------------
# 4. ros_gz Harmonic bridge (Humble ↔ Harmonic)
#    Binary lives in OSRF repo. If arm64 build is missing, fall back to source.
#-------------------------------------------------------------------------------
install_ros_gz() {
  section "ros_gz bridge (Humble ↔ Harmonic)"

  log "Probing apt for binary ros-humble-ros-gzharmonic..."
  local candidate
  candidate="$(apt-cache policy ros-humble-ros-gzharmonic 2>/dev/null | awk '/Candidate:/ {print $2}')"
  if [ -n "${candidate:-}" ] && [ "$candidate" != "(none)" ]; then
    log "Binary candidate: $candidate — installing."
    sudo apt-get install -y ros-humble-ros-gzharmonic
    return
  fi

  warn "ros-humble-ros-gzharmonic 바이너리 없음 → source 빌드 fallback (workspace: $ROS_GZ_FALLBACK_WS)"
  mkdir -p "$ROS_GZ_FALLBACK_WS/src"
  if [ -d "$ROS_GZ_FALLBACK_WS/src/ros_gz/.git" ]; then
    log "ros_gz fallback clone exists; updating to origin/humble..."
    git -C "$ROS_GZ_FALLBACK_WS/src/ros_gz" fetch --all
    git -C "$ROS_GZ_FALLBACK_WS/src/ros_gz" reset --hard origin/humble
  else
    log "Cloning ros_gz humble branch..."
    git clone -b humble https://github.com/gazebosim/ros_gz.git "$ROS_GZ_FALLBACK_WS/src/ros_gz"
  fi
  (
    cd "$ROS_GZ_FALLBACK_WS"
    export GZ_VERSION=harmonic
    # ROS 2 setup.bash 안에서 unbound 변수(AMENT_TRACE_SETUP_FILES 등)를 참조하므로
    # 우리 스크립트의 `set -u`(nounset)와 충돌 → source 동안만 nounset 해제.
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    set -u
    log "Installing ros_gz build dependencies via rosdep..."
    # gz-harmonic은 OSRF apt에서 직접 설치되므로 rosdep 키가 없을 수 있음 → skip
    # libgflags-dev 등 system 의존성이 여기서 들어옴
    rosdep install --from-paths src --ignore-src -r -y \
      --skip-keys "gz_cmake_vendor gz_common_vendor gz_dome_vendor gz_fortress_vendor gz_garden_vendor gz_harmonic_vendor gz_math_vendor gz_msgs_vendor gz_plugin_vendor gz_sensors_vendor gz_sim_vendor gz_tools_vendor gz_transport_vendor gz_utils_vendor"
    log "colcon build ros_gz (sequential, NUM_JOBS=2 per package)..."
    # colcon 기본 parallel-workers = N_CPU (Orin Nano 6 cores) → 동시 N개 패키지 × 2 jobs = 12+ compile
    # 8GB RAM에 과부하 → reboot 위험. --parallel-workers 1로 한 번에 한 패키지씩.
    NUM_JOBS=2 colcon build --packages-up-to ros_gz --symlink-install \
      --parallel-workers 1 \
      --event-handlers console_direct+ \
      --cmake-args -DCMAKE_BUILD_TYPE=Release
  )
  log "ros_gz source 빌드 완료 — ~/.bashrc 에 다음 줄을 추가하세요:"
  log "  source $ROS_GZ_FALLBACK_WS/install/setup.bash"
}

#-------------------------------------------------------------------------------
# 5. PX4 Autopilot
#-------------------------------------------------------------------------------
install_px4() {
  section "PX4 Autopilot"

  if [ -d "$PX4_DIR/.git" ]; then
    log "PX4 already at $PX4_DIR; fetching + refreshing submodules..."
    git -C "$PX4_DIR" fetch --all --tags
    git -C "$PX4_DIR" submodule update --init --recursive
  else
    log "Cloning PX4-Autopilot to $PX4_DIR..."
    git clone --recursive https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR"
  fi

  # --no-nuttx: skip flight-controller HW deps (SITL only).
  # --no-sim-tools: skip PX4-bundled Gazebo install; we manage Gazebo Harmonic ourselves.
  log "Running PX4 Ubuntu setup (--no-nuttx --no-sim-tools)..."
  bash "$PX4_DIR/Tools/setup/ubuntu.sh" --no-nuttx --no-sim-tools

  # NUM_JOBS propagates through PX4 → cmake → Ninja; MAKEFLAGS=-j2 does not.
  log "Building PX4 SITL default (NUM_JOBS=2, ~5–20 min on Orin Nano)..."
  ( cd "$PX4_DIR" && NUM_JOBS=2 make px4_sitl_default )
  log "PX4 build done: $(ls -1 "$PX4_DIR/build/px4_sitl_default/bin/px4" 2>/dev/null || echo 'BINARY MISSING')"
}

#-------------------------------------------------------------------------------
# 6. Project Python venv (POLICY: all Python in this project runs under venv).
#    --system-site-packages so rclpy and other ROS 2 Python remain importable.
#-------------------------------------------------------------------------------
install_project_venv() {
  section "project Python venv"

  if [ ! -d "$VENV_DIR" ]; then
    log "Creating project venv at $VENV_DIR (--system-site-packages)..."
    python3 -m venv --system-site-packages "$VENV_DIR"
  else
    log "venv already exists at $VENV_DIR."
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  log "venv python: $(which python3)   pip: $(which pip)"
  pip install --upgrade pip
  if [ -f "$REPO_ROOT/requirements-dev.txt" ]; then
    log "Installing requirements-dev.txt into venv..."
    pip install -r "$REPO_ROOT/requirements-dev.txt"
  else
    warn "requirements-dev.txt 없음 — 건너뜀."
  fi
  log "Installed in venv:"
  pip list --format=columns | sed 's/^/[install_jetson]   /'
  deactivate
}

#-------------------------------------------------------------------------------
# 7. Postinstall hint
#-------------------------------------------------------------------------------
print_postinstall() {
  section "post-install instructions"
  cat <<EOM

다음을 ~/.bashrc 에 추가하세요:

  source /opt/ros/humble/setup.bash
  export PX4_DIR="$PX4_DIR"
  # ros_gz를 source로 빌드한 경우(자동 분기 시):
  # source $ROS_GZ_FALLBACK_WS/install/setup.bash

매 작업 셸에서 프로젝트 venv 활성화:

  source $VENV_DIR/bin/activate

리포 루트에서 빌드·실행 흐름:

  cd "$REPO_ROOT"
  colcon build --symlink-install           # 우리 ROS 2 패키지가 추가된 후
  source install/setup.bash
  ros2 launch sim minimal.launch.py        # (sim 패키지가 만들어진 후)

PX4 SITL standalone 검증:

  cd "\$PX4_DIR" && make px4_sitl gz_x500

문제가 생기면 $LOG_FILE 마지막 ~50줄을 공유하세요.
EOM
}

main() {
  log "===== install_jetson.sh starting (pid $$, VERBOSE=${VERBOSE:-0}) ====="
  sanity
  dump_sysinfo
  install_base
  install_ros2
  install_gazebo
  install_ros_gz
  install_px4
  install_project_venv
  print_postinstall
}

main "$@"
