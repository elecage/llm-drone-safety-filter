"""tier1_b0.launch.py — B0 (pass-through) 모드로 안전 필터 노드 launch.

G2 nominal source → tier1 (B0) → G1 토픽 인터페이스 통과 확인용.
B0는 필터 없음이라 c2 baseline 결과가 tier1 도입 전과 동일해야 함 (regression test).
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    tier1 = Node(
        package='tier1_filter',
        executable='filter_node',
        name='tier1_filter',
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'mode': 'b0',
            'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
            'input_pose_topic': '/cmd/pose_setpoint_nominal',
            'output_twist_topic': '/cmd/trajectory_setpoint_safe',
            'output_pose_topic': '/cmd/pose_setpoint_safe',
        }],
    )
    return LaunchDescription([tier1])
