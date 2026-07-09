# docker/

Mac mini M4 (Apple Silicon) 기반 sim host용 Docker 자산. Jetson Orin Nano sustained-compile hardware crash 이슈로 sim host를 Mac mini로 전환 — 자세한 동기는 [ADR-0007](../docs/handover/decisions/0007-sim-host-macmini.md).

## 무엇이 들어 있나

- `Dockerfile` — ROS 2 Humble + Gazebo Harmonic + ros_gz (Humble↔Harmonic, source 빌드) + **px4_msgs** (§3, uXRCE-DDS 데이터 deserialize 필수) + PX4 SITL + **MicroXRCEAgent** (§4.5, uXRCE-DDS 브릿지) + 프로젝트 venv. Base = `ros:humble-ros-base` (Docker Hub `library/ros`, ARM64 native). `osrf/ros:humble-desktop-full`은 amd64-only라 회피.
- `entrypoint.sh` — 컨테이너 ENTRYPOINT. 대화·비대화 모두에서 ROS / ros_gz / venv를 source한 뒤 사용자 명령을 exec.
- `run.sh` — 컨테이너 진입/명령 실행 wrapper.

## 필요 조건

- Apple Silicon Mac (M1/M2/M3/M4) — ARM64 native Docker 컨테이너 실행
- Docker Desktop 설치·동작 중
- 디스크 ≥ 10 GB (이미지 ~5–7 GB + 빌드 캐시)
- **메모리 ≥ 12 GB Docker Desktop 할당** (default 7.75 GiB는 ros_gz_bridge 빌드 시 OOM 발생). 변경: Docker Desktop → Settings → Resources → Memory. 호스트가 24 GB라면 16 GB까지 올려도 무방.

## 사전 1회 셋업

### (a) Docker Desktop credstore SSH 충돌 우회

기본 Docker Desktop은 macOS Keychain credstore를 강제 — 비대화 SSH 세션에서 `docker pull`이 막힘. 우회 두 가지:

- **권장**: base 이미지 1회 수동 pull (이후 build·run은 cached, keychain 무관):
  ```bash
  docker pull --platform linux/arm64 osrf/ros:humble-desktop-full
  ```
- 또는 `~/.docker/config.json`에서 `"credsStore": "desktop"` 줄 제거 (사설 레포 사용 시 매번 `docker login` 필요해짐)

## 빌드

리포 루트에서:

```bash
docker buildx build --platform linux/arm64 -t llmdrone-sim:latest -f docker/Dockerfile .
```

M4 10코어 활용 시 빌드 시간 약 **30–50 분** (ros_gz source 빌드 + PX4 SITL + MicroXRCEAgent 빌드 포함). 이미지 크기 ≈ 6–9 GB.

## 실행

```bash
./docker/run.sh                                # 인터랙티브 셸
./docker/run.sh "ros2 launch sim minimal.launch.py"   # 명령 1회 실행
```

호스트 리포가 컨테이너 `/workspace`에 마운트되므로 Mac에서 코드 편집, 컨테이너에서 실행이 자연.

## 컨테이너 내부 자동 환경

ENTRYPOINT (`/usr/local/bin/entrypoint.sh`)가 컨테이너 시작 시 다음을 처리한 뒤 사용자 명령을 exec:
- `source /opt/ros/humble/setup.bash`
- `source /opt/ros_gz_ws/install/setup.bash`
- `source /opt/llmdrone_venv/bin/activate`
- `export PX4_DIR=/opt/PX4-Autopilot`

WORKDIR이 `/workspace`이므로 호스트 리포가 그 경로에서 보임. 비대화 실행 (`docker run image bash -c "..."` 또는 `./docker/run.sh "..."`)에서도 동일 환경이 보장됨.

## 검증 명령 (컨테이너 안에서)

```bash
ros2 --help | head -3
gz sim --version
ros2 pkg list | grep ros_gz
ls $PX4_DIR/build/px4_sitl_default/bin/px4
which python3 && python3 -c "import rclpy; print('rclpy OK')"
test -x $MICROXRCE_AGENT_BIN && echo "MicroXRCEAgent OK"
```

## E2 — uXRCE-DDS 브릿지 + 사용자 마커 (Sim 트랙 E2)

**전제**: 호스트에서 PX4 SITL이 먼저 실행 중이어야 한다.

```bash
# T1 (macOS 호스트) — PX4 SITL + Gazebo 시작
./scripts/run_native_sitl_livingroom.sh    # HEADLESS=1 서버
# 별도 터미널: gz sim -g (GUI)

# T2 (MacBook 터미널) — 컨테이너 안에서 E2 launch
./docker/run.sh "colcon build --symlink-install && \
    source install/setup.bash && \
    ros2 launch sim_user_marker e2_sim_bridge.launch.py"
```

> `source install/setup.bash` 필수 — entrypoint는 `/opt/ros/humble`·
> `/opt/ros_gz_ws/install/`만 source하므로 `/workspace/install/`의 새 빌드
> 결과는 명시적으로 source해야 `ros2 launch`가 패키지를 찾는다.

**E2 검증** — 컨테이너 별도 셸에서:

```bash
./docker/run.sh "ros2 topic list | grep fmu"
# 기대 출력 예:
#   /fmu/out/vehicle_local_position
#   /fmu/out/vehicle_odometry
#   /fmu/out/vehicle_status
#   ...
```

PX4가 실행 중이고 에이전트 연결이 성립하면 `/fmu/out/*` 토픽이 보인다 → E2 통과.

## GUI (Gazebo·RViz)

1차 평가는 headless라 GUI 불필요. 시각 검사 필요 시:
- macOS XQuartz 설치 + `xhost +` 후 `docker run --env DISPLAY=host.docker.internal:0 ...`
- 또는 컨테이너 안에서 화면 캡처해 호스트로 복사

## Mac mini ↔ Mac (개발 머신) 워크플로

- 코드 편집: 개발 Mac에서 git (편의)
- git push → Mac mini가 git pull
- Mac mini Docker 컨테이너 안에서 빌드·실행

또는 Mac mini에서 직접 편집(VS Code Remote-SSH)도 가능.

## 트러블슈팅

| 증상 | 해결 |
|---|---|
| `docker pull` 시 keychain 에러 | base 이미지 수동 pull (위 사전 셋업) |
| `buildx` 안 보임 | Docker Desktop 27+ 필요. `docker buildx ls`로 확인 |
| 컨테이너 메모리 부족 | Docker Desktop → Settings → Resources → Memory 12–16 GB로 증가 |
| ros_gz 빌드 시 OOM | 메모리 늘리거나 Dockerfile의 colcon `--parallel-workers 1` 추가 |
| PX4 SITL 빌드 실패 | `NUM_JOBS=2` 환경변수로 빌드 병렬도 제한 (Dockerfile 수정) |
| `ros2 pkg list \| grep ros_gz` 비어 있음 | 과거 이미지가 `colcon build --symlink-install` 잔재로 install 트리가 dangling symlinks. 현재 Dockerfile은 이 옵션을 끄도록 수정됨 — 재빌드(이미지 캐시 무효화 필요) |
| `MicroXRCEAgent: not found` | §4.5 추가 전 구 이미지. 재빌드 필요. |
| `/fmu/out/*` 토픽 미출력 | PX4 SITL이 실행 중인지 확인. 에이전트 로그에서 `establish_session` 미출력이면 포트 8888 충돌 점검. |
| `ros2 topic echo /fmu/out/*` 메시지 0건 (publisher_count: 1) | px4_msgs 누락. `ros2 pkg prefix px4_msgs` → `Package not found`이면 §3 px4_msgs 추가 전 구 이미지. 재빌드 또는 임시 워크스페이스 빌드 ([SETUP_NATIVE_MACOS.md §6](../docs/SETUP_NATIVE_MACOS.md#6-트러블슈팅) 참조). |
| MicroXRCEAgent 빌드 OOM | Dockerfile의 `--parallel 2` → `--parallel 1`로 조정 후 재빌드. |
