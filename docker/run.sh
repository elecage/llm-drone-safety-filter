#!/usr/bin/env bash
# run.sh — Mac mini Docker 컨테이너 진입/실행 wrapper.
#
# 용법:
#   ./docker/run.sh                  # 인터랙티브 셸로 진입
#   ./docker/run.sh "ros2 launch ..."  # 명령 한 줄 실행 후 종료
#
# 호스트 경로 매핑:
#   /workspace   ← 리포 루트 (호스트 read-write 마운트)
#   /workspace/install_jetson.log 같은 산출물은 호스트에 그대로 남음

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-llmdrone-sim:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-llmdrone-sim}"

# Mac OS는 host network 모드 미지원 → bridge + 포트 매핑 또는 docker0 IP 사용.
# uXRCE-DDS (ADR-0010): 호스트 PX4 SITL이 localhost:8888로 에이전트(컨테이너)에
# 연결한다. Docker 포트 포워딩으로 투명하게 처리.
NETWORK_FLAGS=(-p 8888:8888/udp)

# GUI(Gazebo·RViz) X11 forwarding — `WITH_GUI=1 ./docker/run.sh ...`로 활성화.
# macOS는 native X11이 없으므로 XQuartz가 X 서버 역할. 컨테이너는
# host.docker.internal:0 (= Mac 호스트의 TCP :6000)로 그림을 보냄.
# 사전 1회 호스트 셋업:
#   defaults write org.xquartz.X11 nolisten_tcp -bool false
#   defaults write org.xquartz.X11 enable_iglx -bool true
#   open -a XQuartz
#   xhost +localhost
GUI_FLAGS=()
if [ "${WITH_GUI:-0}" = "1" ]; then
  GUI_FLAGS+=(-e "DISPLAY=host.docker.internal:0")
  # macOS XQuartz + Docker(Linux 컨테이너 안 mesa) 조합은 잘 알려진 함정:
  #   - LIBGL_ALWAYS_SOFTWARE=1 → 컨테이너 mesa가 swrast를 시도하는데 XQuartz는
  #     mesa-호환 fbConfigs를 export 안 함 → "No matching fbConfigs" + GLXBadContext.
  #   - LIBGL_ALWAYS_INDIRECT=1 → GLX 명령을 XQuartz에 위임 (XQuartz는 자체 GLX로
  #     macOS native GL을 호출). XQuartz의 +iglx 옵션이 켜진 상태여야 작동
  #     (`defaults write org.xquartz.X11 enable_iglx -bool true` + XQuartz 재시작).
  # 첫 시도는 INDIRECT.
  GUI_FLAGS+=(-e "LIBGL_ALWAYS_INDIRECT=1")
fi

# TTY가 있을 때만 -t. SSH 비대화 호출(예: ssh host './docker/run.sh "cmd"')에선
# stdin이 터미널이 아니므로 -t를 붙이면 docker가 "cannot attach stdin to a
# TTY-enabled container"로 거부한다. -i는 항상 켜둬도 무해.
TTY_FLAGS=(-i)
if [ -t 0 ] && [ -t 1 ]; then
  TTY_FLAGS+=(-t)
fi

DOCKER_RUN_ARGS=(
  --rm
  "${TTY_FLAGS[@]}"
  --name "$CONTAINER_NAME"
  --platform linux/arm64
  -v "$REPO_ROOT":/workspace
  -w /workspace
  # 빈 배열을 `set -u` 하에서 안전하게 펼치는 bash 3.2 호환 관용구.
  # NETWORK_FLAGS / GUI_FLAGS가 비어 있으면 아무 것도 펼치지 않음.
  ${NETWORK_FLAGS[@]+"${NETWORK_FLAGS[@]}"}
  ${GUI_FLAGS[@]+"${GUI_FLAGS[@]}"}
)

if [ $# -eq 0 ]; then
  exec docker run "${DOCKER_RUN_ARGS[@]}" "$IMAGE"
else
  # ENTRYPOINT(/usr/local/bin/entrypoint.sh)가 sourcing을 처리하므로 login shell
  # 플래그(-l)는 필요 없음. 그냥 bash -c로 명령 실행.
  exec docker run "${DOCKER_RUN_ARGS[@]}" "$IMAGE" bash -c "$*"
fi
