"""teleop_keyboard.launch.py — Manual keyboard teleop launch.

전제: PX4 SITL + Gazebo + tier1_filter + g1_offboard 측 가동 중 (up.sh 측 cover).
본 launch 측 *별 Terminal 측 docker exec -it* 측 interactive tty 측 시작 의무.

사용 (Docker exec -it 패턴):
  docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \\
      "cd /workspace && source install/setup.bash && \\
       ros2 launch teleop_keyboard teleop_keyboard.launch.py"

  또는 직접:
  docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \\
      "cd /workspace && source install/setup.bash && \\
       ros2 run teleop_keyboard teleop_keyboard_node"

up.sh 측 *자동 시작 안 함* — interactive stdin 측 docker exec -d (detached) 측
*불가*. 사용자 측 g2_waypoint_player 측 *대신* 측 *수동 모드* 측 별 명령 측 직접
시작.

토픽 흐름 (ADR-0011 D1 정합, g2_waypoint_player 측 동일):
  teleop_keyboard → /cmd/trajectory_setpoint_nominal → tier1_filter (mode)
    → /cmd/trajectory_setpoint_safe → g1_offboard → PX4
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'output_topic',
            default_value='/cmd/trajectory_setpoint_nominal',
            description='ENU TwistStamped publish 대상 토픽 (tier1 nominal 입력).',
        ),
        DeclareLaunchArgument(
            'linear_speed',
            default_value='0.5',
            description='Linear velocity 측 max 측 [m/s] (default = tier1 u_max 정합).',
        ),
        DeclareLaunchArgument(
            'angular_speed',
            default_value='0.5',
            description='Angular velocity 측 max yaw rate [rad/s].',
        ),
        DeclareLaunchArgument(
            'key_timeout_s',
            default_value='0.5',
            description='키 입력 없는 측 자동 zero velocity (hover) 측 timeout [s].',
        ),
        Node(
            package='teleop_keyboard',
            executable='teleop_keyboard_node',
            name='teleop_keyboard',
            output='screen',
            emulate_tty=True,  # docker exec -it 측 stdin 측 line buffer 회피
            parameters=[{
                'use_sim_time': False,  # wall time — g1_offboard 측 동일 정책
                'output_topic': LaunchConfiguration('output_topic'),
                'linear_speed': LaunchConfiguration('linear_speed'),
                'angular_speed': LaunchConfiguration('angular_speed'),
                'key_timeout_s': LaunchConfiguration('key_timeout_s'),
            }],
        ),
    ])
