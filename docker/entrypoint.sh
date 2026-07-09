#!/usr/bin/env bash
# entrypoint.sh — Docker ENTRYPOINT 스크립트.
#
# 목적: ROS 2 Humble / ros_gz / 프로젝트 venv / PX4 경로를 셸 형태와 무관하게
# (대화·비대화·login·non-login 전부) 미리 source/export한 뒤 사용자 명령을 exec.
#
# 배경: `~/.bashrc`에 sourcing을 넣어두면 `docker run -it ... bash` (대화형)에는
# 통하지만 `docker run ... bash -c "..."` (비대화) 혹은 `./docker/run.sh "cmd"`에선
# .bashrc가 실행되지 않아 환경이 비어 있다. ENTRYPOINT는 항상 실행되므로 여기서
# source하면 exec 이후 모든 자식 프로세스가 환경을 상속받는다.
#
# 사용:
#   docker run --rm image                          → /bin/bash (CMD 기본값)
#   docker run --rm image bash -c "ros2 ..."       → entrypoint가 sourcing 후 bash 실행
#   docker run -it image                           → 대화형 bash. PS1 등은 그대로.

# set -e는 사용하지 않음 — setup.bash 내부의 무해한 non-zero return이
# 컨테이너 시작 자체를 죽이지 않도록.

source /opt/ros/humble/setup.bash
source /opt/ros_gz_ws/install/setup.bash
source /opt/llmdrone_venv/bin/activate
export PX4_DIR=/opt/PX4-Autopilot

# 인자 없이 들어오면 기본 셸을 띄움 (Dockerfile CMD가 적용됨).
exec "$@"
