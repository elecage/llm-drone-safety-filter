"""b3_context_aug.launch.py — B3 baseline trial launch.

eval_baselines.b3_context_aug.b3_config() 의 launch 측 구현. 본 PR (B7 #10) scope
측 launch 는 tier1_filter mode='b2' 단독 stub — b2_modulated.launch.py 와 *동일
구성* (literal). context_aug=True 의 실 의미 = LLM 입력에 context graph + ego-stream
융합 (paper §6) — 이는 intent/llm/ wrapper (별 PR, ADR-0025 D5 의 intent/llm/{ovd,
llm_cloud,llm_edge,vla,classifier}/*.py) 측 *context fusion 모드 선택* 으로 실현.
intent/llm/ wrapper 의 launch 합성은 B7 #12 runner.py 측 *BaselineConfig 입력 →
wrapper 선택* logic 에서 결정.

paper §C trial 측 본 launch 가 단독 실행되며, fault injector / nominal source /
rosbag2 record / intent/llm/ wrapper / intent/confidence/estimator_node 는 runner.py
(B7 #12) 측 별 process 로 합성.

tier1_filter 파라미터는 b2_modulated.launch.py 와 동일 (literal 동일) — B2 와 B3
의 tier1 측 동작은 같고, 차이는 LLM 입력 측 context 융합 여부.

CBF spec (cmsm-proof §7.1 P1-P5, 2026-05-25 잠금):
  - r_min = 0.9 m, r_max = 1.5 m (시안)
  - gamma = 4.0 /s
  - u_max = 0.5 m/s
  - dot_c_max = 0.833 /s (= u_max/(r_max-r_min), §6 가용성 조건에서 자동 derive)

신뢰도 입력 (tier1_filter mode='b2' 공통):
  - /intent/grounding_confidence (std_msgs/Float32, c ∈ [0,1])
  - 미수신 시 fail-active default c̃ = 1.0 → r = r_min → B1 과 동일 거동.

user 위치 (local ENU, default 거실 layout v3):
  world (-2.6, 1.5, 1.1) - spawn (0.5, -0.5, 0.15) = local (-3.1, 2.0, 0.95)
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
            # 신뢰도 입력 토픽 (B2/B3/B4 공통).
            'grounding_confidence_topic': '/intent/grounding_confidence',
        }],
    )
    return LaunchDescription([tier1])
