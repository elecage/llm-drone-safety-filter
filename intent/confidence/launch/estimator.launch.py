"""estimator_node launch — synthesis | live 두 모드 (ADR-0020 Amendment 2026-05-31).

synthesis (default — YAML 신호 재생):
    ros2 launch intent_confidence estimator.launch.py \
        scenario:=signals_high_confidence
    ros2 launch intent_confidence estimator.launch.py \
        scenario:=signals_s1_drop dot_c_max:=0.5

live (실 OVD/LLM 출력 위 산출 — paper §C strict e2e):
    ros2 launch intent_confidence estimator.launch.py \
        estimator_mode:=live dot_c_max:=0.833
    # referent latch TTL 30s (기본 0=무한, 대체까지):
    ros2 launch intent_confidence estimator.launch.py \
        estimator_mode:=live sigma_latch_timeout_s:=30.0

scenario:= 인자 = share/intent_confidence/scenarios/signals_*.yaml 의 stem
(synthesis 모드만 사용 — live 모드는 무시 + warn).
dot_c_max:= 인자가 음수(또는 0)면 미지정 sentinel — 노드 fallback 체인:
    launch 인자 > YAML dot_c_max: (synthesis) > cmsm-proof §7.1 시안 default 0.833.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    mode_arg = DeclareLaunchArgument(
        'estimator_mode',
        default_value='synthesis',
        description=(
            "입력 source 모드 — 'synthesis' (YAML 신호 재생, default) | 'live' "
            '(실 OVD /intent/ovd/detections + LLM /intent/llm_sigma_raw). '
            'ADR-0020 Amendment 2026-05-31.'
        ),
    )
    scenario_arg = DeclareLaunchArgument(
        'scenario',
        default_value='signals_high_confidence',
        description='시나리오 이름 (synthesis 모드, share/.../scenarios/*.yaml 의 stem)',
    )
    dot_c_max_arg = DeclareLaunchArgument(
        'dot_c_max',
        default_value='-1.0',
        description=(
            'ADR-0020 D4 변화율 한도 [1/s]. 음수(또는 0) = 미지정 sentinel — '
            '노드 안 fallback 체인: launch 인자 > YAML dot_c_max: (synthesis) > '
            'cmsm-proof §7.1 시안 default 0.833.'
        ),
    )
    output_arg = DeclareLaunchArgument(
        'output_topic',
        default_value='/intent/grounding_confidence',
        description='c̃ publish 토픽 (tier1_filter B2 가 구독)',
    )
    report_arg = DeclareLaunchArgument(
        'report_topic',
        default_value='/intent/estimator/report',
        description='EstimatorReport JSON 진단 publish 토픽',
    )
    initial_arg = DeclareLaunchArgument(
        'initial_c_tilde',
        default_value='1.0',
        description='시작 c̃ 값 (rate limiter 초기 상태)',
    )
    exit_arg = DeclareLaunchArgument(
        'exit_on_finish',
        default_value='false',
        description='시나리오 종료 시 노드 종료 여부 (synthesis 모드만 의미)',
    )
    # --- live 모드 전용 ---
    ovd_topic_arg = DeclareLaunchArgument(
        'ovd_detection_topic',
        default_value='/intent/ovd/detections',
        description='live 모드 s1 source — OVD Detection2DArray 토픽',
    )
    sigma_topic_arg = DeclareLaunchArgument(
        'sigma_raw_topic',
        default_value='/intent/llm_sigma_raw',
        description='live 모드 s2/s3 source — wrapper sigma_raw String 토픽',
    )
    timeout_arg = DeclareLaunchArgument(
        'signal_timeout_s',
        default_value='1.0',
        description='live 모드 OVD detection stale 윈도 [s] — 초과 시 s1 부재→c=0',
    )
    latch_arg = DeclareLaunchArgument(
        'sigma_latch_timeout_s',
        default_value='0.0',
        description=(
            'live 모드 referent latch TTL [s] (ADR-0020 amendment 2026-06-11 — '
            '발견 A). LLM sigma 는 발화당 1회 이벤트라 OVD 와 분리해 latch. '
            '0 이하 = 무한(새 sigma 대체까지 지속). 양수면 그 TTL 후 만료→c=0.'
        ),
    )
    rate_arg = DeclareLaunchArgument(
        'publish_rate_hz',
        default_value='10.0',
        description='live 모드 c̃ publish 주기 [Hz]',
    )

    estimator = Node(
        package='intent_confidence',
        executable='estimator_node',
        name='intent_confidence_estimator',
        output='screen',
        parameters=[{
            'estimator_mode': LaunchConfiguration('estimator_mode'),
            'output_topic': LaunchConfiguration('output_topic'),
            'report_topic': LaunchConfiguration('report_topic'),
            'scenario_file': LaunchConfiguration('scenario'),
            'dot_c_max': LaunchConfiguration('dot_c_max'),
            'initial_c_tilde': LaunchConfiguration('initial_c_tilde'),
            'exit_on_finish': LaunchConfiguration('exit_on_finish'),
            'ovd_detection_topic': LaunchConfiguration('ovd_detection_topic'),
            'sigma_raw_topic': LaunchConfiguration('sigma_raw_topic'),
            'signal_timeout_s': LaunchConfiguration('signal_timeout_s'),
            'sigma_latch_timeout_s': LaunchConfiguration('sigma_latch_timeout_s'),
            'publish_rate_hz': LaunchConfiguration('publish_rate_hz'),
        }],
    )
    return LaunchDescription([
        mode_arg, scenario_arg, dot_c_max_arg, output_arg, report_arg,
        initial_arg, exit_arg, ovd_topic_arg, sigma_topic_arg, timeout_arg,
        latch_arg, rate_arg, estimator,
    ])
