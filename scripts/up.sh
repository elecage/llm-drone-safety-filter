#!/usr/bin/env bash
# up.sh — 한 줄로 G1 트랙 full session 가동 (v2: 순서 재정렬 + client 안정화).
#
# v1 → v2 변경: PX4 client가 Agent 부재 시간(10–30s)에 stale state로 갇히는
# 문제 회피를 위해 **Agent를 먼저** 띄우고 PX4를 뒤에 띄우는 순서로 변경.
# 더해서 PX4 부팅 후 uxrce_dds_client status를 확인, disconnected면 자동 재시작.
#
# v3 변경 (2026-05-28, PR #142): SCENARIO env 추가 — livingroom (default) | yard.
# T1 측 wrapper script 분기 + T3 측 sim_user_marker 측 yard 좌표 전달.
# v4 변경 (2026-05-28, PR #143, 별 PR sim-yard-g2-waypoint): SCENARIO=yard 측
# default `G2_SCENARIO=y0_yard_child_follow` 측 g2_waypoint_player 자동 통합 +
# `SCENARIO=yard ./scripts/up.sh` 측 *한 명령 측 yard 완전 비행* (자녀 follow +
# 가족 sweep + dock 복귀).
# v5 변경 (2026-05-28, PR 후속 sim-tier1-yard-launch): tier1 launch 측 scenario
# arg 측 전달 — TIER1_MODE in {b1, b2} 측 yard user 좌표 정합. b0 측 passthrough
# 측 *변경 없음*.
# v6 변경 (2026-05-28, PR #148 후속 sim-teleop-keyboard build fix): colcon
# build 측 packages-select 측 `teleop_keyboard` 추가 — manual teleop 패키지 측
# install 누락 회피.
# v7 변경 (옵션 C cross-package scenario_layout): `scenario_params` 추가 —
# sim_user_marker + tier1_filter 측 공유 좌표 패키지. colcon 의존성 순서 측
# package.xml exec_depend 측 자동 처리 (scenario_params 먼저 빌드됨).
#
# 자동화 범위:
#   1. Docker 컨테이너 재생성 (detached) + colcon build
#   2. MicroXRCEAgent 시작 (Agent가 먼저 listen)
#   3. T1 새 Terminal.app 창: PX4 SITL (PX4가 부팅하자마자 Agent와 즉시 connect)
#   4. T2 새 Terminal.app 창: gz GUI (클라이언트 연결 시 서버 자동 진행 — ▶ 불요)
#   5. PX4 client `uxrce_dds_client status` 확인 + disconnected면 stop/start
#   6. SITL preflight param 자동 완화 (mavlink)
#   7. sim_user_marker (scenario-aware launch) + g1_offboard + waypoint_follower
#      launch (detached). tier1 은 TIER1_MODE 명시 시에만 (영속 셸 기본은 미포함,
#      ADR-0030 D2 — per-trial 합성이 소유).
#   8. check_g1_smoke.sh
#
# 수동 조작: 없음 — gz GUI 는 클라이언트 연결만으로 자동 진행(2026-06-14 실측).
#
# 옵션 환경변수:
#   SCENARIO=livingroom (default) | yard
#                Sim 인프라 점검 측 yard (sim/worlds/yard_base.sdf S8 정적 layout)
#                측 *별 트랙* — paper-1 scope 영향 없음.
#   G2_SCENARIO=<yaml name> | ""
#                g2_waypoint_player 측 시나리오 yaml (확장자 생략). yard 측
#                default=y0_yard_child_follow. **빈 string ""** 측 g2 측 *자동
#                시작 안 함* — manual teleop (sim/teleop_keyboard) 측 별 명령
#                측 사용자 직접 시작 패턴. 예:
#                  G2_SCENARIO="" SCENARIO=yard ./scripts/up.sh
#                  # 별 Terminal:
#                  docker exec -it llmdrone-sim ros2 run teleop_keyboard teleop_keyboard_node
#   FAST=1       대기 시간 단축
#   NO_GUI=1     T2 (gz GUI) 미사용 — 현재 미권장 (PX4 lockstep 차단)
#   SKIP_CHECK=1 마지막 스모크 생략
#   TIER1_MODE=<미설정> (default) | b0 | b1 | b1_max | b2
#                **미설정(기본) 측 tier1 launch 안 함** — 본실험 영속 셸
#                (P4-3 sim 라이프사이클, ADR-0030 D2): tier1 은 mode(b0/b1/b1_max/b2)·
#                scenario 가 trial 차원이라 per-trial 합성
#                (compose_trial_node_specs)이 소유한다. 영속 셸에 tier1 을 두면
#                per-trial tier1 과 중복(둘 다 /cmd/.._safe 발행)이라 제거.
#                b0|b1|b1_max|b2 명시 시에만 영속 tier1 launch — 수동 GUI e2e 편의.
#                (b1_max = B1b 정적 r_max, ADR-0025 amendment 19.)
#   DRONE_CAMERA=1
#                전방 카메라 중계 가동 (P1 OVD 입력 사슬, ADR-0024 Task #2 A):
#                host 측 gz_cam_relay_host.py (gz 카메라 구독 → TCP 15601) +
#                컨테이너 측 gz_cam_relay_node.py (TCP 수신 → /camera/image_raw).
#                macOS Docker Desktop 의 gz-transport 경계 차단 우회 —
#                배경은 scripts/gz_cam_relay_host.py 머리주석. 기본 0 (미가동).
#
# 종료: ./scripts/down.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
IMAGE="${IMAGE:-llmdrone-sim:latest}"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"

WAIT_PX4="${WAIT_PX4:-12}"
WAIT_T2="${WAIT_T2:-8}"
WAIT_NODES="${WAIT_NODES:-10}"

if [ "${FAST:-0}" = "1" ]; then
  WAIT_PX4=6
  WAIT_T2=3
  WAIT_NODES=5
fi

log() { echo "[up.sh] $*"; }
# warn: 비치명적 경고 (set -euo 하에서 `... || warn "..."` 가 미정의 command 로
# 중단되던 버그 수정 — 2026-06-28). world-ready 미확인·gz unpause 실패 등은
# 계속 진행 가능한 경고이므로 stderr 로 알리고 0 을 반환한다.
warn() { echo "[up.sh] WARN: $*" >&2; }

# PX4 헤드리스 stdin 블로킹 헬퍼 (pxh 콘솔 EOF-스핀 로그 폭증 방지, 세션 53).
source "$REPO_ROOT/scripts/lib_px4_stdin.sh"

# ------------------------------------------------------------------
# 0. Sanity
# ------------------------------------------------------------------
log "0/8 환경 점검 ..."

if ! command -v osascript >/dev/null 2>&1; then
  echo "ERROR: osascript 미발견 — macOS 환경에서만 동작" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker 미발견" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "ERROR: docker image '$IMAGE' 미발견" >&2
  exit 1
fi

# SCENARIO env 측 default + 허용 list 검증 + T1 측 wrapper script 측 lookup +
# user marker 좌표 lookup + g2 waypoint scenario lookup. 두 scenario 측 r_min=
# 0.9 m 동일 (cmsm-proof §7.1 P1). user 좌표 = sim_user_marker/launch/e2_sim_bridge.launch.py
# 측 _SCENARIO_USER_PARAMS 정합 (PR #142) — 본 lookup 측 *별 source-of-truth*
# 측 *직접 ros2 run* 측 paramter 전달 측 필요 (Agent 측 §2 측 이미 시작 측면 측
# launch 측 두 번째 Agent 측 conflict). G2_SCENARIO 측 g2_waypoint_player 측
# yaml (확장자 생략) 측 정합 — `sim/g2_waypoint_player/scenarios/<name>.yaml`.
SCENARIO="${SCENARIO:-livingroom}"
case "$SCENARIO" in
  livingroom)
    T1_SCRIPT="$REPO_ROOT/scripts/run_native_sitl_livingroom.sh"
    USER_X="-2.6"; USER_Y="1.5"; USER_Z="1.1"; R_MIN="0.9"
    G2_SCENARIO=""  # livingroom 측 기존 패턴 — g2 측 별 명령 측 사용자 시작 (c0/c1/c2 선택)
    ;;
  yard)
    T1_SCRIPT="$REPO_ROOT/scripts/run_native_sitl_yard.sh"
    USER_X="0.0"; USER_Y="-3.0"; USER_Z="1.1"; R_MIN="0.9"
    G2_SCENARIO="${G2_SCENARIO:-y0_yard_child_follow}"  # yard 측 default g2 시나리오
    ;;
  *)
    echo "ERROR: SCENARIO=$SCENARIO 측 unknown — 허용 = livingroom | yard" >&2
    exit 1
    ;;
esac
if [ ! -x "$T1_SCRIPT" ]; then
  echo "ERROR: T1 script 미발견 — $T1_SCRIPT" >&2
  exit 1
fi
log "    SCENARIO=$SCENARIO — T1=$(basename "$T1_SCRIPT") user=($USER_X, $USER_Y, $USER_Z) r_min=$R_MIN g2=$G2_SCENARIO"

# ------------------------------------------------------------------
# 1. Docker 컨테이너 + colcon build (먼저)
# ------------------------------------------------------------------
log "1/8 Docker 컨테이너 재생성 ($CONTAINER) ..."
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER" \
  --platform linux/arm64 \
  -p 8888:8888/udp \
  -v "$REPO_ROOT":/workspace \
  -w /workspace \
  "$IMAGE" \
  tail -f /dev/null \
  >/dev/null

log "    colcon build (scenario_params px4_msgs sim_user_marker g1_offboard g2_waypoint_player tier1_filter intent_confidence teleop_keyboard intent_ovd waypoint_follower intent_sigma_bridge) ..."
docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && colcon build --packages-select scenario_params px4_msgs sim_user_marker g1_offboard g2_waypoint_player tier1_filter intent_confidence teleop_keyboard intent_ovd waypoint_follower intent_sigma_bridge 2>&1 | tail -3"

# ------------------------------------------------------------------
# 2. MicroXRCEAgent (PX4보다 *먼저*)
# ------------------------------------------------------------------
log "2/8 MicroXRCEAgent 시작 (UDP 8888) — PX4 client가 connect할 listener 준비 ..."
docker exec -d "$CONTAINER" /opt/MicroXRCEAgent/build/MicroXRCEAgent udp4 -p 8888
sleep 2

# ------------------------------------------------------------------
# 3·4. T1 PX4 SITL + gz (HEADLESS=1: nohup·SSH 호환 / 미설정: osascript Terminal)
# ------------------------------------------------------------------
# HEADLESS=1 (SSH·맥미니 headless sweep): osascript 는 audit session 제약으로 SSH
# 세션에서 GUI 앱(Terminal·gz GUI)을 못 띄운다 → PX4 SITL 을 nohup 으로 직접 기동
# (sim_reset.sh 와 동일 패턴) + gz GUI skip + service unpause(헤드리스는 GUI 클라이언트
# 부재로 lockstep paused 시작). HEADLESS 미설정(콘솔): 기존 osascript T1/T2 경로.
if [ "${HEADLESS:-0}" = "1" ]; then
  log "3/8 PX4 SITL 헤드리스 기동 (HEADLESS=1 nohup — osascript 미사용, SSH 호환) ..."
  PX4_LOG=/tmp/px4_sitl.log
  # stdin = 블로킹 FIFO (pxh 콘솔 EOF-스핀 로그 폭증 방지, lib_px4_stdin.sh).
  # ★ command substitution 금지 — statement 호출 후 PX4_STDIN_FIFO_READY 사용.
  # `|| true` — mkfifo 실패 시 함수가 1 반환(set -e 중단 방지, /dev/null 폴백).
  px4_stdin_fifo || true
  HEADLESS=1 nohup "$T1_SCRIPT" > "$PX4_LOG" 2>&1 < "$PX4_STDIN_FIFO_READY" &
  log "    gz world-ready 폴링 (상한 ${WAIT_PX4_HEADLESS:-60}s, log=$PX4_LOG) ..."
  _ready=0
  for _i in $(seq 1 "${WAIT_PX4_HEADLESS:-60}"); do
    if grep -q "Gazebo world is ready" "$PX4_LOG" 2>/dev/null; then _ready=1; break; fi
    if grep -qE "can't load libgz|Timed out waiting for Gazebo|Startup script returned" \
         "$PX4_LOG" 2>/dev/null; then
      echo "ERROR: SITL 헤드리스 기동 실패 — $PX4_LOG tail:" >&2
      tail -5 "$PX4_LOG" >&2 2>/dev/null || true
      break
    fi
    sleep 1
  done
  [ "$_ready" = "1" ] && log "    ✓ gz world ready" || warn "    world-ready 미확인 — 계속(로그 확인)"

  log "4/8 gz GUI skip (headless) — service unpause 로 lockstep 해제 ..."
  case "$SCENARIO" in
    yard) _GZ_WORLD="${GZ_WORLD:-yard_base}" ;;
    *)    _GZ_WORLD="${GZ_WORLD:-livingroom_base}" ;;
  esac
  if docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
      "GZ_IP=127.0.0.1 gz service -s /world/$_GZ_WORLD/control \
         --reqtype gz.msgs.WorldControl --reptype gz.msgs.Boolean \
         --timeout 3000 --req 'pause: false'" >/dev/null 2>&1; then
    log "    unpause 전송 (service ack, world=$_GZ_WORLD)."
  else
    warn "    gz unpause 실패 — world 이름($_GZ_WORLD)·gz 서버 기동 확인."
  fi
else
log "3/8 T1 Terminal.app 창 생성 (PX4 SITL × $SCENARIO) ..."
T1_REL="$(basename "$T1_SCRIPT")"
osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    do script "cd '$REPO_ROOT' && ./scripts/${T1_REL}"
end tell
APPLESCRIPT

log "    PX4 부팅 대기 ${WAIT_PX4}s ..."
sleep "$WAIT_PX4"

if [ "${NO_GUI:-0}" != "1" ]; then
  log "4/8 T2 Terminal.app 창 생성 (gz GUI) ..."
  osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    do script "export GZ_IP=127.0.0.1 && gz sim -g"
end tell
APPLESCRIPT

  log "    gz GUI 부팅 대기 ${WAIT_T2}s ..."
  sleep "$WAIT_T2"

  # gz GUI(클라이언트) 연결 시 서버가 자동 진행(unpause) — ▶ 클릭·ENTER 대기 불요.
  # 2026-06-14 실측 확정: ▶ 미클릭·대기 없이 드론이 1.5 m hover 이륙. (종전 read 대기
  # 제거 — background 실행 시 stdin EOF 로 조기 종료하던 문제도 함께 해소.)
  log "    gz GUI 자동 진행 (▶ 클릭 불요) — PX4 lockstep 진행 중"
fi
fi

# ------------------------------------------------------------------
# 5. PX4 client status 확인 + 필요 시 재시작
# ------------------------------------------------------------------
log "5/8 PX4 uxrce_dds_client connect 확인 ..."
ensure_client_connected() {
  # mavlink_shell.py로 NSH 명령 send 시도. 실패 시 안내만.
  if [ ! -f "$PX4_DIR/Tools/mavlink_shell.py" ] || [ ! -f "$PX4_DIR/.venv/bin/activate" ]; then
    echo "    WARN: mavlink_shell.py 또는 PX4 venv 미발견 — client 자동 재시작 생략." >&2
    return 0
  fi
  # PX4 client에 status query → output에 "connected" 있으면 OK.
  # mavlink_shell.py는 비대화 stdin redirect에 fragile해서 timeout으로 감쌈.
  # shellcheck disable=SC1091
  source "$PX4_DIR/.venv/bin/activate"
  local out
  out=$(timeout 5 python3 "$PX4_DIR/Tools/mavlink_shell.py" udp:0.0.0.0:14540 <<< "uxrce_dds_client status" 2>&1 | tail -20 || true)
  if echo "$out" | grep -q "Running, connected"; then
    log "    ✓ Running, connected — Agent와 정상 link"
    return 0
  fi
  log "    disconnected 감지 — uxrce_dds_client 재시작 ..."
  timeout 5 python3 "$PX4_DIR/Tools/mavlink_shell.py" udp:0.0.0.0:14540 <<EOF >/dev/null 2>&1 || true
uxrce_dds_client stop
uxrce_dds_client start -t udp -p 8888 -h 127.0.0.1
EOF
  sleep 3
  log "    재시작 후 진행 (status 확인은 다음 단계에서 간접 검증)."
}
ensure_client_connected

# ------------------------------------------------------------------
# 6. SITL param 자동 완화
# ------------------------------------------------------------------
log "6/8 SITL preflight param 완화 (mavlink) ..."
if ! "$REPO_ROOT/scripts/sitl_set_params.sh"; then
  echo "    WARN: param 완화 실패 — T1 콘솔에 수동 입력 필요:" >&2
  echo "          param set NAV_DLL_ACT 0" >&2
  echo "          param set NAV_RCL_ACT 0" >&2
  echo "          param set COM_RCL_EXCEPT 4" >&2
fi

# ------------------------------------------------------------------
# 7. sim_user_marker + tier1 + g1_offboard (detached)
# ------------------------------------------------------------------
# 토픽 흐름 (ADR-0011 D1):
#   G2 → /cmd/.._nominal → [tier1 (TIER1_MODE)] → /cmd/.._safe → G1 → PX4
# TIER1_MODE 미설정(기본) = tier1 launch 안 함 — 본실험 영속 셸은 per-trial 합성이
# tier1 을 소유(ADR-0030 D2). b0|b1|b2 명시 시에만 영속 tier1 launch(수동 e2e 편의).
TIER1_MODE="${TIER1_MODE:-}"

log "7/8 sim_user_marker (scenario=$SCENARIO) + g1_offboard 시작 (background)$( [ -n "$TIER1_MODE" ] && echo " + tier1 (mode=$TIER1_MODE)" ) ..."
# v3 (PR #142 amendment): sim_user_marker 측 scenario 별 user 좌표 + r_min
# parameter 전달 — 본 host 측 §0 측 lookup table 측 SCENARIO_*_USER_* 변수 측 정합.
# `ros2 launch e2_sim_bridge.launch.py scenario:=$SCENARIO` 측 측 launch 측 두 번째
# MicroXRCEAgent 측 startup 측 port 8888 conflict 가능 (§2 측 이미 시작) →
# `ros2 run user_marker_node --ros-args -p ...` 측 직접 호출 측 conflict 회피.
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && \
   ros2 run sim_user_marker user_marker_node \
     --ros-args -p user_x:=$USER_X -p user_y:=$USER_Y -p user_z:=$USER_Z -p r_min:=$R_MIN \
     > /tmp/sim_user_marker.log 2>&1"
sleep 1
# tier1 (opt-in) — TIER1_MODE 명시 시에만 영속 launch. 본실험 영속 셸은 미설정이라
# 이 블록을 건너뛰고, per-trial 합성(compose_trial_node_specs)이 tier1 을 소유한다
# (ADR-0030 D2 중복 해소). 수동 GUI e2e 는 b0|b1|b2 명시로 영속 tier1 을 띄운다.
# v5 (PR 후속 sim-tier1-yard-launch): TIER1_MODE in {b1, b2} 측 *scenario arg*
# 측 launch 측 전달 — yard 측 user 좌표 (local 0, -1, 0.95) 측 정합. b0 측 user_local
# parameter 측 없음 측 *변경 없음 (passthrough)*. tier1_b1/b2.launch.py 측
# `_SCENARIO_USER_PARAMS` lookup (scenario=$SCENARIO).
if [ -n "$TIER1_MODE" ]; then
  if [ "$TIER1_MODE" = "b1" ] || [ "$TIER1_MODE" = "b1_max" ] || [ "$TIER1_MODE" = "b2" ]; then
    TIER1_LAUNCH_ARGS="scenario:=$SCENARIO"
  else
    TIER1_LAUNCH_ARGS=""
  fi
  docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 launch tier1_filter tier1_${TIER1_MODE}.launch.py $TIER1_LAUNCH_ARGS \
       > /tmp/tier1.log 2>&1"
  sleep 1
else
  log "    tier1 영속 launch 생략 (TIER1_MODE 미설정 — per-trial 합성이 소유)"
fi
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && \
   ros2 launch g1_offboard g1_offboard.launch.py > /tmp/g1_offboard.log 2>&1"
sleep 1

# waypoint_follower (영속 셸) — sigma_bridge 의 목표 지점 /intent/target_waypoint 를
# 포화 P-제어 연속 속도 /cmd/trajectory_setpoint_nominal (20Hz) 로 변환 → tier1 §5
# 속도 CBF (ADR-0029 D-A1 블로커 3, 연속 속도 경로). 무상태 소비자 — 입력 waypoint
# 가 없으면 idle(속도 미발행). OVD detector·sigma_bridge 는 의도 스택
# (start_intent_stack.sh)이 기동(영속 셸 일부) — 본실험 runner 의 영속/per-trial
# 조율(영속 1회 + per-trial wrapper·estimator 재시작)은 P4-3 sim 라이프사이클.
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && \
   ros2 launch waypoint_follower follower.launch.py > /tmp/waypoint_follower.log 2>&1"

# sigma_bridge (영속, opt-in) — wrapper σ(/intent/llm_sigma_raw) → 목표 지점
# /intent/target_waypoint 변환(ADR-0029 D-A1·ADR-0030 D2 영속 부류). 발화 사슬
# (per-trial 발화→wrapper→σ→sigma_bridge→follower→tier1→setpoint_safe)의 영속
# 연결 고리. **본실험 격자 영속 셸은 SIGMA_BRIDGE=1 필수**. 기본 off — 수동 e2e 는
# start_intent_stack.sh 가 자체 sigma_bridge 기동(두 개 충돌 회피). user_guard off·
# output /intent/target_waypoint 는 launch 기본값(D-A1). scenario_id 는 SIGMA_SCENARIO_ID
# (기본 livingroom→S5·yard→S8; scenario 경계 재구성은 P4-3 D3 후속).
# SIGMA_STANDOFF (기본 0.7) — 객체 standoff. **Track B(사용자 지향 적대 setpoint)는
# SIGMA_STANDOFF=0 필수** — standoff 0.7 이면 사용자 회피 영역 침입이 r_min 경계에
# 취약(ADR-0025 amendment 20 D-T3). 넓은 격자는 inspect 만 써서 무관(vantage_standoff 별).
if [ "${SIGMA_BRIDGE:-0}" = "1" ]; then
  if [ "$SCENARIO" = "yard" ]; then SIGMA_SCENARIO_ID="${SIGMA_SCENARIO_ID:-S8}";
  else SIGMA_SCENARIO_ID="${SIGMA_SCENARIO_ID:-S5}"; fi
  log "    sigma_bridge 영속 launch (scenario_id=$SIGMA_SCENARIO_ID, user_guard off, standoff=${SIGMA_STANDOFF:-0.7}) ..."
  docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 launch intent_sigma_bridge sigma_bridge.launch.py scenario_id:=$SIGMA_SCENARIO_ID \
       takeoff_altitude_m:=${SIGMA_TAKEOFF_ALT:-1.5} \
       target_standoff_m:=${SIGMA_STANDOFF:-0.7} \
       > /tmp/sigma_bridge.log 2>&1"
fi

# P1 카메라 중계 (opt-in) — host 절반(gz 구독→TCP) + 컨테이너 절반(TCP→ROS).
# host 절반은 Homebrew gz Python 바인딩 필요 (gz-harmonic 동반 설치).
if [ "${DRONE_CAMERA:-0}" = "1" ]; then
  log "    DRONE_CAMERA=1 — 카메라 중계 시작 (host relay + 컨테이너 relay node) ..."
  # GZ_RELAY_PY: gz Python 바인딩 + protobuf 가 보이는 host python
  # (준비 절차는 scripts/gz_cam_relay_host.py 머리주석). -u = 로그 즉시 flush.
  GZ_RELAY_PY="${GZ_RELAY_PY:-$HOME/.venvs/llmdrone-gz/bin/python3}"
  if [ ! -x "$GZ_RELAY_PY" ]; then GZ_RELAY_PY="python3"; fi
  pkill -f gz_cam_relay_host.py 2>/dev/null || true
  GZ_IP=127.0.0.1 nohup "$GZ_RELAY_PY" -u "$REPO_ROOT/scripts/gz_cam_relay_host.py" \
    --topic /drone/front_camera/image --port 15601 \
    > /tmp/gz_cam_relay_host.log 2>&1 &
  docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     python3 /workspace/scripts/gz_cam_relay_node.py > /tmp/gz_cam_relay_node.log 2>&1"
fi

# OVD detector (opt-in) — 본실험 영속 셸. 카메라 영상(/camera/image_raw) → 객체
# 후보 검출 → /intent/ovd/detections (estimator s1 입력, ADR-0029 D-A2). 종전엔
# start_intent_stack 가 OVD 를 기동(수동 e2e)이라 본실험 격자 영속 셸은 OVD
# detector standalone 을 따로 기동해야 했다 — OVD=1 로 up.sh 가 기동하도록 통합
# (wrapper·estimator 는 per-trial 소유, D-A3). DRONE_CAMERA=1 과 함께 써야 영상
# 입력이 있다. 의존(ultralytics·clip)은 이미지 bake (docker/Dockerfile §5.5).
if [ "${OVD:-0}" = "1" ]; then
  if [ "${DRONE_CAMERA:-0}" != "1" ]; then
    echo "    WARN: OVD=1 인데 DRONE_CAMERA!=1 — 카메라 영상 없어 검출 0. DRONE_CAMERA=1 권장." >&2
  fi
  # OVD 정적 어휘 단일 진실 소스 = scenario_params.scene (scene ``ovd_class`` 파생).
  # 영속 셸은 OVD detector 한 인스턴스로 전 시나리오(S5–S8 = 거실+마당)를 서빙하므로
  # 전 장소 합집합 {chair,cup,person,sofa,table} 를 쓴다. 종전 하드코딩
  # ['couch','table','chair'] 는 거실 referent 'sofa'·마당 'person' 을 빠뜨려
  # S5/S6/S8 grounding 영구 실패(검출 0→s1≈0→c=0→B4 게이트 전부 reject, 세션 53
  # B4 게이트 sim e2e 적발) → scene 에서 파생해 drift 차단. OVD_VOCAB 명시 시 우선.
  if [ -z "${OVD_VOCAB:-}" ]; then
    OVD_VOCAB="$(docker exec "$CONTAINER" bash -c \
      'cd /workspace && source install/setup.bash >/dev/null 2>&1 && python3 -c "from scenario_params.scene import ovd_vocabulary_launch_str; print(ovd_vocabulary_launch_str())"' 2>/dev/null | tail -n1)"
    # 파생 실패(소스 누락 등) 시 정합 합집합 fallback (scene 단일 소스와 동일 값).
    OVD_VOCAB="${OVD_VOCAB:-['chair','cup','person','sofa','table']}"
  fi
  OVD_THROTTLE_HZ="${OVD_THROTTLE_HZ:-5.0}"
  log "    OVD=1 — OVD detector launch (vocab=$OVD_VOCAB, throttle=${OVD_THROTTLE_HZ}Hz) ..."
  docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     mkdir -p /workspace/models/ovd && cd /workspace/models/ovd && \
     ros2 launch intent_ovd ovd_detector.launch.py \
       device:=cpu throttle_hz:=$OVD_THROTTLE_HZ vocabulary:=\"$OVD_VOCAB\" \
       > /tmp/ovd_detector.log 2>&1"
fi

log "    state machine 진행 대기 (CLIMB → ACTIVE까지 ~${WAIT_NODES}s) ..."
sleep "$WAIT_NODES"

# v4 (PR #143 별 PR sim-yard-g2-waypoint): SCENARIO=yard 측 default g2 시나리오
# (y0_yard_child_follow) 측 자동 시작 — *한 명령 측 yard 완전 비행*. livingroom
# 측 기존 패턴 유지 (g2 측 사용자 별 명령 측 c0/c1/c2 선택). G2_SCENARIO env
# override 가능. g2 측 state machine ACTIVE 직후 측 시작 측 정합 — 위 sleep
# "$WAIT_NODES" 측 CLIMB → ACTIVE 측 완료 보장.
if [ -n "$G2_SCENARIO" ]; then
  log "    g2_waypoint_player 측 scenario=$G2_SCENARIO 시작 (background) ..."
  docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 launch g2_waypoint_player g2_play.launch.py scenario:=$G2_SCENARIO \
       > /tmp/g2_waypoint_player.log 2>&1"
  sleep 1
fi

# ------------------------------------------------------------------
# 8. 스모크 검증
# ------------------------------------------------------------------
if [ "${SKIP_CHECK:-0}" != "1" ]; then
  log "8/8 check_g1_smoke.sh ..."
  if "$REPO_ROOT/scripts/check_g1_smoke.sh"; then
    log "✓ G1 세션 완전 가동 — gz GUI에서 hover 확인."
  else
    echo "WARN: 스모크 일부 FAIL. 로그 확인:" >&2
    echo "  docker exec $CONTAINER cat /tmp/g1_offboard.log | tail -20" >&2
    exit 1
  fi
else
  log "8/8 스모크 검증 생략 (SKIP_CHECK=1)"
fi

cat <<EOF

[up.sh] 가동 완료.

진단:
  docker exec $CONTAINER cat /tmp/g1_offboard.log | tail -20
  docker exec $CONTAINER cat /tmp/sim_user_marker.log | tail -10$( [ -n "$TIER1_MODE" ] && echo "
  docker exec $CONTAINER cat /tmp/tier1.log | tail -10" )$( [ -n "$G2_SCENARIO" ] && echo "
  docker exec $CONTAINER cat /tmp/g2_waypoint_player.log | tail -10" )

추가 검증 (nominal velocity 흘려보내기 — tier1을 거쳐 G1으로):
  docker exec $CONTAINER /usr/local/bin/entrypoint.sh bash -c \\
    "source /opt/ros/humble/setup.bash && source /workspace/install/setup.bash && \\
     ros2 topic pub --rate 10 --times 50 /cmd/trajectory_setpoint_nominal \\
       geometry_msgs/msg/TwistStamped \\
       '{header: {frame_id: world}, twist: {linear: {x: 0.5}}}'"

종료: ./scripts/down.sh
EOF
