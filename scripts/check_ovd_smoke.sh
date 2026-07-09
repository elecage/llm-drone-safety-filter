#!/usr/bin/env bash
# B1 intent_ovd Docker smoke — light smoke (colcon build + import + launch parse).
#
# 정합 (CLAUDE.md §A4 + ADR-0008): 실 OVD 추론은 macOS host venv 에서 (MPS).
# Docker 트랙은 토픽 wiring 검증용 — 본 smoke 는 colcon 빌드 + module import +
# launch file parse 만 확인. 실 image → detection e2e 는 paper §C 실험 트랙
# 또는 별 PR (heavy smoke).
#
# Docker 컨테이너 안에서 ros2 sourced 후 실행:
#
#   $ ./docker/run.sh "cd /workspace && ./scripts/check_ovd_smoke.sh"
#
# Step:
#   1. colcon build --packages-select intent_ovd
#   2. install/setup.bash source
#   3. python -c "from intent_ovd.detector_node import OVDDetectorNode" — import 검증
#   4. ros2 launch --print-only — launch file parse 검증
#
# Expected: 4 step 모두 PASS. Exit 0 / 실패 시 exit 1.

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
ROS_DISTRO="${ROS_DISTRO:-humble}"
BUILD_LOG=/tmp/ovd_smoke_build.log
LAUNCH_LOG=/tmp/ovd_smoke_launch.log

cd "${WORKSPACE}"

# ROS 2 setup.bash 는 AMENT_TRACE_SETUP_FILES 를 디폴트 없이 참조 → `set -u` 와 충돌.
# 표준 회피: source 주변에서 -u 일시 해제.
ros_source() {
    set +u
    # shellcheck disable=SC1090,SC1091
    source "$1"
    set -u
}

# Step 1 — colcon build.
echo "[ovd-smoke 1/4] colcon build --packages-select intent_ovd"
ros_source "/opt/ros/${ROS_DISTRO}/setup.bash"
colcon build --packages-select intent_ovd >"${BUILD_LOG}" 2>&1 || {
    echo "FAIL: colcon build" >&2
    tail -40 "${BUILD_LOG}" >&2
    exit 1
}

# Step 2 — source workspace.
echo "[ovd-smoke 2/4] source install/setup.bash"
ros_source install/setup.bash

# Step 3 — module import.
# detector_node 는 cv_bridge / sensor_msgs / vision_msgs / rclpy import 필요.
# torch / ultralytics 는 OVDDetector 의 lazy import 라 import 자체엔 불필요.
echo "[ovd-smoke 3/4] python -c 'from intent_ovd.detector_node import OVDDetectorNode'"
python3 -c "
from intent_ovd.detector_node import OVDDetectorNode, _to_detection2d, _to_detection2d_array
from intent_ovd.detector import Detection, DetectionResult, OVDDetector
from intent_ovd.vocabulary import Vocabulary
print('import OK:',
      'OVDDetectorNode',
      'OVDDetector',
      'Vocabulary',
      'Detection',
      'DetectionResult')
" || {
    echo "FAIL: module import" >&2
    exit 1
}

# Step 4 — launch file parse (ros2 launch --print-only).
# 실 노드 띄우지 않고 LaunchDescription 만 구성 → syntax 검증.
echo "[ovd-smoke 4/4] ros2 launch --print intent_ovd ovd_detector.launch.py (parse only)"
ros2 launch --print intent_ovd ovd_detector.launch.py >"${LAUNCH_LOG}" 2>&1 || {
    echo "FAIL: launch parse" >&2
    tail -20 "${LAUNCH_LOG}" >&2
    exit 1
}
# Minimal sanity: launch description must include the detector_node executable.
grep -qF "detector_node" "${LAUNCH_LOG}" || {
    echo "FAIL: launch description 에 detector_node executable 미포함" >&2
    cat "${LAUNCH_LOG}" >&2
    exit 1
}

echo "PASS: intent_ovd smoke 4/4 (colcon build + import + launch parse)"
