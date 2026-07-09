"""tier2_gate_node launch — S6 거실 layout v3 기준 default 파라미터.

list/dict-type 파라미터는 모두 stringified JSON 으로 통일 (M_launch fix) —
ROS 2 launch DSL 의 LaunchConfiguration → declare_parameter substitution 경로가
string 만 안전히 전달하기 때문. 노드 측에서 ``json.loads`` 로 파싱 + schema 검증.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument('geofence_xmin', default_value='-3.0'),
        DeclareLaunchArgument('geofence_xmax', default_value='3.0'),
        DeclareLaunchArgument('geofence_ymin', default_value='-2.0'),
        DeclareLaunchArgument('geofence_ymax', default_value='2.0'),
        DeclareLaunchArgument('geofence_zmin', default_value='0.0'),
        DeclareLaunchArgument('geofence_zmax', default_value='2.4'),
        DeclareLaunchArgument(
            'known_objects_json',
            default_value='["sofa", "mug", "tv", "tv_stand", "coffee_table"]',
        ),
        # target_poses 는 시나리오 SDF 의 가구 ENU 좌표 (사용자 confirm 후 갱신).
        # 빈 dict 면 inspect-in-progress 자동 종료 못함 (외부 위치 입력 대기).
        DeclareLaunchArgument(
            'target_poses_json',
            default_value=(
                '{"sofa": [-2.6, 1.0, 0.4], "mug": [-0.5, 0.0, 0.5], '
                '"tv": [2.4, 0.0, 0.8], "tv_stand": [2.4, 0.0, 0.3], '
                '"coffee_table": [0.0, 0.0, 0.3]}'
            ),
        ),
        DeclareLaunchArgument('dock_pos_json', default_value='[0.5, -0.5, 0.15]'),
        DeclareLaunchArgument('progress_check_hz', default_value='10.0'),

        Node(
            package='tier2_gate',
            executable='gate_node',
            name='tier2_gate_node',
            output='screen',
            parameters=[{
                'geofence_xmin': LaunchConfiguration('geofence_xmin'),
                'geofence_xmax': LaunchConfiguration('geofence_xmax'),
                'geofence_ymin': LaunchConfiguration('geofence_ymin'),
                'geofence_ymax': LaunchConfiguration('geofence_ymax'),
                'geofence_zmin': LaunchConfiguration('geofence_zmin'),
                'geofence_zmax': LaunchConfiguration('geofence_zmax'),
                'known_objects_json': LaunchConfiguration('known_objects_json'),
                'target_poses_json': LaunchConfiguration('target_poses_json'),
                'dock_pos_json': LaunchConfiguration('dock_pos_json'),
                'progress_check_hz': LaunchConfiguration('progress_check_hz'),
            }],
        ),
    ])
