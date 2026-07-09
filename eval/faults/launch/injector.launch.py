"""eval_faults injector_node launch — fault scenario YAML 측 trial 측 launch.

usage:
    ros2 launch eval_faults injector.launch.py \\
        scenario_file:=/path/to/fault_scenario.yaml seed:=42

scenario_file 측 *절대 경로* 권장 — share/eval_faults/scenarios/ 측 inst all
된 YAML 측 `$(find-pkg-share eval_faults)/scenarios/*.yaml` 측 resolve 가능
(후속 작업 측 PathSubstitution wiring).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    scenario_file_arg = DeclareLaunchArgument(
        'scenario_file',
        description='fault_scenario YAML 절대 경로 (필수)',
    )
    seed_arg = DeclareLaunchArgument(
        'seed',
        default_value='-1',
        description='rng seed — -1 측 scenario.seed 사용, 그 외 override',
    )

    injector_node = Node(
        package='eval_faults',
        executable='injector_node',
        name='eval_faults_injector',
        parameters=[{
            'scenario_file': LaunchConfiguration('scenario_file'),
            'seed': LaunchConfiguration('seed'),
        }],
        output='screen',
    )

    return LaunchDescription([
        scenario_file_arg,
        seed_arg,
        injector_node,
    ])
