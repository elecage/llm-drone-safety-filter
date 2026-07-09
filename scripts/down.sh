#!/usr/bin/env bash
# down.sh — 한 줄로 G1 트랙 full session 정리.
#
# 종료 순서 (역의존성):
#   1. 컨테이너 안 ROS 2 노드 종료 (g1_offboard, sim_user_marker)
#   2. 컨테이너 안 MicroXRCEAgent 종료
#   3. 컨테이너 정지 + 제거
#   4. macOS host의 PX4 SITL 종료
#   5. macOS host의 gz sim 종료
#   6. 잔여 프로세스·컨테이너 확인 출력
#   7. (옵션) Terminal.app 창 닫기 — KILL_TERMINAL=1
#
# 옵션:
#   KEEP_CONTAINER=1   컨테이너는 stop만 (rm 안 함). 빠른 재가동용.
#   KILL_TERMINAL=1    up.sh가 띄운 Terminal.app 창 자동 닫기 (활성 창들 중
#                      PX4/gz 명령이 보이는 창). osascript heuristic이라 100%
#                      신뢰는 아님 — 권장 안 함 (수동 닫기가 깔끔).

set -uo pipefail   # -e 없음 — 일부 단계 실패해도 다음 단계 진행.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
FAIL_COUNT=0

log() { echo "[down.sh] $*"; }
warn() { echo "[down.sh] WARN: $*" >&2; FAIL_COUNT=$((FAIL_COUNT + 1)); }

# PX4 stdin 블로킹 holder 정리용 (lib_px4_stdin.sh, 세션 53).
source "$REPO_ROOT/scripts/lib_px4_stdin.sh"

# ------------------------------------------------------------------
# 1. 컨테이너 안 ROS 2 노드
# ------------------------------------------------------------------
if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  log "1/6 컨테이너 안 ROS 2 노드 종료 ..."
  docker exec "$CONTAINER" pkill -TERM -f g1_offboard 2>/dev/null || true
  docker exec "$CONTAINER" pkill -TERM -f sim_user_marker 2>/dev/null || true
  docker exec "$CONTAINER" pkill -TERM -f user_marker_node 2>/dev/null || true
  sleep 1
  # 살아 있으면 강제 종료. pkill -f 가 컨테이너 안에서 안 먹히는 사례 실측
  # (세션 29 — 수동 publisher 잔존 시 stale setpoint 로 드론 폭주) →
  # ps|awk|kill -9 로 한 번 더 보강. KEEP_CONTAINER=1 (step 3 의 rm 생략)
  # 경로에서 특히 중요. 'ros2 topic pub' 수동 publisher 도 함께 정리.
  docker exec "$CONTAINER" pkill -9 -f g1_offboard 2>/dev/null || true
  docker exec "$CONTAINER" pkill -9 -f user_marker_node 2>/dev/null || true
  docker exec "$CONTAINER" bash -c \
    "ps aux | grep -E 'g1_offboard|sim_user_marker|user_marker_node|ros2 topic pub' \
     | grep -v grep | awk '{print \$2}' | xargs -r kill -9" 2>/dev/null || true
else
  log "1/6 컨테이너 미실행 — 건너뜀."
fi

# ------------------------------------------------------------------
# 2. MicroXRCEAgent
# ------------------------------------------------------------------
if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  log "2/6 MicroXRCEAgent 종료 ..."
  docker exec "$CONTAINER" pkill -TERM -f MicroXRCEAgent 2>/dev/null || true
  sleep 1
  docker exec "$CONTAINER" pkill -9 -f MicroXRCEAgent 2>/dev/null || true
  docker exec "$CONTAINER" bash -c \
    "ps aux | grep MicroXRCEAgent | grep -v grep | awk '{print \$2}' \
     | xargs -r kill -9" 2>/dev/null || true
fi

# ------------------------------------------------------------------
# 3. 컨테이너 정지 + 제거
# ------------------------------------------------------------------
if [ "${KEEP_CONTAINER:-0}" = "1" ]; then
  log "3/6 컨테이너 stop (KEEP_CONTAINER=1 — rm 생략) ..."
  docker stop "$CONTAINER" >/dev/null 2>&1 || warn "컨테이너 stop 실패"
else
  log "3/6 컨테이너 stop + rm ..."
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------
# 4. 호스트 PX4 SITL (+ 카메라 중계 host 절반)
# ------------------------------------------------------------------
log "4/6 호스트 PX4 SITL 종료 ..."
pkill -TERM -f "gz_cam_relay_host.py" 2>/dev/null || true
# PX4 SITL binary 경로 — build/px4_sitl_default/bin/px4 (실측 2026-05-24).
pkill -TERM -f "px4_sitl_default/bin/px4" 2>/dev/null || true
pkill -TERM -f "PX4_SIM_MODEL" 2>/dev/null || true
sleep 1
pkill -9 -f "px4_sitl_default/bin/px4" 2>/dev/null || true
pkill -9 -f "PX4_SIM_MODEL" 2>/dev/null || true
# PX4 stdin 블로킹 FIFO holder 종료 + FIFO 제거 (세션 53).
px4_stdin_cleanup

# ------------------------------------------------------------------
# 5. 호스트 gz sim
# ------------------------------------------------------------------
log "5/6 호스트 gz sim 종료 ..."
pkill -TERM -f "gz sim" 2>/dev/null || true
pkill -TERM -f "ruby.*gz sim" 2>/dev/null || true
sleep 1
pkill -9 -f "gz sim" 2>/dev/null || true
pkill -9 -f "ruby.*gz sim" 2>/dev/null || true
pkill -9 -f "gz-sim-server" 2>/dev/null || true
pkill -9 -f "gz-sim-gui" 2>/dev/null || true

# ------------------------------------------------------------------
# 6. 잔여 확인
# ------------------------------------------------------------------
log "6/6 잔여 확인 ..."

LEFTOVER_PROC=$(ps aux | grep -E "px4|gz sim|gz-sim|MicroXRCEAgent" \
  | grep -v grep \
  | grep -v "Visual Studio" \
  | grep -v "Cursor" \
  | grep -v "/down.sh" || true)
if [ -n "$LEFTOVER_PROC" ]; then
  warn "잔여 프로세스:"
  echo "$LEFTOVER_PROC" | sed 's/^/    /' >&2
fi

LEFTOVER_CONT=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep "^${CONTAINER}\$" || true)
if [ -n "$LEFTOVER_CONT" ]; then
  warn "잔여 컨테이너: $LEFTOVER_CONT"
fi

# ------------------------------------------------------------------
# 7. (옵션) Terminal.app 창 닫기
# ------------------------------------------------------------------
if [ "${KILL_TERMINAL:-0}" = "1" ]; then
  log "7/+ Terminal.app 창 닫기 시도 (heuristic) ..."
  osascript <<'APPLESCRIPT' || true
tell application "Terminal"
    set windowList to every window
    repeat with w in windowList
        set winName to name of w as string
        if winName contains "px4" or winName contains "PX4" or winName contains "gz sim" or winName contains "run_native_sitl" then
            try
                close w saving no
            end try
        end if
    end repeat
end tell
APPLESCRIPT
fi

# ------------------------------------------------------------------
# 결과
# ------------------------------------------------------------------
echo ""
if [ "$FAIL_COUNT" -eq 0 ]; then
  log "✓ 정리 완료."
else
  log "정리 중 경고 ${FAIL_COUNT}건 — 잔여 확인 필요."
  exit 1
fi
