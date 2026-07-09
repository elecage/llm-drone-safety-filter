"""tier1_b2.launch.py — B2 신뢰도 변조 CBF-QP 모드.

cmsm-proof §6 정리 2 매핑 — 시변 $\\tilde c(t)$ 하 안전집합 전방불변성.

CBF spec (cmsm-proof §7.1 P1-P5):
  - r_min = 0.9 m (scenario-aware lookup, 두 scenario 측 동일), gamma = 4.0 /s, u_max = 0.5 m/s
  - r_max / dot_c_max = livingroom *manual 기본값* (1.5 / 0.833). 본 launch 는
    up.sh 측 location(livingroom/yard) 인자 기반 *수동 검증용*. **paper §C 본실험
    값 아님** — 실험 경로(runner → filter_node)는 scenario_id(S5-S8)별 r_max 를
    scenario_params.tier1_cbf_params(ADR-0023)에서 resolve(filter_node 가 scenario
    파라미터로 내부 override). manual demo 의 1.5 는 livingroom feasibility 내.

신뢰도 입력:
  - /intent/grounding_confidence (std_msgs/Float32, c ∈ [0,1])
  - 미수신 시 fail-active default $\\tilde c = 1.0$ → $r = r_\\text{min}$ → B1과 동일 동작.

user 위치 단일 진실 소스: [`scenario_params.params`](../../sim/scenario_params/scenario_params/params.py)
  → `tier1_filter.scenario_layout.resolve_scenario_params(scenario)` 경유.
  좌표 변경은 scenario_params 1 곳만 update.

검증 기대 (c2 시나리오, livingroom):
  - 상수 $c = 1.0$ → B1 regression (MIN ≈ 0.877 m, overshoot ≈ 0.023 m).
  - 상수 $c = 0.0$ → $r = r_\\text{max} = 1.5$ m → 더 멀리 brake (MIN ≈ 1.5 m 근처).
  - 시간 함수 $c(t)$ (램프 or step) → trajectory 변동 + 변화율 제한기 동작 확인.

검증 기대 (y0 시나리오, yard):
  - 자녀 follow trajectory 측 user r_min 외부 + 신뢰도 변조 측 *r(c̃)* 측 yard
    layout 측 *별 c 분포* 측 *별 trajectory*. Phase B (Mac mini SSH) 측 진입.
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
    """LaunchConfiguration 측 scenario 측 tier1_filter (B2) 측 parameters 결정."""
    scenario = LaunchConfiguration('scenario').perform(context)
    user_params = resolve_scenario_params(scenario)  # raises RuntimeError if unknown
    return [Node(
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
            # CBF spec (cmsm-proof §7.1 P1-P5). r_min 측 scenario lookup.
            'r_max': 1.5,
            'gamma': 4.0,
            'u_max': 0.5,
            'dot_c_max': 0.833,
            # user 위치 local ENU + r_min (scenario lookup, 4 keys).
            **user_params,
            # PX4 vehicle_local_position 토픽.
            'vehicle_local_position_topic': '/fmu/out/vehicle_local_position_v1',
            # 신뢰도 입력 토픽 (B2 전용).
            'grounding_confidence_topic': '/intent/grounding_confidence',
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
