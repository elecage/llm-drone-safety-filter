"""b1a_static_rmin.launch.py — B1a baseline trial launch (정적 $r_\\text{min}$).

eval_baselines.b1a_static_rmin.b1a_config() 의 launch 측 구현 (ADR-0025 amendment
19 — B1→B1a/B1b 분리). tier1_filter 의 기존 launch
(safety/tier1/launch/tier1_b1.launch.py) 와 *동일 파라미터를 명시*해 literal 동일
구성 + paper §C 재현성 + tier1_filter default drift 회피. B1a 정의 측 intent layer
와 Tier 2 게이트 모두 불활성 이므로 본 launch 는 tier1_filter mode='b1' 단독.

paper §C trial 측 본 launch 가 단독 실행되며, fault injector / nominal source /
rosbag2 record 는 runner.py 측 별 process 로 합성.

CBF spec (cmsm-proof §7.1 P1-P5, 2026-05-25 잠금):
  - r_min = 0.9 m (b_human 0.75 + drone radius 0.142 + brake 0.025)
  - gamma = 4.0 /s (PX4 closed-loop 1/τ_ctrl)
  - u_max = 0.5 m/s (EASA C2 conservative scaling)

user 위치 (local ENU, 거실 layout v4.1, 2026-05-30 — 소파 동쪽 옆자리).
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
            'mode': 'b1',
            'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
            'input_pose_topic': '/cmd/pose_setpoint_nominal',
            'output_twist_topic': '/cmd/trajectory_setpoint_safe',
            'output_pose_topic': '/cmd/pose_setpoint_safe',
            # CBF spec (cmsm-proof §7.1 P1-P5).
            'r_min': 0.9,
            'gamma': 4.0,
            'u_max': 0.5,
            # user 위치 local ENU (거실 layout v4.1, 2026-05-30 — 소파 동쪽 옆자리).
            'user_local_x': -0.5,
            'user_local_y': 2.0,
            'user_local_z': 0.95,
            # PX4 vehicle_local_position 토픽.
            'vehicle_local_position_topic': '/fmu/out/vehicle_local_position_v1',
        }],
    )
    return LaunchDescription([tier1])
