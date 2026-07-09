"""OVD detector_node launch.

사용 예:
    ros2 launch intent_ovd ovd_detector.launch.py \\
        model_path:=/abs/path/to/yolov8s-worldv2.pt \\
        vocabulary:="['couch','table','chair']" \\
        device:=auto \\
        throttle_hz:=5.0

*신뢰 boundary*: ``model_path`` 가 가리키는 weight 는 launch 호출자가 책임. paper-1
표준 경로는 ``$REPO_ROOT/models/ovd/yolov8s-worldv2.pt`` (install_ovd.sh 가
``OVD_FETCH_WEIGHTS=1`` 시 받아둠).

cmsm-proof §10.1 ξ_ovd 채널 공급. ROADMAP §3 B1.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [
        DeclareLaunchArgument(
            "model_path",
            default_value="yolov8s-worldv2.pt",
            description="ultralytics weight 식별자 (절대경로 권장).",
        ),
        DeclareLaunchArgument(
            "vocabulary",
            default_value="['couch','table','chair']",
            description="초기 어휘 prompt list. 빈 list 면 노드 die. *정적* — runtime 변경 불가.",
        ),
        DeclareLaunchArgument(
            "device",
            default_value="auto",
            description="'auto' | 'mps' | 'cpu'.",
        ),
        DeclareLaunchArgument(
            "conf_threshold",
            default_value="0.25",
            description="ultralytics predict() 의 conf 파라미터.",
        ),
        DeclareLaunchArgument(
            "input_image_topic",
            default_value="/camera/image_raw",
            description="sensor_msgs/Image 구독 토픽 (SENSOR_DATA QoS).",
        ),
        DeclareLaunchArgument(
            "output_detection_topic",
            default_value="/intent/ovd/detections",
            description="vision_msgs/Detection2DArray 발행 토픽 (default reliable QoS).",
        ),
        DeclareLaunchArgument(
            "throttle_hz",
            default_value="0.0",
            description="0 = 매 프레임, > 0 = target rate frame skip.",
        ),
    ]

    node = Node(
        package="intent_ovd",
        executable="detector_node",
        name="ovd_detector",
        output="screen",
        parameters=[{
            "model_path": LaunchConfiguration("model_path"),
            "vocabulary": LaunchConfiguration("vocabulary"),
            "device": LaunchConfiguration("device"),
            "conf_threshold": LaunchConfiguration("conf_threshold"),
            "input_image_topic": LaunchConfiguration("input_image_topic"),
            "output_detection_topic": LaunchConfiguration("output_detection_topic"),
            "throttle_hz": LaunchConfiguration("throttle_hz"),
        }],
    )

    return LaunchDescription([*args, node])
