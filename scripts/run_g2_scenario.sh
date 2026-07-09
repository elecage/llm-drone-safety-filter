#!/usr/bin/env bash
# run_g2_scenario.sh — G2 시나리오 한 줄 실행 wrapper.
#
# 사용:
#   ./scripts/run_g2_scenario.sh c0
#   ./scripts/run_g2_scenario.sh c0 --diagnose    # G2 로그 + topic echo 동시 캡처
#
# 시나리오 후보: c0, c1, c2 (또는 full name)
#
# --diagnose 옵션: 한 셸에서 G2 실행 + topic echo(/cmd/.._safe) + PX4 NED
# trajectory_setpoint를 모두 캡처. 시퀀스 끝나면 /tmp/g2_diagnose.log에 합쳐
# 저장. 동시 셸 띄울 필요 없음.
#
# 전제: up.sh가 가동 완료 + G1 ACTIVE 상태 + 컨테이너 살아 있음.

set -euo pipefail

CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"

if [ $# -lt 1 ]; then
  echo "사용: $0 <scenario> [--diagnose]" >&2
  echo "  scenario 후보: c0, c1, c2 (또는 full name)" >&2
  exit 1
fi

SCENARIO="$1"
DIAGNOSE=0
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --diagnose) DIAGNOSE=1 ;;
    *) echo "WARN: 알 수 없는 옵션 '$1' 무시" >&2 ;;
  esac
  shift
done

# 짧은 이름 → full name 매핑.
case "$SCENARIO" in
  c0) SCENARIO=c0_up_down_sweep ;;
  c1) SCENARIO=c1_square_pattern ;;
  c2) SCENARIO=c2_s6_adversarial ;;
esac

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
  echo "ERROR: 컨테이너 '$CONTAINER' 미실행. 먼저 ./scripts/up.sh 실행." >&2
  exit 1
fi

echo "[run_g2] 시나리오: $SCENARIO"

if [ "$DIAGNOSE" -eq 0 ]; then
  # 단순 실행 모드.
  docker exec -it "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 launch g2_waypoint_player g2_play.launch.py scenario:=$SCENARIO"
  exit $?
fi

# --diagnose 모드: G2 + vehicle_local_position(실제 위치) + ENU/NED 명령
# 세 stream을 /tmp/g2_diagnose.log에 저장.
LOG=/tmp/g2_diagnose.log
POS_LOG=/tmp/g2_pos.log

# c0=11s, c1=16s, c2=21s — 넉넉히 잡음.
case "$SCENARIO" in
  c0*) TIMEOUT=16 ;;
  c1*) TIMEOUT=22 ;;
  c2*) TIMEOUT=32 ;;
  *)   TIMEOUT=35 ;;
esac

echo "[run_g2] --diagnose 모드 — 로그: $LOG  위치: $POS_LOG  (최대 ${TIMEOUT}s)"

docker exec "$CONTAINER" bash -c "rm -f $LOG $POS_LOG && touch $LOG $POS_LOG"

# G2 launch (background).
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && \
   { echo '=== G2 NODE ===' ; \
     ros2 launch g2_waypoint_player g2_play.launch.py scenario:=$SCENARIO ; } \
   >> $LOG 2>&1"

# 실제 드론 위치 — ENU 변환해 저장 (NED→ENU: x_enu=y_ned, y_enu=x_ned, z_enu=-z_ned).
# vehicle_local_position_v1: x/y/z 필드가 NED 좌표.
# install/setup.bash 필수 — px4_msgs 타입 인식.
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && \
   timeout ${TIMEOUT} ros2 topic echo \
     --qos-reliability best_effort \
     /fmu/out/vehicle_local_position_v1 \
     px4_msgs/msg/VehicleLocalPosition \
   >> $POS_LOG 2>&1"

# ENU /cmd/trajectory_setpoint_safe
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "{ echo '=== ENU cmd ===' ; \
     timeout ${TIMEOUT} ros2 topic echo /cmd/trajectory_setpoint_safe ; } \
   >> $LOG 2>&1"

echo "[run_g2] 캡처 중 (${TIMEOUT}s) ..."
sleep "$TIMEOUT"

echo ""
echo "[run_g2] === 진단 요약 ==="
echo ""

# G2 step 로그.
echo "----- G2 step 진행 -----"
docker exec "$CONTAINER" grep -E 'step [0-9]/|시나리오.*완료' "$LOG" 2>/dev/null | head -20
echo ""

# 시나리오별 USER_POS 전달 — C2(적대적)만 user와의 거리 컬럼 + r_min 침입 판정 출력.
# 좌표는 G1의 local ENU frame (PX4 EKF origin 기준) = user_marker world (-2.6, 1.5, 1.1)
# - spawn world (0.5, -0.5, ~0.15) = local (-3.1, 2.0, 0.95).
# (2026-05-24 frame 통일 — c2 YAML와 동일 frame.)
USER_POS=""
case "$SCENARIO" in
  c2*) USER_POS="-3.1,2.0,0.95" ;;  # 거실 layout v3, local frame
esac

# 실제 드론 위치 — 2초마다 ENU 샘플 출력 + peak 변위 표.
# 파서를 호스트에서 docker cp로 복사 후 실행 (이전엔 `docker exec ... python3 - <<EOF`
# 인라인 heredoc 사용했으나 컨테이너 STDIN 전달 실패로 무음 종료 — docker cp 경유).
echo "----- 드론 실제 위치 (ENU, 2s 간격 샘플) -----"
PARSER_HOST="$(cd "$(dirname "$0")" && pwd)/parse_g2_pos.py"
docker cp "$PARSER_HOST" "$CONTAINER:/tmp/parse_g2_pos.py" >/dev/null
docker exec -e "POS_LOG=$POS_LOG" -e "USER_POS=$USER_POS" "$CONTAINER" \
  python3 /tmp/parse_g2_pos.py
echo ""
echo "전체 위치 로그: docker exec $CONTAINER cat $POS_LOG | head -80"
echo "전체 명령 로그: docker exec $CONTAINER cat $LOG"
