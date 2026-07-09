"""e2_sim_bridge.launch.py — Sim 트랙 E2 ROS 2 launch 자동화.

동시에 시작하는 프로세스:
  1. MicroXRCEAgent (udp4, 포트 8888)
       PX4 SITL(macOS 호스트)과 컨테이너 ROS 2 DDS를 연결하는 uXRCE-DDS 브릿지.
       PX4 기본 설정(XRCE_DDS_AG_IP=127.0.0.1, port=8888)으로 자동 연결된다.
  2. sim_user_marker/user_marker_node
       world→user 정적 TF + 사용자 회피 영역 RViz 마커를 주기 발행.

전제조건:
  - 호스트에서 PX4 SITL이 이미 실행 중이어야 한다
    (T1: scripts/run_native_sitl_livingroom.sh)
  - T2에서 gz GUI가 unpaused 상태여야 함 (gz sim -g -r). paused 상태면
    PX4가 lockstep으로 부팅 중 멈춰 uxrce_dds_client가 안 시작됨.
  - Docker 실행 시 -p 8888:8888/udp 포트 매핑 필요 (docker/run.sh 기본 포함)
  - 빌드 직후라면 `source install/setup.bash`로 로컬 워크스페이스를 source해야 함
    (entrypoint는 /opt/ros/humble과 /opt/ros_gz_ws/install/만 source)

표준 실행 명령 (T3):
  ./docker/run.sh "colcon build --packages-select sim_user_marker && \\
      source install/setup.bash && \\
      ros2 launch sim_user_marker e2_sim_bridge.launch.py"

  ※ --symlink-install 금지 (Dockerfile §3 주석): build/ 제거 시 install/ 가
    dangling symlinks 덩어리가 되어 launch 직후 silent termination 발생 (D3 원인).
    --packages-select sim_user_marker 로 범위를 한정해 빌드 시간 단축.

E2 통과 우회 경로 (참고 — 위 명령으로 안 될 때 대안):
  ./docker/run.sh "$MICROXRCE_AGENT_BIN udp4 -p 8888"
  docker exec -it llmdrone-sim bash -c "cd /workspace && colcon build \
      --packages-select sim_user_marker && source install/setup.bash && \
      ros2 run sim_user_marker user_marker_node"
  상세 progress/2026-05-23-e2-passed.md §D3.

E2 검증 (같은 컨테이너 안에서):
  ros2 topic list | grep fmu
  → /fmu/out/vehicle_local_position 등이 보이면 통과
  → ros2 topic echo --qos-reliability best_effort /fmu/out/vehicle_attitude
    데이터 흐름 확인 (px4_msgs 빌드돼 있어야 함; Dockerfile §3 참조)
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, LogInfo, OpaqueFunction, TimerAction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from scenario_params.params import VALID_SCENARIOS, user_marker_params


def _build_user_marker(context, *args, **kwargs):
    """LaunchConfiguration 측 scenario 측 user_marker_node 측 parameters 결정."""
    scenario = LaunchConfiguration('scenario').perform(context)
    params = user_marker_params(scenario)  # unknown scenario 측 RuntimeError
    return [Node(
        package='sim_user_marker',
        executable='user_marker_node',
        name='sim_user_marker',
        output='screen',
        parameters=[params],
    )]


def generate_launch_description():
    agent_bin = os.environ.get(
        'MICROXRCE_AGENT_BIN',
        '/opt/MicroXRCEAgent/build/MicroXRCEAgent',
    )

    micro_xrce_agent = ExecuteProcess(
        cmd=[agent_bin, 'udp4', '-p', '8888'],
        output='screen',
        name='micro_xrce_agent',
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'scenario',
            default_value='livingroom',
            description="Scenario 측 user 좌표 + r_min 잠금. "
                        f"Allowed: {sorted(VALID_SCENARIOS)} — "
                        "'livingroom' (default, ADR-0009 v3) | 'yard' "
                        "(S8 sim 인프라 점검, sim/worlds/yard_base.sdf 정합).",
        ),
        LogInfo(msg='[E2] uXRCE-DDS 에이전트 시작 (UDP 8888) ...'),
        micro_xrce_agent,
        # 에이전트가 PX4와 세션을 맺을 시간을 확보한 뒤 마커 노드를 시작.
        # PX4가 실행 중이면 연결은 ~1초 내에 완료되므로 2초 여유로 충분.
        LogInfo(msg='[E2] 2초 대기 후 사용자 마커 노드 시작 ...'),
        TimerAction(
            period=2.0,
            actions=[OpaqueFunction(function=_build_user_marker)],
        ),
    ])
