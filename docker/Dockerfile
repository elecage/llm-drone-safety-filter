# Dockerfile — paper-1 sim stack (ROS 2 Humble + Gazebo Harmonic + ros_gz + PX4 SITL)
#
# 대상 호스트: Mac mini M4 (Apple Silicon, ARM64), Docker Desktop.
# 빌드 명령: docker buildx build --platform linux/arm64 -t llmdrone-sim:latest -f docker/Dockerfile .
# 빌드 시간 추정: ~20–30분 (M4 10코어 활용 시).
# 이미지 크기 추정: ~5–7 GB.
#
# 컨테이너 안 구조:
#   /opt/ros/humble              — ROS 2 Humble (apt 설치)
#   /opt/ros_gz_ws/install       — ros_gz Humble↔Harmonic (source 빌드)
#   /opt/PX4-Autopilot           — PX4 main + SITL 빌드
#   /opt/llmdrone_venv           — 프로젝트 Python venv (--system-site-packages)
#   /workspace                   — 호스트 리포 마운트 지점 (`docker run -v` 사용)
#
# Base 이미지 선택: `ros:humble-ros-base` (docker.io/library/ros)는 linux/arm64/v8
# manifest를 가짐. `osrf/ros:humble-desktop-full`은 amd64-only로 확인됨 → 회피.
# rviz2 등 desktop 도구는 아래 RUN에서 apt로 추가 (packages.ros.org arm64 빌드 존재).

FROM ros:humble-ros-base

ARG DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ---------------------------------------------------------------------------
# 1. 베이스 빌드 도구 + ROS 2 dev 도구 (ros-humble-ros-base에 일부만 있음 — 보강).
#    rviz2는 headless 평가 위주라 1차엔 미포함; 필요해지면 ros-humble-rviz2 추가.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl wget gnupg lsb-release ca-certificates \
        software-properties-common \
        build-essential cmake git \
        python3-pip python3-venv python3-dev \
        python3-rosdep python3-vcstool python3-colcon-common-extensions \
        ros-humble-rmw-cyclonedds-cpp \
        ros-dev-tools \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. Gazebo Harmonic — OSRF apt 저장소 (arm64 jammy 빌드 제공됨)
# ---------------------------------------------------------------------------
RUN wget -qO /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
        https://packages.osrfoundation.org/gazebo.gpg \
    && echo "deb [arch=arm64 signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable jammy main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gz-harmonic \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 3. ros_gz (Humble ↔ Harmonic) + px4_msgs — source 빌드
#    OSRF apt에 arm64 binary가 안 들어 있는 시점 기준. 향후 binary 가용해지면
#    `apt-get install ros-humble-ros-gzharmonic`으로 단순화 가능.
#    px4_msgs는 PX4 main과 정합되는 main 브랜치. uXRCE-DDS Agent가 publish하는
#    /fmu/out/* 토픽을 ROS 2 측에서 deserialize하려면 px4_msgs가 반드시 필요
#    (2026-05-23 발견 — 누락 시 ros2 topic echo 0건, ADR-0010 진단).
# ---------------------------------------------------------------------------
RUN mkdir -p /opt/ros_gz_ws/src \
    && cd /opt/ros_gz_ws/src \
    && git clone -b humble https://github.com/gazebosim/ros_gz.git \
    && git clone --depth 1 https://github.com/PX4/px4_msgs.git \
    && cd /opt/ros_gz_ws \
    && rosdep update --include-eol-distros \
    && apt-get update \
    && rosdep install --from-paths src --ignore-src -r -y \
       --skip-keys "gz_cmake_vendor gz_common_vendor gz_dome_vendor gz_fortress_vendor gz_garden_vendor gz_harmonic_vendor gz_math_vendor gz_msgs_vendor gz_plugin_vendor gz_sensors_vendor gz_sim_vendor gz_tools_vendor gz_transport_vendor gz_utils_vendor" \
    && rm -rf /var/lib/apt/lists/* \
    && source /opt/ros/humble/setup.bash \
    && export GZ_VERSION=harmonic \
    # Docker Desktop default 메모리(7.75 GiB)에서 ros_gz_bridge 빌드 OOM 발생.
    # 병렬도 제한: 패키지 1개씩 + 내부 make -j2 → peak 메모리 절감.
    # Docker Desktop 메모리 ≥12 GiB면 -j4로 올려도 OK.
    #
    # --symlink-install은 의도적으로 사용하지 않는다. 그 옵션을 켜면 install/이
    # build/로의 심볼릭 링크로 채워지는데, 아래 `rm -rf build log src`가 build/를
    # 지우는 순간 install/이 dangling symlinks 덩어리가 된다 (2026-05-21 사고).
    # 표준 copy install로 install/이 자기충족적이게 만들고, 빌드 산출물·소스는
    # 이미지 크기 절감을 위해 모두 제거.
    && MAKEFLAGS="-j2" colcon build --packages-up-to ros_gz px4_msgs \
        --parallel-workers 1 \
        --event-handlers console_direct+ \
        --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && rm -rf build log src

# ---------------------------------------------------------------------------
# 4. PX4 Autopilot + SITL 빌드
# ---------------------------------------------------------------------------
RUN git clone --recursive --depth 1 https://github.com/PX4/PX4-Autopilot.git /opt/PX4-Autopilot \
    && cd /opt/PX4-Autopilot \
    && bash ./Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools \
    # NUM_JOBS=2 → PX4 내부 cmake/Ninja에 전파. 메모리 제한 환경에서 OOM 방지.
    && NUM_JOBS=2 make px4_sitl_default \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 4.5 MicroXRCEAgent — uXRCE-DDS 에이전트 (ADR-0010)
#     PX4 SITL(호스트, UDP 8888)과 컨테이너 ROS 2를 연결하는 DDS 브릿지.
#     Ubuntu 22.04 arm64에서는 macOS 전용 패치(libc++/sysroot/nonnull) 불필요.
#     -DUAGENT_SOCKETCAN_PROFILE=OFF: Docker 컨테이너는 SocketCAN 미지원.
#
#     SuperBuild는 Fast-CDR·Fast-DDS·Micro-CDR를 빌드 트리 안에 놓고
#     최종 바이너리가 RPATH로 이들을 참조한다. cmake --install 후 빌드 트리를
#     지우면 RPATH가 끊기므로 소스·.git만 제거하고 build/ 트리는 보존한다.
#     결과물: /opt/MicroXRCEAgent/build/MicroXRCEAgent
#
#     대안 (arm64 바이너리 패키지 가용 시 더 빠름):
#       apt-get install ros-humble-micro-ros-agent
#     → packages.ros.org arm64 jammy에서 확인 필요.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        libasio-dev libtinyxml2-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 \
        https://github.com/eProsima/Micro-XRCE-DDS-Agent.git \
        /opt/MicroXRCEAgent \
    && cmake -S /opt/MicroXRCEAgent -B /opt/MicroXRCEAgent/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DUAGENT_SOCKETCAN_PROFILE=OFF \
    # --parallel 2: Docker Desktop 기본 메모리 한계 하에서 OOM 방지.
    && cmake --build /opt/MicroXRCEAgent/build --parallel 2 \
    # 소스 + .git만 제거. build/ 트리는 RPATH 의존으로 보존.
    && find /opt/MicroXRCEAgent -maxdepth 1 -mindepth 1 \
        ! -name build -exec rm -rf {} + \
    && find /opt/MicroXRCEAgent/build -name ".git" -type d -exec rm -rf {} + \
        2>/dev/null || true

ENV MICROXRCE_AGENT_BIN=/opt/MicroXRCEAgent/build/MicroXRCEAgent

# ---------------------------------------------------------------------------
# 5. 프로젝트 Python venv
#    CLAUDE.md §A4 정책: 모든 Python은 venv 안에서. --system-site-packages로
#    ROS 2 시스템 Python(rclpy 등)을 venv에서도 보이게 함.
# ---------------------------------------------------------------------------
RUN python3 -m venv --system-site-packages /opt/llmdrone_venv \
    && /opt/llmdrone_venv/bin/pip install --upgrade pip \
    && /opt/llmdrone_venv/bin/pip install pytest \
    # cv_bridge (ros-humble-cv-bridge) 는 NumPy 1.x ABI 로 컴파일됨. /root/.local/
    # 또는 user-site 에 NumPy 2 가 transitive 로 깔리면 "AttributeError: _ARRAY_API
    # not found" 노이즈 발생 (import 자체는 통과하나 일부 op crash 위험). venv 에
    # NumPy 1.x 를 install 해서 site-package resolution 우선순위로 가림.
    && /opt/llmdrone_venv/bin/pip install 'numpy<2'

# ---------------------------------------------------------------------------
# 5.5 GUI 의존성 — Mac mini XQuartz forwarding으로 Gazebo·RViz를 띄울 때 필요.
#     Docker-Mac에는 GPU passthrough가 없어 mesa 소프트웨어 렌더링(swrast)에
#     의존. ros-humble-ros-base는 rviz2를 포함하지 않으므로 여기서 추가.
#     늦은 layer로 둬서 GUI 의존성 변경 시 ros_gz·PX4 재빌드를 피함.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1-mesa-dri libglx-mesa0 libegl-mesa0 \
        libgl1 libglx0 libxcb-glx0 \
        libxkbcommon-x11-0 libxcb-cursor0 \
        mesa-utils \
        ros-humble-rviz2 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 5.5 intent_ovd 패키지 (B1) — cv_bridge / vision_msgs + 추론 런타임.
#     실 OVD 추론 = *Docker CPU* ([ADR-0024 amendment 2026-05-31] — host macOS
#     측 ROS 2 native 부재로 host MPS 단념, yolov8s-worldv2 25 MB small 모델은
#     CPU arm64 + throttle_hz=5 로 충분). torch 는 PyPI aarch64 manylinux wheel
#     = CPU 빌드. numpy<2 동시 고정 — cv_bridge 가 NumPy 1.x ABI (§5 주석).
#     시스템 pip 에 설치: start_intent_stack.sh 의 노드 실행 경로는
#     /ros_entrypoint.sh (시스템 python3) 이고, /opt/llmdrone_venv 는
#     --system-site-packages 라 양쪽에서 보인다.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-humble-cv-bridge \
        ros-humble-vision-msgs \
    && rm -rf /var/lib/apt/lists/* \
    # torch 는 CPU 인덱스로 먼저 고정 — PyPI aarch64 기본 wheel 이 CUDA 변형
    # (nvidia-cudnn ~GB 동반)으로 풀리는 것 차단 (빌드 중 실측 2026-06-11).
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        'torch>=2.6,<3.0' 'torchvision>=0.21,<0.22' \
    && pip install --no-cache-dir 'numpy<2' 'ultralytics>=8.3,<9.0' \
    # YOLO-World set_classes 는 clip 패키지 필요 (ultralytics 자동 설치는
    # metadata UNKNOWN 으로 깨짐 — 실측 2026-06-11). OpenAI 원본으로 고정.
    && pip install --no-cache-dir 'git+https://github.com/openai/CLIP.git' \
    # CLIP ViT-B/32 weight (338 MB) 를 이미지에 bake — 컨테이너 재생성마다
    # /root/.cache/clip 재다운로드 회피 (up.sh 가 매번 컨테이너를 새로 만듦).
    && python3 -c "import clip; clip.load('ViT-B/32', device='cpu')"

# ---------------------------------------------------------------------------
# 5.6 LLM backbone 런타임 (intent_llm) — cloud backbone(gpt-4o 등)의 `openai`
#     SDK. intent/llm/intent_llm/cloud_llm.py 가 `import openai`(v1+ client API,
#     `OpenAI()`). OVD(5.5)와 동일하게 *시스템 pip* — wrapper_node 도
#     start_intent_stack / per-trial launch 의 `ros2 run`(시스템 python3) 경로로
#     실행되고 /opt/llmdrone_venv 는 --system-site-packages 라 양쪽에서 보인다.
#     본실험 격자(ADR-0030 F9)에서 gpt-4o backbone wrapper 가 컨테이너 기동마다
#     필요 — 런타임 pip install 제거(이미지 bake). 키는 docker exec -e
#     OPENAI_API_KEY 로 주입(run_grid.py 가 .env 에서 전파).
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir 'openai>=1.0,<3.0'

# ---------------------------------------------------------------------------
# 6. Entrypoint — 대화·비대화 모두에서 ROS 2 + ros_gz + venv가 source된 상태로
#    명령이 실행되도록 ENTRYPOINT 스크립트로 처리. .bashrc 사본은 두지 않는다
#    (.bashrc는 대화형에만 적용돼 `docker run image bash -c "..."`에서 비어 있음.)
# ---------------------------------------------------------------------------
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# 기본 동작: 인터랙티브 셸. `docker run -it ...`로 진입.
# 비대화 실행은 `docker run llmdrone-sim:latest bash -c "..."` 형태로.
CMD ["/bin/bash"]
