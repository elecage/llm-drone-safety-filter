"""intent_confidence publisher launch — scenario_file 인자로 시나리오 선택.

사용:
    ros2 launch intent_confidence confidence_player.launch.py scenario:=c_constant_1
    ros2 launch intent_confidence confidence_player.launch.py scenario:=c_step_down

scenario:= 인자 = 시나리오 이름 (확장자 없이). share/intent_confidence/scenarios/
아래에서 자동으로 찾음.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    scenario_arg = DeclareLaunchArgument(
        'scenario',
        default_value='c_constant_1',
        description='시나리오 이름 (share/intent_confidence/scenarios/*.yaml의 stem)',
    )
    exit_on_finish_arg = DeclareLaunchArgument(
        'exit_on_finish',
        default_value='false',
        description='시나리오 종료 시 노드 종료 여부',
    )

    publisher = Node(
        package='intent_confidence',
        executable='publisher_node',
        name='intent_confidence_publisher',
        output='screen',
        parameters=[{
            'output_topic': '/intent/grounding_confidence',
            'scenario_file': LaunchConfiguration('scenario'),
            'exit_on_finish': LaunchConfiguration('exit_on_finish'),
        }],
    )
    return LaunchDescription([scenario_arg, exit_on_finish_arg, publisher])
