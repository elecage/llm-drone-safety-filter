"""A4-3 sim 통합 launch — tier1 + tier2_gate + PX4·tier1 어댑터 + SDF 자동 동기.

OpaqueFunction 으로 launch 시점에 SDF 파싱해 ``known_objects_json`` /
``target_poses_json`` / ``dock_pos_json`` 을 자동 주입. 시나리오 SDF 변경 시
launch 만 다시 띄우면 모든 가구·도크 좌표가 자동 동기.

기본 SDF: ``sim/worlds/livingroom_base.sdf`` (S5/S6/S7 거실 layout v3).
다른 시나리오로 바꿀 때:

    $ ros2 launch tier2_gate sim_integration.launch.py \\
        sdf_path:=/workspace/sim/worlds/garden_base.sdf
"""

import json

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


# wall_*, ground, ceiling, sky, user_avoidance_visual, drone_dock 은
# known_objects 에서 제외 — drone_dock 은 별도 dock_pos 로 사용.
_EXCLUDE_PREFIXES = (
    'wall_', 'ground', 'ceiling', 'sky', 'user_avoidance', 'drone_dock',
)


def _launch_setup(context, *args, **kwargs):
    # SDF 파싱은 ROS 의존 없이 가능 — 본 함수 안에서 import (launch 시점에 해석).
    from tier2_gate.sdf_targets import (
        extract_known_objects_json,
        extract_model_poses,
        extract_model_poses_json,
    )

    sdf_path = LaunchConfiguration('sdf_path').perform(context)
    known_json = extract_known_objects_json(
        sdf_path, exclude_prefixes=_EXCLUDE_PREFIXES
    )
    poses_json = extract_model_poses_json(sdf_path)

    # dock_pos 는 SDF 의 drone_dock model pose — 없으면 conservative default.
    all_poses = extract_model_poses(sdf_path)
    dock = all_poses.get('drone_dock', (0.5, -0.5, 0.025))
    dock_json = json.dumps(list(dock))

    tier1 = Node(
        package='tier1_filter',
        executable='filter_node',
        name='tier1_filter',
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'mode': LaunchConfiguration('tier1_mode'),
            'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
            'input_pose_topic': '/cmd/pose_setpoint_nominal',
            'output_twist_topic': '/cmd/trajectory_setpoint_safe',
            'output_pose_topic': '/cmd/pose_setpoint_safe',
            'r_min': 0.9,
            'gamma': 4.0,
            'u_max': 0.5,
            'user_local_x': -3.1,
            'user_local_y': 2.0,
            'user_local_z': 0.95,
            'vehicle_local_position_topic': '/fmu/out/vehicle_local_position_v1',
        }],
    )

    px4_adapter = Node(
        package='tier2_gate',
        executable='px4_adapter',
        name='tier2_px4_adapter',
        output='screen',
        parameters=[{'use_sim_time': True}],
    )

    dispatch_adapter = Node(
        package='tier2_gate',
        executable='dispatch_to_tier1',
        name='tier2_dispatch_to_tier1',
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'kp': 0.5,
            'max_speed_default': 0.3,
            'publish_hz': 50.0,
            'arrival_threshold': 0.1,
        }],
    )

    gate = Node(
        package='tier2_gate',
        executable='gate_node',
        name='tier2_gate_node',
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'geofence_xmin': -3.0,
            'geofence_xmax': 3.0,
            'geofence_ymin': -2.0,
            'geofence_ymax': 2.0,
            'geofence_zmin': 0.0,
            'geofence_zmax': 2.4,
            'known_objects_json': known_json,
            'target_poses_json': poses_json,
            'dock_pos_json': dock_json,
            'progress_check_hz': 10.0,
        }],
    )

    return [tier1, px4_adapter, dispatch_adapter, gate]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            'sdf_path',
            default_value='/workspace/sim/worlds/livingroom_base.sdf',
            description='시나리오 SDF — known_objects·target_poses·dock_pos 자동 추출',
        ),
        DeclareLaunchArgument(
            'tier1_mode',
            default_value='b2',
            description='tier1 mode b0/b1/b2 — 기본 b2 (신뢰도 변조 CBF)',
        ),
        OpaqueFunction(function=_launch_setup),
    ])
