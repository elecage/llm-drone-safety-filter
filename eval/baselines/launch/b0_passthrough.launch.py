"""b0_passthrough.launch.py — B0 baseline trial launch.

eval_baselines.b0_passthrough.b0_config() 의 launch 측 구현. 현재 본 launch 는
tier1_filter 의 기존 launch (safety/tier1/launch/tier1_b0.launch.py) 와 *동일
구성을 복제* — 본 PR (B7 #7) scope 측 stub 단계라 차이 없음. 후속 B7 PR (#10
b3_context_aug · #11 b4_full_loop) 에서 intent layer / Tier 2 게이트 노드 추가
시 baseline 간 차이 발생 자리. B0 정의 측 intent layer 와 Tier 2 게이트 모두
불활성 이므로 본 launch 는 tier1_filter mode='b0' 단독.

paper §C trial 측 본 launch 가 단독 실행되며, fault injector / nominal source /
rosbag2 record 는 runner.py (B7 #12) 측 별 process 로 합성.
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
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
