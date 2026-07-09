#!/usr/bin/env bash
# restart_persistent_node.sh — 영속 노드 재빌드 + graceful 재기동.
#
# 코드 변경 후 영속 노드(sigma_bridge·follower·ovd)만 클린 재기동(down+up) 없이
# 갱신한다. ros2 launch 는 부모(launch)–자식(node) 프로세스 트리라, ps|awk|kill -9
# 로 자식만 죽이면 launch 가 재spawn 하거나 orphan/중복 launch 가 남는다(세션 48
# 실측 — 재기동마다 노드 2개). SIGINT 가 ros2 launch 의 정상 종료 경로로 자식
# node 까지 정리하므로, launch 프로세스에 SIGINT → 재launch 순으로 처리한다.
#
# 사용:
#   ./scripts/restart_persistent_node.sh sigma_bridge   # 코드 변경 후
#   SIGMA_SCENARIO_ID=S7 ./scripts/restart_persistent_node.sh sigma_bridge
#   OVD_THROTTLE_HZ=5.0 OVD_VOCAB="['couch','table','chair']" ./scripts/restart_persistent_node.sh ovd
#
# 전제: up.sh 로 영속 셸이 떠 있고 컨테이너($CONTAINER_NAME, 기본 llmdrone-sim)가
# 실행 중. host pull 후 본 스크립트가 컨테이너 안에서 colcon build → 재launch.

set -uo pipefail

NODE="${1:?usage: $0 <sigma_bridge|follower|ovd>}"
CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"

case "$NODE" in
  sigma_bridge)
    PKG=intent_sigma_bridge
    LAUNCH="intent_sigma_bridge sigma_bridge.launch.py scenario_id:=${SIGMA_SCENARIO_ID:-S5}"
    PAT='sigma_bridge\.launch\.py'
    PRE=''
    ;;
  follower)
    PKG=waypoint_follower
    LAUNCH="waypoint_follower follower.launch.py"
    PAT='follower\.launch\.py'
    PRE=''
    ;;
  ovd)
    PKG=intent_ovd
    LAUNCH="intent_ovd ovd_detector.launch.py device:=cpu throttle_hz:=${OVD_THROTTLE_HZ:-5.0} vocabulary:=\"${OVD_VOCAB:-['couch','table','chair']}\""
    PAT='ovd_detector\.launch\.py'
    PRE='mkdir -p /workspace/models/ovd && cd /workspace/models/ovd &&'
    ;;
  *)
    echo "unknown node: $NODE (sigma_bridge|follower|ovd)" >&2
    exit 1
    ;;
esac

if ! docker exec "$CONTAINER" true 2>/dev/null; then
  echo "ERROR: 컨테이너 '$CONTAINER' 미실행 — up.sh 로 영속 셸 먼저 기동." >&2
  exit 1
fi

echo "[restart] $NODE: colcon build ($PKG) ..."
docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && colcon build --packages-select $PKG 2>&1 | tail -2"

# graceful 종료 — launch 프로세스에 SIGINT (자식 node 까지 정상 정리).
echo "[restart] $NODE: graceful 종료 (SIGINT to ros2 launch) ..."
docker exec "$CONTAINER" bash -c \
  "for p in \$(pgrep -f '$PAT' 2>/dev/null); do kill -INT \$p 2>/dev/null; done; true"
sleep 4

# graceful 실패 잔존 강제 정리 — node 실행 *경로*('install/$PKG/lib')로 매칭해
# 본 스크립트의 grep/bash 명령줄 자기 매칭을 피한다(launch.py 패턴은 명령줄에도
# 들어가 자기 자신을 잡던 문제).
docker exec "$CONTAINER" bash -c \
  "for p in \$(pgrep -f 'install/$PKG/lib' 2>/dev/null); do kill -9 \$p 2>/dev/null; done; true"
sleep 1

echo "[restart] $NODE: 재launch ..."
docker exec -d "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash && $PRE ros2 launch $LAUNCH > /tmp/${NODE}_restart.log 2>&1"

# 노드 폴링 — ros2 launch + node init 은 수 초 걸리므로 고정 sleep 은 과소 측정한다
# (세션 48: sleep 3 에서 노드 0 오보고, 실제론 정상 기동). 최대 ~12 s 폴링.
N=0
for _ in 1 2 3 4 5 6; do
  sleep 2
  N=$(docker exec "$CONTAINER" bash -lc \
    "source /opt/ros/humble/setup.bash; source /workspace/install/setup.bash; ros2 node list 2>/dev/null | grep -c $NODE || true" 2>/dev/null)
  N="${N:-0}"
  [ "$N" = "1" ] && break
done
echo "[restart] $NODE 노드 수=$N (1 기대)"
if [ "$N" != "1" ]; then
  echo "    WARN: 노드 수 ≠ 1 — graceful 종료 실패 가능. 클린 재기동(down+up) 권장." >&2
  exit 1
fi
echo "[restart] $NODE 재기동 완료."
