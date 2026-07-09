"""tier1_b1.launch.py — B1 정적 CBF-QP 모드.

CBF spec (cmsm-proof §7.1, 2026-05-25 잠금):
  - r_min = 0.9 m (scenario-aware lookup, 두 scenario 측 동일)
  - gamma = 4.0 /s
  - u_max = 0.5 m/s

user 위치 단일 진실 소스: [`scenario_params.params`](../../sim/scenario_params/scenario_params/params.py)
  → `tier1_filter.scenario_layout.resolve_scenario_params(scenario)` 경유.
  좌표 변경은 scenario_params 1 곳만 update.

검증 기대 (c2 시나리오, livingroom):
  - 같은 nominal 입력 (적대적 NW 접근)에 drone이 sphere 경계 (r_min=0.9m)에서 brake
  - MIN 3D 거리 ≥ 0.9m, r_min 침입? NO
  - B0의 MIN ≈ 0.2m와 대조 — CBF-QP 효과 입증

검증 기대 (y0 시나리오, yard):
  - 자녀 follow trajectory 측 user 측 모든 step 측 r_min=0.9 m 외부 잠금.
  - sim 측 *실 비행 검증* 측 Phase B (Mac mini SSH) 측 진입.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from tier1_filter.scenario_layout import (
    SCENARIO_USER_PARAMS,
    resolve_scenario_params,
)


def _build_tier1(context, *args, **kwargs):
    """LaunchConfiguration 측 scenario 측 tier1_filter 측 parameters 결정."""
    scenario = LaunchConfiguration('scenario').perform(context)
    user_params = resolve_scenario_params(scenario)  # raises RuntimeError if unknown
    return [Node(
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
            # CBF spec (cmsm-proof §7.1 P1-P5). r_min 측 scenario lookup.
            'gamma': 4.0,
            'u_max': 0.5,
            # user 위치 local ENU + r_min (scenario lookup, 4 keys).
            **user_params,
            # PX4 vehicle_local_position 토픽.
            'vehicle_local_position_topic': '/fmu/out/vehicle_local_position_v1',
        }],
    )]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'scenario',
            default_value='livingroom',
            description="Scenario 측 user 좌표 + r_min 잠금 (local ENU). "
                        f"Allowed: {sorted(SCENARIO_USER_PARAMS.keys())} — "
                        "'livingroom' (default, ADR-0009 v3) | 'yard' "
                        "(S8 sim 인프라 점검, yard_base.sdf 정합).",
        ),
        OpaqueFunction(function=_build_tier1),
    ])
