#!/usr/bin/env bash
# check_ovd_e2e_smoke.sh — P1 OVD 입력 사슬 heavy smoke (ADR-0024 Task #2 A).
#
# 검증 사슬: gz 카메라 센서(host) → gz_cam_relay(host→컨테이너 TCP)
#            → /camera/image_raw (ROS) → ovd_detector → /intent/ovd/detections.
#
# 전제: Mac mini host 에서 실행. 다음이 가동 중이어야 함:
#   DRONE_CAMERA=1 ./scripts/up.sh          (sim + 카메라 중계)
#   OVD=1 ./scripts/start_intent_stack.sh   (detector — 첫 추론은 weight
#                                            다운로드 포함 수 분 걸릴 수 있음)
#
# 사용: ./scripts/check_ovd_e2e_smoke.sh
#   CONTAINER_NAME=...   (기본 llmdrone-sim)
#   DETECT_TIMEOUT_S=... (기본 90 — 첫 추론 + weight 다운로드 여유)

set -uo pipefail

CONTAINER="${CONTAINER_NAME:-llmdrone-sim}"
# 첫 추론 = CLIP ViT-B/32 다운로드(338 MB, 캐시 후 생략) + 텍스트 인코딩 포함
# — 기본 여유 크게 (실측 2026-06-11).
DETECT_TIMEOUT_S="${DETECT_TIMEOUT_S:-180}"
# PX4 가 gz 서버 측에 GZ_IP=127.0.0.1 고정 — host 측 gz CLI 도 동일 필요.
export GZ_IP="${GZ_IP:-127.0.0.1}"
PASS=0
FAIL=0

ok()   { echo "  ✓ $*"; PASS=$((PASS + 1)); }
bad()  { echo "  ✗ $*" >&2; FAIL=$((FAIL + 1)); }

echo "[ovd_e2e_smoke] 1/4 host gz 카메라 토픽 존재 ..."
# macOS 에 coreutils timeout 이 없음 — perl alarm 으로 hang 가드.
# grep -q 금지: pipefail 하에서 -q 의 조기 종료가 gz 를 SIGPIPE(141)로 죽여
# 파이프라인 전체가 실패 판정됨 (실측 2026-06-11) — 입력 전부 소비 형태로.
if perl -e 'alarm 20; exec @ARGV' gz topic -l 2>/dev/null | grep "/drone/front_camera/image" >/dev/null; then
  ok "/drone/front_camera/image (gz)"
else
  bad "/drone/front_camera/image 부재 — 카메라 센서 SDF 또는 Sensors 렌더링(ogre2) 확인"
fi

echo "[ovd_e2e_smoke] 2/4 컨테이너 /camera/image_raw 수신 ..."
# relay 발행 QoS = SENSOR_DATA(best_effort) — echo 기본(reliable)과 비호환이라
# --qos-profile sensor_data 필수 (실측 2026-06-11).
# "A message was lost!!!" 류 경고 줄 배제 — 숫자 줄만 채택.
WIDTH=$(docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash >/dev/null 2>&1; \
   timeout 15 ros2 topic echo --once --qos-profile sensor_data /camera/image_raw --field width 2>/dev/null" \
  | awk '/^[0-9]+$/{print; exit}' || true)
if [ -n "$WIDTH" ] && [ "$WIDTH" != "0" ]; then
  ok "/camera/image_raw width=$WIDTH"
else
  bad "/camera/image_raw 미수신 — /tmp/gz_cam_relay_host.log (host) / /tmp/gz_cam_relay_node.log (컨테이너) 확인"
fi

echo "[ovd_e2e_smoke] 3/4 ovd_detector 노드 존재 ..."
if docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "ros2 node list 2>/dev/null" | grep -q "ovd_detector"; then
  ok "ovd_detector 노드"
else
  bad "ovd_detector 노드 부재 — OVD=1 ./scripts/start_intent_stack.sh 가동 확인"
fi

echo "[ovd_e2e_smoke] 4/4 /intent/ovd/detections 1건 수신 (최대 ${DETECT_TIMEOUT_S}s) ..."
DET=$(docker exec "$CONTAINER" /usr/local/bin/entrypoint.sh bash -c \
  "cd /workspace && source install/setup.bash >/dev/null 2>&1; \
   timeout $DETECT_TIMEOUT_S ros2 topic echo --once /intent/ovd/detections --field header.frame_id 2>/dev/null" | head -1 | tr -d '[:space:]')
if [ -n "$DET" ]; then
  ok "/intent/ovd/detections frame_id=$DET"
else
  bad "/intent/ovd/detections 미수신 — /tmp/llmdrone_intent/ovd_detector.log 확인"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "[ovd_e2e_smoke] ✓ PASS ($PASS/4) — OVD 입력 사슬 가동."
  exit 0
else
  echo "[ovd_e2e_smoke] ✗ FAIL ($FAIL 실패 / $PASS 통과)" >&2
  exit 1
fi
