"""b2_modulated.launch.py — B2 baseline trial launch.

eval_baselines.b2_modulated.b2_config() 의 launch 측 구현. tier1_filter 의 기존
launch (safety/tier1/launch/tier1_b2.launch.py) 와 *동일 파라미터를 명시*해
literal 동일 구성 + paper §C 재현성 + tier1_filter default drift 회피
(PR #115 review S-1 lesson 정합). 본 PR (B7 #9) scope 측 stub 단계라 추가 노드
없음 — 후속 B7 PR (#10 b3_context_aug · #11 b4_full_loop) 에서 intent layer /
Tier 2 게이트 노드 추가 시 baseline 간 차이 발생 자리. B2 정의 측 *context_aug*
불활성 이므로 본 launch 는 tier1_filter mode='b2' 단독 — 신뢰도 c 는 외부 source
(paper §C 측 fault injector 또는 estimator_node) 가 `/intent/grounding_confidence`
토픽으로 publish.

paper §C trial 측 본 launch 가 단독 실행되며, fault injector / nominal source /
rosbag2 record 는 runner.py (B7 #12) 측 별 process 로 합성.

CBF spec (cmsm-proof §7.1 P1-P5, 2026-05-25 잠금):
  - r_min = 0.9 m, gamma = 4.0 /s, u_max = 0.5 m/s
  - r_max / dot_c_max = livingroom *manual 기본값* (1.5 / 0.833). **paper §C 본실험
    값 아님** — 실험 경로(runner compose_trial_node_specs → filter_node)는
    scenario_id(S5-S8)별 r_max 를 scenario_params.tier1_cbf_params 단일 소스
    (ADR-0023+세션49: S5/S6/S7=1.80·S8=1.15, dot_c_max 파생)에서 resolve. 본 stub
    launch 는 runner 가 사용 안 하는 manual/legacy 표현이라 livingroom 기본값 유지.

신뢰도 입력 (B2 전용):
  - /intent/grounding_confidence (std_msgs/Float32, c ∈ [0,1])
  - 미수신 시 fail-active default c̃ = 1.0 → r = r_min → B1 과 동일 거동.

user 위치 (local ENU, default 거실 layout v3):
  world (-2.6, 1.5, 1.1) - spawn (0.5, -0.5, 0.15) = local (-3.1, 2.0, 0.95)
  시나리오별 user_local_* override 는 runner.py 측 별 처리 (paper §C 4 시나리오
  사용자 좌표 차이).
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
            'mode': 'b2',
            'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
            'input_pose_topic': '/cmd/pose_setpoint_nominal',
            'output_twist_topic': '/cmd/trajectory_setpoint_safe',
            'output_pose_topic': '/cmd/pose_setpoint_safe',
            # CBF spec (cmsm-proof §7.1 P1-P5).
            'r_min': 0.9,
            'r_max': 1.5,
            'gamma': 4.0,
            'u_max': 0.5,
            'dot_c_max': 0.833,
            # user 위치 local ENU (거실 layout v4.1, 2026-05-30 — 소파 동쪽 옆자리).
            'user_local_x': -0.5,
            'user_local_y': 2.0,
            'user_local_z': 0.95,
            # PX4 vehicle_local_position 토픽.
            'vehicle_local_position_topic': '/fmu/out/vehicle_local_position_v1',
            # 신뢰도 입력 토픽 (B2 전용).
            'grounding_confidence_topic': '/intent/grounding_confidence',
        }],
    )
    return LaunchDescription([tier1])
