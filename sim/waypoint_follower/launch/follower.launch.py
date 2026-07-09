"""waypoint_follower 단독 기동 launch (ADR-0029 블로커 3).

목표 지점(/intent/target_waypoint) + 드론 위치 → 연속 속도 공칭
(/cmd/trajectory_setpoint_nominal) → tier1 속도 CBF.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    k_p = DeclareLaunchArgument('k_p', default_value='0.6')
    u_max = DeclareLaunchArgument('u_max', default_value='0.5')
    return LaunchDescription([
        k_p, u_max,
        Node(
            package='waypoint_follower',
            executable='follower_node',
            name='waypoint_follower',
            output='screen',
            parameters=[{
                'k_p': LaunchConfiguration('k_p'),
                'u_max': LaunchConfiguration('u_max'),
            }],
        ),
    ])
