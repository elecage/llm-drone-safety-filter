#!/usr/bin/env bash
# teleop.sh — 컨테이너 안 teleop_keyboard_node 빠른 실행 래퍼.
#
# up.sh 실행 후 별 Terminal에서 호출:
#   ./scripts/teleop.sh
#
# 키 매핑 (ENU velocity):
#   W/S  = +X/-X (forward/backward)   R/F  = +Z/-Z (up/down)
#   A/D  = +Y/-Y (left/right)         Q/E  = yaw left/right
#   space = stop
#
# 주의: docker exec -it 필수 (termios raw stdin). SSH 헤드리스 세션에선 동작 안 함.
#       반드시 Mac mini 콘솔 Terminal.app 에서 실행할 것.
#
# 옵션 환경변수:
#   CONTAINER_NAME=llmdrone-sim (default)

set -euo pipefail

CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"

if ! /usr/local/bin/docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "ERROR: 컨테이너 '${CONTAINER}' 미실행 — up.sh 먼저 실행" >&2
  exit 1
fi

exec /usr/local/bin/docker exec -it "$CONTAINER" \
  /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && \
   ros2 run teleop_keyboard teleop_keyboard_node"
