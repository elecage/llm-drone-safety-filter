"""g2_play.launch.py — G2 waypoint player launch (시나리오 인자 받음).

사용:
  ros2 launch g2_waypoint_player g2_play.launch.py scenario:=c0_up_down_sweep

전제: tier1_filter + G1이 가동 중 — 토픽 흐름은 ADR-0011 D1:
  G2 → /cmd/.._nominal → tier1_filter (mode) → /cmd/.._safe → G1 → PX4

G2는 *nominal* 토픽에만 publish — tier1이 안전 필터를 거쳐 safe 토픽으로 forward.
intent-agnostic (ADR-0005 D3): tier1은 publisher 무관, 같은 nominal 인터페이스를
*의도해석기*·fault-injection 등도 사용 가능.

ADR-0011 §D4 amendment (use_sim_time=False) — G1과 동일 시간 정책.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    scenario_arg = DeclareLaunchArgument(
        'scenario',
        default_value='c0_up_down_sweep',
        description='YAML 시나리오 파일명 (확장자 생략 가능, share/g2_waypoint_player/scenarios/ 안)',
    )

    output_topic_arg = DeclareLaunchArgument(
        'output_topic',
        default_value='/cmd/trajectory_setpoint_nominal',
        description='ENU TwistStamped publish 대상 토픽 (tier1 nominal 입력)',
    )

    position_output_topic_arg = DeclareLaunchArgument(
        'position_output_topic',
        default_value='/cmd/pose_setpoint_nominal',
        description='ENU PoseStamped publish 대상 토픽 (tier1 nominal 입력)',
    )

    player_node = Node(
        package='g2_waypoint_player',
        executable='waypoint_player_node',
        name='g2_waypoint_player',
        output='screen',
        parameters=[{
            'use_sim_time': False,
            'scenario_file': LaunchConfiguration('scenario'),
            'output_topic': LaunchConfiguration('output_topic'),
            'position_output_topic': LaunchConfiguration('position_output_topic'),
            'frame_id': 'world',
            'exit_on_finish': True,
        }],
    )

    return LaunchDescription([
        scenario_arg,
        output_topic_arg,
        position_output_topic_arg,
        player_node,
    ])
