"""b1b_static_rmax.launch.py — B1b baseline trial launch (정적 $r_\\text{max}$).

eval_baselines.b1b_static_rmax.b1b_config() 의 launch 측 구현 (ADR-0025 amendment
19 — B1→B1a/B1b 분리). B1a 와 동일 정적 CBF 구성이되 tier1_filter mode='b1_max' —
정적 반경 $r_\\text{max}$ 로 *실 비행*. B1b 정의 측 intent layer 와 Tier 2 게이트
모두 불활성.

paper §C trial 측 본 launch 가 단독 실행되며, fault injector / nominal source /
rosbag2 record 는 runner.py 측 별 process 로 합성.

CBF spec (cmsm-proof §7.1 P1-P5, 2026-05-25 잠금):
  - r_max = 1.5 m (거실 layout 시안; 본실험 격자 경로는 scenario_id resolve)
  - gamma = 4.0 /s (PX4 closed-loop 1/τ_ctrl)
  - u_max = 0.5 m/s (EASA C2 conservative scaling)

본 launch 는 manual e2e 편의 — 정적 반경 = r_max (node 측 b1_max 모드가 r_max 를
정적 반경으로 사용). 본실험 격자는 launch_composition 이 scenario_id 를 전달해
ADR-0023 시나리오별 r_max 를 resolve.

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
            'mode': 'b1_max',
            'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
            'input_pose_topic': '/cmd/pose_setpoint_nominal',
            'output_twist_topic': '/cmd/trajectory_setpoint_safe',
            'output_pose_topic': '/cmd/pose_setpoint_safe',
            # CBF spec (cmsm-proof §7.1 P1-P5). b1_max 모드 → 정적 반경 = r_max.
            'r_min': 0.9,
            'r_max': 1.5,
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
