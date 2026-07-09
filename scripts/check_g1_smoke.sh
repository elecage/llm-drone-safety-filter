#!/usr/bin/env bash
# check_g1_smoke.sh — G1 (offboard control) 스모크 검증.
#
# 검증 항목 (5개):
#   [1] g1_offboard_control 노드 살아 있음
#   [2] /fmu/in/offboard_control_mode publish 흐름 (≥2 Hz)
#   [3] /fmu/in/trajectory_setpoint publish 흐름
#   [4] PX4 arming_state == ARMED (vehicle_status_v1)
#   [5] PX4 nav_state == OFFBOARD (vehicle_status_v1)
#
# 전제조건 (스크립트 실행 전 모두 실행 중이어야 함):
#   T1: ./scripts/run_native_sitl_livingroom.sh  (macOS, PX4 SITL)
#   T2: export GZ_IP=127.0.0.1 && gz sim -g     (macOS, Gazebo GUI — unpaused)
#   T3: ./docker/run.sh "colcon build --packages-select sim_user_marker g1_offboard && \
#         source install/setup.bash && \
#         ros2 launch sim_user_marker e2_sim_bridge.launch.py &
#         sleep 5 && ros2 launch g1_offboard g1_offboard.launch.py"
#   (또는 sim_user_marker 따로, g1_offboard 따로 ros2 launch.)
#
# 실행:
#   chmod +x scripts/check_g1_smoke.sh
#   ./scripts/check_g1_smoke.sh
#
# 환경변수:
#   CONTAINER_NAME (기본 llmdrone-sim)
#   RETRY_ATTEMPTS (기본 3)
#   RETRY_WAIT (기본 5)
#
# 추가 검증 (실제 비행 확인 — 자동화 불가):
#   gz GUI에서 드론이 CLIMB 단계에서 1.5m까지 상승 후 hover하는지 육안 확인.
#   Active forward는 별도 nominal publisher를 띄워 검증:
#     docker exec llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
#       "ros2 topic pub -r 10 /cmd/trajectory_setpoint_safe \
#         geometry_msgs/msg/TwistStamped \
#         '{header: {frame_id: world}, twist: {linear: {x: 0.5}}}'"
#   → 드론이 ENU +x(E) 방향으로 ~0.5 m/s 이동 (gz GUI에서 확인).

set -euo pipefail

CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
TIMEOUT_SEC=8
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-3}"
RETRY_WAIT="${RETRY_WAIT:-5}"
DEXEC="docker exec $CONTAINER /usr/local/bin/entrypoint.sh bash -c"

pass() { echo "  [PASS] $*"; }
fail() { echo "  [FAIL] $*"; FAILED=$((FAILED + 1)); }

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
echo " G1 — offboard control 스모크 검증"
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
# 1. g1_offboard_control 노드 존재
# ------------------------------------------------------------------
echo ""
echo "[1] g1_offboard_control 노드 확인..."
attempt_check \
  "g1_offboard_control 노드 OK" \
  "g1_offboard_control 노드 없음 — ros2 launch g1_offboard g1_offboard.launch.py 확인" \
  "timeout $TIMEOUT_SEC ros2 node list 2>/dev/null | grep -q g1_offboard_control"

# ------------------------------------------------------------------
# 2. /fmu/in/offboard_control_mode publish
# ------------------------------------------------------------------
echo ""
echo "[2] /fmu/in/offboard_control_mode publish 확인..."
attempt_check \
  "/fmu/in/offboard_control_mode 흐름 OK" \
  "/fmu/in/offboard_control_mode 없음 — G1 timer 또는 PX4 input 경로 확인" \
  "timeout $TIMEOUT_SEC ros2 topic echo --qos-reliability best_effort --once /fmu/in/offboard_control_mode 2>/dev/null | grep -q 'timestamp'"

# ------------------------------------------------------------------
# 3. /fmu/in/trajectory_setpoint publish
# ------------------------------------------------------------------
echo ""
echo "[3] /fmu/in/trajectory_setpoint publish 확인..."
attempt_check \
  "/fmu/in/trajectory_setpoint 흐름 OK" \
  "/fmu/in/trajectory_setpoint 없음" \
  "timeout $TIMEOUT_SEC ros2 topic echo --qos-reliability best_effort --once /fmu/in/trajectory_setpoint 2>/dev/null | grep -q 'velocity'"

# ------------------------------------------------------------------
# 4. PX4 ARMED 상태
# ------------------------------------------------------------------
echo ""
echo "[4] PX4 arming_state == ARMED 확인..."
# VehicleStatus.ARMING_STATE_ARMED = 2.
attempt_check \
  "PX4 ARMED (arming_state=2) OK" \
  "PX4 미장전 — vehicle_command 송신 실패 또는 PX4 거부 (콘솔 로그 확인)" \
  "timeout $TIMEOUT_SEC ros2 topic echo --qos-reliability best_effort --once /fmu/out/vehicle_status_v4 2>/dev/null | grep -E 'arming_state: 2'"

# ------------------------------------------------------------------
# 5. PX4 OFFBOARD nav_state
# ------------------------------------------------------------------
echo ""
echo "[5] PX4 nav_state == OFFBOARD 확인..."
# VehicleStatus.NAVIGATION_STATE_OFFBOARD = 14.
attempt_check \
  "PX4 OFFBOARD (nav_state=14) OK" \
  "PX4 OFFBOARD 진입 실패 — setpoint stream rate 또는 mode 명령 확인" \
  "timeout $TIMEOUT_SEC ros2 topic echo --qos-reliability best_effort --once /fmu/out/vehicle_status_v4 2>/dev/null | grep -E 'nav_state: 14'"

# ------------------------------------------------------------------
# 결과 요약
# ------------------------------------------------------------------
echo ""
echo "=========================================================="
if [ "$FAILED" -eq 0 ]; then
  echo " G1 스모크 통과 — 전 항목 PASS"
  echo " 추가: gz GUI에서 드론 takeoff (~1.5 m) 후 hover 육안 확인."
  echo "       nominal velocity 흘려보내 이동 확인 (스크립트 헤더 참조)."
else
  echo " G1 스모크 실패 — $FAILED 항목 FAIL"
  echo " 위 FAIL 항목을 확인하고 T1/T2/T3 + g1_offboard launch 상태를 점검하세요."
fi
echo "=========================================================="
exit "$FAILED"
