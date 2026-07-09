"""tier1_b1_max.launch.py — B1b 정적 CBF-QP 모드 (정적 $r_\\text{max}$).

ADR-0025 amendment 19 — B1 을 B1a(정적 $r_\\text{min}$, 효율 baseline)·B1b(정적
$r_\\text{max}$, 안전 baseline)로 분리. 본 launch 는 B1b — tier1_filter mode='b1_max',
정적 반경 $r_\\text{max}$ 로 비행.

CBF spec (cmsm-proof §7.1, 2026-05-25 잠금):
  - gamma = 4.0 /s
  - u_max = 0.5 m/s
  - 정적 반경 = r_max (본 manual launch 는 location 기반 — user 좌표·r_min 은
    scenario lookup, r_max 는 filter_node 기본값 1.5 m 사용. 본실험 격자 경로
    (launch_composition)는 scenario_id(S5–S8)를 전달해 ADR-0023 시나리오별 r_max 를
    resolve 한다.)

검증 기대 (c2 시나리오, livingroom):
  - 같은 nominal 입력에 drone 이 sphere 경계 (r_max)에서 brake — B1a(r_min=0.9m)보다
    더 보수적인 정지. B1a 대비 *과보수성* 상승 + 안전 위반 동일(0) 확인.

본 baseline 은 C2 트레이드오프의 *안전점* — B2 변조가 B1a(효율점)·B1b(안전점)를
모두 dominate 함을 입증하는 대조군.
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
            'mode': 'b1_max',
            'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
            'input_pose_topic': '/cmd/pose_setpoint_nominal',
            'output_twist_topic': '/cmd/trajectory_setpoint_safe',
            'output_pose_topic': '/cmd/pose_setpoint_safe',
            # CBF spec (cmsm-proof §7.1 P1-P5). 정적 반경 = r_max (node 기본 1.5 m).
            'gamma': 4.0,
            'u_max': 0.5,
            # user 위치 local ENU + r_min (scenario lookup, 4 keys). r_max 는
            # node 기본값 — 본실험 경로는 scenario_id resolve.
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
