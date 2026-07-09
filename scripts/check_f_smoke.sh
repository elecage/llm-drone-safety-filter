#!/usr/bin/env bash
# check_f_smoke.sh — Sim 트랙 F 통합 스모크 검증.
#
# 전제조건 (이 스크립트 실행 전 모두 실행 중이어야 함):
#   T1: ./scripts/run_native_sitl_livingroom.sh  (macOS, PX4 SITL)
#   T2: export GZ_IP=127.0.0.1 && gz sim -g     (macOS, Gazebo GUI — unpaused 상태)
#   T3: ./docker/run.sh "colcon build --packages-select sim_user_marker && \
#         source install/setup.bash && \
#         ros2 launch sim_user_marker e2_sim_bridge.launch.py"
#
# T3가 silent termination되면 우회 경로:
#   T3a: ./docker/run.sh "$MICROXRCE_AGENT_BIN udp4 -p 8888"
#   T3b: docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
#            "cd /workspace && colcon build --packages-select sim_user_marker && \
#            source install/setup.bash && ros2 run sim_user_marker user_marker_node"
#
# 실행:
#   chmod +x scripts/check_f_smoke.sh
#   ./scripts/check_f_smoke.sh
#
# 환경변수:
#   RETRY_ATTEMPTS (기본 3) — 각 검증의 최대 시도 횟수.
#   RETRY_WAIT (기본 5) — 재시도 사이 대기 시간 (초).
#
# 첫 실행에서 [1][2] 같은 토픽 수신 검증이 FAIL되는 경우 — agent register 진행
# 중이거나 ros2 cli cold-start discovery 윈도에 빠질 가능성. 이런 false-negative를
# 흡수하기 위해 각 검증이 자체적으로 재시도한다 (2026-05-24 attempt 실측).
# 안정화 30s 이상 지난 후에도 FAIL이 반복되면 T1·T2 상태 점검 필요.

set -euo pipefail

CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
TIMEOUT_SEC=8
# 초기 publish 안정화 윈도 — agent register 직후 ~30s 동안 ros2 cli가 cold-start
# discovery로 메시지를 못 잡는 경우 대비. 환경변수로 조정 가능.
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-3}"
RETRY_WAIT="${RETRY_WAIT:-5}"
# docker exec는 ENTRYPOINT를 거치지 않으므로 entrypoint.sh를 명시적으로 호출.
DEXEC="docker exec $CONTAINER /usr/local/bin/entrypoint.sh bash -c"

pass() { echo "  [PASS] $*"; }
fail() { echo "  [FAIL] $*"; FAILED=$((FAILED + 1)); }

# attempt_check <pass-label> <fail-label> <DEXEC bash-c subcommand>
# 검증 1회 실행 후 실패 시 ${RETRY_WAIT}s 간격으로 최대 ${RETRY_ATTEMPTS}회 재시도.
# 첫 성공 시 즉시 PASS. 모든 시도 실패 시 FAIL.
# 이유: agent register 진행 중에 first-call timeout 8s가 빠듯해 publish가
# 안정화되기 전 false-negative 발생. 2026-05-24 F 스모크 attempt에서 실측.
attempt_check() {
  local pass_label="$1"
  local fail_label="$2"
  local cmd="$3"
  local attempt
  for attempt in $(seq 1 "$RETRY_ATTEMPTS"); do
    if $DEXEC "$cmd" 2>/dev/null; then
      if [ "$attempt" -gt 1 ]; then
        pass "$pass_label (retry $attempt/$RETRY_ATTEMPTS)"
      else
        pass "$pass_label"
      fi
      return 0
    fi
    if [ "$attempt" -lt "$RETRY_ATTEMPTS" ]; then
      echo "    (retry $attempt/$RETRY_ATTEMPTS — ${RETRY_WAIT}s 대기)"
      sleep "$RETRY_WAIT"
    fi
  done
  fail "$fail_label"
  return 1
}

FAILED=0

echo "=========================================================="
echo " Sim 트랙 F — 통합 스모크 검증"
echo "=========================================================="

# ------------------------------------------------------------------
# 0. 컨테이너 실행 확인
# ------------------------------------------------------------------
echo ""
echo "[0] 컨테이너 상태 확인..."
if docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  pass "컨테이너 '$CONTAINER' 실행 중"
else
  echo "  [ERROR] 컨테이너 '$CONTAINER'가 없거나 중지됨."
  echo "          T3를 먼저 실행하세요."
  exit 1
fi

# ------------------------------------------------------------------
# 1. /fmu/out/vehicle_attitude (E2 기준선)
# ------------------------------------------------------------------
echo ""
echo "[1] /fmu/out/vehicle_attitude 수신 확인 (E2 기준선)..."
attempt_check \
  "/fmu/out/vehicle_attitude 수신 OK" \
  "/fmu/out/vehicle_attitude 수신 실패 — MicroXRCEAgent·PX4 연결 확인" \
  "timeout $TIMEOUT_SEC ros2 topic echo --qos-reliability best_effort --once /fmu/out/vehicle_attitude 2>/dev/null | grep -q 'timestamp'"

# ------------------------------------------------------------------
# 2. /fmu/out/vehicle_local_position (F 신규)
# ------------------------------------------------------------------
echo ""
echo "[2] /fmu/out/vehicle_local_position 수신 확인 (F 신규)..."
attempt_check \
  "/fmu/out/vehicle_local_position_v1 수신 OK" \
  "/fmu/out/vehicle_local_position_v1 수신 실패" \
  "timeout $TIMEOUT_SEC ros2 topic echo --qos-reliability best_effort --once /fmu/out/vehicle_local_position_v1 2>/dev/null | grep -q 'timestamp'"

# ------------------------------------------------------------------
# 3. TF world→user
# ------------------------------------------------------------------
echo ""
echo "[3] TF world→user 확인..."
attempt_check \
  "TF world→user 발행 OK" \
  "TF world→user 없음 — user_marker_node 실행 확인" \
  "timeout $TIMEOUT_SEC ros2 run tf2_ros tf2_echo world user 2>/dev/null | grep -q 'Translation'"

# ------------------------------------------------------------------
# 4. /visualization_marker 토픽
# ------------------------------------------------------------------
echo ""
echo "[4] /user_avoidance_zone 마커 토픽 확인..."
attempt_check \
  "/user_avoidance_zone 수신 OK" \
  "/user_avoidance_zone 없음 — user_marker_node 실행 확인" \
  "timeout $TIMEOUT_SEC ros2 topic echo --once /user_avoidance_zone 2>/dev/null | grep -q 'header'"

# ------------------------------------------------------------------
# 5. 토픽 목록 요약
# ------------------------------------------------------------------
echo ""
echo "[5] /fmu/ 토픽 목록..."
FMU_TOPICS=""
for attempt in $(seq 1 "$RETRY_ATTEMPTS"); do
  FMU_TOPICS=$($DEXEC "ros2 topic list 2>/dev/null | grep '/fmu/'" 2>/dev/null || true)
  if [ -n "$FMU_TOPICS" ]; then break; fi
  if [ "$attempt" -lt "$RETRY_ATTEMPTS" ]; then
    echo "    (retry $attempt/$RETRY_ATTEMPTS — ${RETRY_WAIT}s 대기)"
    sleep "$RETRY_WAIT"
  fi
done
if [ -n "$FMU_TOPICS" ]; then
  pass "/fmu/ 토픽 발견:"
  echo "$FMU_TOPICS" | sed 's/^/         /'
else
  fail "/fmu/ 토픽 없음"
fi

# ------------------------------------------------------------------
# 결과 요약
# ------------------------------------------------------------------
echo ""
echo "=========================================================="
if [ "$FAILED" -eq 0 ]; then
  echo " F 스모크 통과 — 전 항목 PASS"
else
  echo " F 스모크 실패 — $FAILED 항목 FAIL"
  echo " 위 FAIL 항목을 확인하고 T1/T2/T3 상태를 점검하세요."
fi
echo "=========================================================="
exit "$FAILED"
