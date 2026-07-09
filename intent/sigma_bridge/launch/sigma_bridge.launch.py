"""sigma_bridge_node launch — σ → /intent/target_waypoint (ADR-0029 D-A1).

본실험 live 경로 (운용 가드 off — tier1 r_max 단일 안전 책임, ADR-0028 Track B):
    ros2 launch intent_sigma_bridge sigma_bridge.launch.py scenario_id:=S5

데모 트랙 (Track A — 운용 가드 on):
    ros2 launch intent_sigma_bridge sigma_bridge.launch.py \
        scenario_id:=S5 user_guard_radius_m:=1.0

하류: /intent/target_waypoint → waypoint_follower → 연속 속도 → tier1 CBF.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.descriptions import ParameterValue


def _float_param(name: str):
    """LaunchConfiguration 을 DOUBLE 로 강제 — 정수꼴 입력('0')도 coerce.

    ros2 launch 는 '0' 을 INTEGER 로 추론하나 노드는 DOUBLE 선언이라 타입 충돌로
    init 실패한다(예: SIGMA_STANDOFF=0). value_type=float 로 항상 DOUBLE 보장.
    """
    return ParameterValue(LaunchConfiguration(name), value_type=float)


def generate_launch_description():
    args = [
        DeclareLaunchArgument(
            'scenario_id', default_value='S5',
            description='시나리오 ID (S5-S8) — spawn·scene world 좌표 lookup.',
        ),
        DeclareLaunchArgument(
            'output_waypoint_topic', default_value='/intent/target_waypoint',
            description='목표 지점 발행 토픽 (PoseStamped ENU → waypoint_follower).',
        ),
        DeclareLaunchArgument(
            'user_guard_radius_m', default_value='0.0',
            description=(
                '사용자 회피 영역 운용 가드 반경 [m]. 0 = 비활성 (본실험 기본 — '
                'tier1 r_max 가 단일 안전 책임, ADR-0028 Track B). 데모는 1.0 등.'
            ),
        ),
        DeclareLaunchArgument(
            'target_standoff_m', default_value='0.7',
            description='객체 standoff [m] — 가구 충돌 회피 데모 가드. 0 = 비활성.',
        ),
        DeclareLaunchArgument(
            'detour_arrival_threshold_m', default_value='0.5',
            description='우회 waypoint 도달 임계 [m].',
        ),
        DeclareLaunchArgument(
            'takeoff_altitude_m', default_value='1.5',
            description='최소 고도 floor [m].',
        ),
    ]

    node = Node(
        package='intent_sigma_bridge',
        executable='sigma_bridge_node',
        name='sigma_bridge',
        output='screen',
        parameters=[{
            'scenario_id': LaunchConfiguration('scenario_id'),
            'output_waypoint_topic': LaunchConfiguration('output_waypoint_topic'),
            'user_guard_radius_m': _float_param('user_guard_radius_m'),
            'target_standoff_m': _float_param('target_standoff_m'),
            'detour_arrival_threshold_m': _float_param('detour_arrival_threshold_m'),
            'takeoff_altitude_m': _float_param('takeoff_altitude_m'),
        }],
    )

    return LaunchDescription([*args, node])
