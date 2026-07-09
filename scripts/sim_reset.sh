#!/usr/bin/env bash
# sim_reset.sh — 본실험 trial 간 sim 리셋 (ADR-0030 D1·D4).
#
# 한 sim 위에서 1,000 trial 격자를 순차 실행할 때, **trial 사이**에 호출되어
# PX4 SITL + Gazebo 를 종료·재기동한다. 드론 위치·EKF·gz 월드가 초기 상태로
# 복원되어 trial 독립성·재현성이 보장된다 (ADR-0030 D1 — gz reset/set_pose 비채택).
#
# 영속 노드(MicroXRCEAgent·OVD detector·sigma_bridge·waypoint_follower·g1_offboard·
# user_marker)는 **종료하지 않는다** — PX4 micro-XRCE-DDS 재연결로 새 SITL 에 자동
# 재결합한다 (ADR-0030 D2 + 2026-06-14 세션 45 amendment 실측: ROS 노드 재시작 불요).
# per-trial 노드(tier1·wrapper·estimator·injector·rosbag)는 runner 가 trial 종료 시
# teardown 하므로 본 스크립트 밖 — sim_reset 진입 전 teardown 완료를 전제한다.
#
# 확정 시퀀스 (ADR-0030 amendment 2026-06-14 실측):
#   per-trial 노드 teardown 확인 (호출측 보장)
#   → host PX4+gz kill + *소멸 폴링* (영속 노드 보존; 잔존 gz 가 새 서버 기동을
#     깨뜨리는 race 차단 — 방어적 강건화)
#   → run_native_sitl_<scenario>.sh 재실행 (PX4 + gz 서버 재기동, 헤드리스) +
#     world-ready 신호 폴링·실패 시 재시도 (방어적 강건화)
#   → gz unpause (lockstep 해제 — 필수; 헤드리스라 GUI 클라이언트 부재로 paused 시작)
#
# ⚠️ F11 격자 루프 근본 원인 (2026-06-14 실측): 위 race/env 가 아니라 **호출측이
#   anaconda python 이면** 자손 gz 서버 dlopen 이 dyld 수준으로 오염 (execv·SIP
#   재-exec 로도 안 끊김) → "can't load libgz-sim8". **run_grid.py 는 .venv python
#   으로 실행해야 한다**(가드 내장). 본 스크립트의 kill/retry 는 별개의 방어적 강건화.
#   → 영속 노드 자동 재연결 (Agent 중계) + g1 재-arm·ACTIVE (~8 s)
#   → EKF 수렴·드론 안정 대기 (폴링: /fmu/out 유효성 + g1 ACTIVE 로그)
#
# ⚠️ host-side 전용 — 호출 위치는 macOS host (PX4/gz 가 native, ADR-0008). 컨테이너
# 안 runner 와의 경계 wiring(run_all → 본 스크립트 발동)은 ADR-0030 이행 4 에서 확정.
#
# 환경변수:
#   SCENARIO=livingroom (default) | yard   — T1 SITL wrapper + gz world 선택.
#   CONTAINER_NAME=llmdrone-sim            — gz unpause·폴링 docker exec 대상.
#   RESET_TIMEOUT=60                       — 재연결·ACTIVE 대기 상한 [s].
#   POLL_INTERVAL=2                        — 폴링 간격 [s].
#   SITL_BOOT_TIMEOUT=50                   — gz world-ready 1회 시도 대기 상한 [s].
#   SITL_MAX_ATTEMPTS=3                    — SITL 재기동 재시도 횟수 (race 흡수).
#   NO_WAIT=1                              — 재기동만 하고 ACTIVE 폴링 생략 (디버그).
#
# 종료 코드: 0 = sim 재기동 + g1 ACTIVE 확인. 1 = 재기동 실패 또는 ACTIVE 미도달.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
SCENARIO="${SCENARIO:-livingroom}"
RESET_TIMEOUT="${RESET_TIMEOUT:-60}"
POLL_INTERVAL="${POLL_INTERVAL:-2}"
RESET_LOG="${RESET_LOG:-/tmp/sim_reset_sitl.log}"
SITL_BOOT_TIMEOUT="${SITL_BOOT_TIMEOUT:-50}"   # gz world-ready 대기 상한 [s].
SITL_MAX_ATTEMPTS="${SITL_MAX_ATTEMPTS:-3}"    # SITL 재기동 재시도 횟수.

log() { echo "[sim_reset] $*"; }
warn() { echo "[sim_reset] WARN: $*" >&2; }

# PX4 헤드리스 stdin 블로킹 헬퍼 (pxh 콘솔 EOF-스핀 로그 폭증 방지, 세션 53).
source "$REPO_ROOT/scripts/lib_px4_stdin.sh"

# ------------------------------------------------------------------
# 헬퍼: SITL 자식 env 정규화 · sim 완전 종료 · 재기동 1회 시도 (F11)
# ------------------------------------------------------------------

# SITL 자식 env 정규화 — *부차적 방어*. F11 격자 루프 근본 원인은 env 도 race 도
# 아니라 호출측 anaconda python 의 dyld 오염이었다(상단 ⚠️ + run_grid.py 가드).
# 진단 기록(2026-06-14 실측):
#   - clean 상태에선 동일 nohup 경로가 12 s 에 "Gazebo world is ready" 정상 →
#     race 는 본질이 아님(다만 잔존 gz 차단은 여전히 좋은 위생이라 kill_sim 유지).
#   - macOS SIP 는 시스템 ruby(`/usr/bin/ruby`, gz CLI 의 `env ruby`)에서
#     DYLD_FALLBACK_LIBRARY_PATH 를 *로그인·비로그인 모두* strip(실측 nil) →
#     DYLD 명시 전파는 ruby tool 엔 무효(up.sh 도 strip 상태로 정상 동작).
#   - anaconda python 자손은 .venv/system python 과 달리 gz dlopen 실패(environ
#     diff 는 `_` 외 동일 → 비환경적 dyld 상속, execv·SIP bash 재-exec 로도 안 끊김).
# 그럼에도 (a) brew/gz 가 PATH 에 있게 보장하고, (b) 부모(ROS/colcon sourced)가
# 물려준 DYLD_LIBRARY_PATH 가 gz_bridge 등 *비-SIP* C++ 자식의 dylib 해석을 오염시킬
# 여지를 제거하기 위해 정규화한다(둘 다 무해·방어적).
setup_sitl_env() {
  export HOMEBREW_PREFIX="${HOMEBREW_PREFIX:-$(/opt/homebrew/bin/brew --prefix 2>/dev/null || echo /opt/homebrew)}"
  export DYLD_FALLBACK_LIBRARY_PATH="$HOMEBREW_PREFIX/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"
  export PATH="$HOMEBREW_PREFIX/bin:/usr/local/bin:$PATH"
  # native gz 는 DYLD_LIBRARY_PATH 불요 — 부모가 물려준 경로는 잘못된 dylib 로드를
  # 유발하므로 제거.
  unset DYLD_LIBRARY_PATH
}

# 모든 host PX4 SITL + gz 프로세스 종료 후 *완전 소멸 확인*. 격자 루프는 직전 trial
# 의 sim 을 죽이고 즉시 재기동하므로, 잔존 gz 서버(transport·포트 점유)가 새 서버
# 기동을 깨뜨리지 않게 polling 으로 소멸을 보장한다(영속 노드·컨테이너는 보존).
SIM_PATTERNS=(
  "px4_sitl_default/bin/px4" "PX4_SIM_MODEL"
  "gz sim" "ruby.*gz sim" "gz-sim-server" "gz-sim-gui"
)
kill_sim() {
  local p
  for p in "${SIM_PATTERNS[@]}"; do pkill -TERM -f "$p" 2>/dev/null || true; done
  sleep 1
  local i alive
  for i in $(seq 1 10); do
    alive=0
    for p in "${SIM_PATTERNS[@]}"; do
      if pgrep -f "$p" >/dev/null 2>&1; then
        alive=1
        pkill -9 -f "$p" 2>/dev/null || true
      fi
    done
    [ "$alive" -eq 0 ] && return 0
    sleep 1
  done
  warn "kill_sim: 강제 종료 후에도 잔존 sim 프로세스 — 재기동이 불안정할 수 있음."
  return 0
}

# SITL 헤드리스 재기동 1회 → px4-rc.gzsim 의 gz world-ready 신호를 RESET_LOG 에서
# 폴링. 성공("Gazebo world is ready") 0 / 실패(timeout·dylib·프로세스 사망·상한) 1.
start_sitl_once() {
  : > "$RESET_LOG"
  # stdin = 블로킹 FIFO (pxh 콘솔 EOF-스핀 로그 폭증 방지, lib_px4_stdin.sh).
  # ★ command substitution 금지 — statement 호출 후 PX4_STDIN_FIFO_READY 사용.
  px4_stdin_fifo
  HEADLESS=1 nohup "$T1_SCRIPT" > "$RESET_LOG" 2>&1 < "$PX4_STDIN_FIFO_READY" &
  SITL_PID=$!
  log "    SITL pid=$SITL_PID (log: $RESET_LOG)"
  local deadline=$((SECONDS + SITL_BOOT_TIMEOUT))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if grep -q "Gazebo world is ready" "$RESET_LOG" 2>/dev/null; then
      log "    ✓ gz world ready (px4-rc.gzsim)."
      return 0
    fi
    if grep -qE "Timed out waiting for Gazebo world|can't load libgz|Startup script returned" \
         "$RESET_LOG" 2>/dev/null; then
      warn "    SITL 기동 실패 신호 (gz world/dylib) 감지."
      return 1
    fi
    if ! kill -0 "$SITL_PID" 2>/dev/null; then
      warn "    SITL 프로세스 조기 종료."
      return 1
    fi
    sleep 1
  done
  warn "    SITL world-ready 상한(${SITL_BOOT_TIMEOUT}s) 초과."
  return 1
}

# ------------------------------------------------------------------
# 0a. scenario → T1 SITL wrapper + gz world 이름 lookup (up.sh 와 정합)
# ------------------------------------------------------------------
case "$SCENARIO" in
  livingroom)
    T1_SCRIPT="$REPO_ROOT/scripts/run_native_sitl_livingroom.sh"
    GZ_WORLD="${GZ_WORLD:-livingroom_base}"
    ;;
  yard)
    T1_SCRIPT="$REPO_ROOT/scripts/run_native_sitl_yard.sh"
    GZ_WORLD="${GZ_WORLD:-yard_base}"
    ;;
  *)
    echo "ERROR: SCENARIO=$SCENARIO unknown — 허용 = livingroom | yard" >&2
    exit 1
    ;;
esac
if [ ! -x "$T1_SCRIPT" ]; then
  echo "ERROR: T1 SITL script 미발견 — $T1_SCRIPT" >&2
  exit 1
fi

# ------------------------------------------------------------------
# 0b. SITL 자식 env 정규화 (F11 부차 방어 — DYLD 오염 제거 + brew/gz PATH 보장)
# ------------------------------------------------------------------
setup_sitl_env

# ------------------------------------------------------------------
# 1. host PX4 SITL + gz kill (down.sh step 4·5 와 동일 — 영속 노드·컨테이너 보존)
# ------------------------------------------------------------------
log "1/4 host PX4 SITL + gz 종료 (소멸 확인, 영속 노드·컨테이너 보존) ..."
kill_sim

# ------------------------------------------------------------------
# 2. PX4 SITL + gz 서버 재기동 (헤드리스 background + world-ready 재시도)
# ------------------------------------------------------------------
# 본실험은 NO_GUI/HEADLESS 권장 (ADR-0030 amendment). T1_SCRIPT 가 HEADLESS=1 기본
# 이므로 gz GUI 없이 서버만 뜬다 — GUI 클라이언트 부재로 lockstep paused 상태로 시작,
# step 3 에서 service unpause 로 해제한다. nohup background 로 띄워 본 스크립트는
# world-ready(px4-rc.gzsim 신호)를 폴링한다. 실패 시 kill 후 재시도(F11: 격자 루프
# 의 SITL 재기동 비신뢰성 — dylib 로드 실패·잔존 gz race 흡수).
log "2/4 PX4 SITL + gz 서버 재기동 (HEADLESS, env 정규화 + 재시도, scenario=$SCENARIO) ..."
attempt=1
while :; do
  log "    시도 $attempt/$SITL_MAX_ATTEMPTS ..."
  if start_sitl_once; then
    break
  fi
  warn "    SITL 기동 실패 (시도 $attempt) — 로그 tail:"
  tail -5 "$RESET_LOG" >&2 2>/dev/null || true
  attempt=$((attempt + 1))
  if [ "$attempt" -gt "$SITL_MAX_ATTEMPTS" ]; then
    echo "ERROR: SITL 재기동 ${SITL_MAX_ATTEMPTS}회 모두 실패 — 격자 중단. 로그: $RESET_LOG" >&2
    exit 1
  fi
  kill_sim
  sleep 2
done

# ------------------------------------------------------------------
# 3. gz unpause (lockstep 해제 — 필수)
# ------------------------------------------------------------------
# 헤드리스 서버는 GUI 클라이언트 연결이 없어 paused 로 시작 → PX4 가 setpoint 를
# 보내도 드론 물리 미진행 (z≈0 유지). gz service 로 unpause (P4-1 발견).
# ⚠️ host 에 `timeout` 부재 — gz CLI 는 컨테이너 안에서 실행 (gz-transport 가 host
# ↔컨테이너 동일 partition; up.sh 가 GZ_IP=127.0.0.1 로 통일). gz service 자체에
# --timeout(ms) 옵션 있어 무한 대기 회피.
log "3/4 gz unpause (/world/$GZ_WORLD/control) ..."
if docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "GZ_IP=127.0.0.1 gz service -s /world/$GZ_WORLD/control \
       --reqtype gz.msgs.WorldControl --reptype gz.msgs.Boolean \
       --timeout 3000 --req 'pause: false'" >/dev/null 2>&1; then
  log "    unpause 요청 전송 (service ack)."
else
  warn "gz unpause service 호출 실패 — world 이름($GZ_WORLD) 또는 gz 서버 기동 확인."
fi

# ------------------------------------------------------------------
# 4. 영속 노드 재연결 + g1 ACTIVE 폴링 (EKF 수렴·드론 안정)
# ------------------------------------------------------------------
if [ "${NO_WAIT:-0}" = "1" ]; then
  log "4/4 ACTIVE 폴링 생략 (NO_WAIT=1) — 재기동만 수행."
  exit 0
fi

log "4/4 영속 노드 재연결 + 드론 재이륙 대기 (상한 ${RESET_TIMEOUT}s) ..."
# 판정 2종:
#  (a) /fmu/out/vehicle_local_position_v1 재발행 (Agent 재연결 + EKF valid).
#  (b) 드론 *재이륙* — local position z(NED, 아래 양수) < -1.0 = 고도 1 m 초과.
#      g1 의 disarm 감지 재-arm·재climb(ADR-0030 F6 fix) 완료를 *실측*으로 확인.
#      종전 `tail|grep ACTIVE` 는 리셋 전 stale 라인 매칭 false positive(F6 발견) →
#      실 고도 확인으로 교체.
deadline=$((SECONDS + RESET_TIMEOUT))
fmu_ok=0
airborne=0
while [ "$SECONDS" -lt "$deadline" ]; do
  # z(NED) 한 번 읽기 — 재발행(fmu_ok) + 고도(airborne) 동시 판정.
  # `ros2 topic echo --once --field z` 는 값 한 줄 + 구분자 `---` 줄을 출력 →
  # head -1 로 값 줄만(`---` 제거; tr 는 공백만 지워 `-` 가 남는 문제 회피, F7 발견).
  # ⚠️ `--once` 는 메시지 1개 올 때까지 *무한 대기* — VLP 재발행이 (간헐적으로)
  # 실패하면 이 한 줄에서 영영 블록되어 아래 while 의 RESET_TIMEOUT 상한이 *작동
  # 못 한다*(F12: trial 57→58 리셋에서 70분 무한 정지 실측, 2026-06-15). 컨테이너
  # `timeout` 으로 감싸 매 폴링이 ≤5s 에 반환 → while 가 deadline 을 실제로 검사 →
  # VLP 미회복 시 60s 후 exit 1(상위 run_grid 가 WARN 후 다음 trial 진행).
  z=$(docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
        "source /opt/ros/humble/setup.bash && source /workspace/install/setup.bash && \
         timeout 5 ros2 topic echo --qos-reliability best_effort --once --field z \
           /fmu/out/vehicle_local_position_v1 2>/dev/null" 2>/dev/null \
        | head -1 | tr -d '[:space:]')
  if [ -n "$z" ]; then
    if [ "$fmu_ok" -eq 0 ]; then
      fmu_ok=1
      log "    ✓ /fmu/out/vehicle_local_position_v1 재발행 (Agent 재연결), z=$z"
    fi
    # z < -1.0 (NED) = 고도 1 m 초과 = 재이륙 완료.
    if awk "BEGIN{exit !($z < -1.0)}" 2>/dev/null; then
      airborne=1
      log "    ✓ 드론 재이륙 (z=$z, NED) — g1 재-arm·재climb 완료."
    fi
  fi
  if [ "$fmu_ok" -eq 1 ] && [ "$airborne" -eq 1 ]; then
    log "✓ sim 리셋 완료 — 다음 trial 진행 가능."
    exit 0
  fi
  sleep "$POLL_INTERVAL"
done

warn "리셋 대기 상한(${RESET_TIMEOUT}s) 초과 — fmu_ok=$fmu_ok airborne=$airborne (z=${z:-?})."
warn "  진단: docker exec $CONTAINER cat /tmp/g1_offboard.log | tail -30"
warn "       cat $RESET_LOG | tail -30"
exit 1
