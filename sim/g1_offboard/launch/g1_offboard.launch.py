"""g1_offboard.launch.py — G1 offboard control + PX4 클럭 브릿지 launch.

전제: T1 (PX4 SITL) + T2 (gz GUI unpaused) + MicroXRCEAgent 실행 중.

사용:
  ./docker/run.sh "colcon build --packages-select g1_offboard && \\
      source install/setup.bash && \\
      ros2 launch g1_offboard g1_offboard.launch.py"

Task #5 — /clock 컨테이너 연결 회복 (ADR-0011 D4 복귀):
  px4_clock_bridge_node: /fmu/out/vehicle_local_position_v1 (~50 Hz) 의
  timestamp 필드를 rosgraph_msgs/msg/Clock 으로 변환 → /clock publish.
  이 노드 자체는 /clock 의 생산자이므로 use_sim_time=False (wall time).

  g1_offboard_control: use_sim_time=True 로 복귀. px4_clock_bridge 가
  /clock 을 공급하므로 타이머·get_clock().now() 모두 시뮬 시간 기준.
  g1_offboard 의 arming_warmup_s=1.0 이 /clock 안정화 여유를 제공한다.
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    clock_bridge = Node(
        package='g1_offboard',
        executable='px4_clock_bridge_node',
        name='px4_clock_bridge',
        output='screen',
        parameters=[{
            'use_sim_time': False,  # /clock 생산자 — 자신은 wall time 사용
        }],
    )

    g1_node = Node(
        package='g1_offboard',
        executable='offboard_control_node',
        name='g1_offboard_control',
        output='screen',
        parameters=[{
            'use_sim_time': True,  # ADR-0011 D4 복귀 — px4_clock_bridge 가 /clock 공급
            'input_topic': '/cmd/trajectory_setpoint_safe',
            'publish_rate_hz': 20.0,
            'takeoff_altitude_m': 1.5,
            'climb_velocity_mps': 1.0,
            'altitude_tolerance_m': 0.2,
            'arming_warmup_s': 1.0,
            'nominal_timeout_s': 0.5,
            'arm_retry_period_s': 1.0,
        }],
    )

    return LaunchDescription([clock_bridge, g1_node])
