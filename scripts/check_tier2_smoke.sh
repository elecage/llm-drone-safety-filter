#!/usr/bin/env bash
# A4-3 tier2_gate sim smoke — 4 시나리오 자동 검증.
#
# Docker 컨테이너 안에서 ros2 sourced 후 실행:
#
#   $ docker exec -it llmdrone-sim bash -lc \
#       'cd /workspace && ./scripts/check_tier2_smoke.sh'
#
# Step:
#   1. colcon build --packages-select tier2_gate
#   2. gate_node 띄움 (백그라운드)
#   3. /tier2/gate/decision echo 시작 (백그라운드, 로그 file 에 dump)
#   4. mock_tier2_intent.py 4 시나리오 순차 발송
#   5. validate_tier2_smoke.py 로 expected sequence 검증
#
# Expected: 5 decisions = accept, reject(CC-1), reject(Φ_4), accept, confirm(Φ_10).
# 통과 시 exit 0, 실패 시 exit 1.

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
ROS_DISTRO="${ROS_DISTRO:-humble}"
DEC_LOG=/tmp/tier2_smoke_dec.log
BUILD_LOG=/tmp/tier2_smoke_build.log
GATE_LOG=/tmp/tier2_smoke_gate.log

cleanup() {
    [ -n "${GATE_PID:-}" ] && kill "${GATE_PID}" 2>/dev/null || true
    [ -n "${ECHO_PID:-}" ] && kill "${ECHO_PID}" 2>/dev/null || true
}
trap cleanup EXIT

cd "${WORKSPACE}"

# ROS 2 setup.bash 는 AMENT_TRACE_SETUP_FILES 를 디폴트 없이 참조 → `set -u` 와 충돌.
# 표준 회피: source 주변에서 -u 일시 해제. check_ovd_smoke.sh (PR #62) 와 동일 패턴.
ros_source() {
    set +u
    # shellcheck disable=SC1090,SC1091
    source "$1"
    set -u
}

# Step 1 — build.
echo "[smoke 1/5] colcon build --packages-select tier2_gate"
ros_source "/opt/ros/${ROS_DISTRO}/setup.bash"
colcon build --packages-select tier2_gate >"${BUILD_LOG}" 2>&1 || {
    echo "FAIL: colcon build" >&2
    tail -30 "${BUILD_LOG}" >&2
    exit 1
}
ros_source install/setup.bash

# Step 2 — gate_node 백그라운드.
echo "[smoke 2/5] gate_node 띄움 (백그라운드, 3s warmup)"
ros2 run tier2_gate gate_node >"${GATE_LOG}" 2>&1 &
GATE_PID=$!
sleep 3

# Step 3 — decision echo.
echo "[smoke 3/5] /tier2/gate/decision echo (dump to ${DEC_LOG})"
: >"${DEC_LOG}"
ros2 topic echo /tier2/gate/decision std_msgs/msg/String >"${DEC_LOG}" 2>&1 &
ECHO_PID=$!
sleep 1

# Step 4 — 4 시나리오 발송.
echo "[smoke 4/5] 4 시나리오 mock publish"
for scenario in accept reject_cc1 reject_phi4 confirm_phi10; do
    echo "  -> ${scenario}"
    python3 scripts/mock_tier2_intent.py --scenario "${scenario}" --delay 0.3 \
        >/tmp/tier2_smoke_mock_${scenario}.log 2>&1 || {
        echo "FAIL: mock_tier2_intent ${scenario}" >&2
        tail -10 /tmp/tier2_smoke_mock_${scenario}.log >&2
        exit 1
    }
    sleep 1
done
sleep 2  # decision 도착 대기.

# Step 5 — validate.
echo "[smoke 5/5] validate_tier2_smoke (expected: 5 decisions)"
python3 scripts/validate_tier2_smoke.py "${DEC_LOG}" || {
    echo "FAIL: validate" >&2
    echo "--- decision log ---" >&2
    cat "${DEC_LOG}" >&2
    exit 1
}

echo "PASS: tier2_gate smoke 5/5"
