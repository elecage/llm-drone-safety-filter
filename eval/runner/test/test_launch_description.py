"""eval_runner.launch_description 단위 테스트.

NodeSpec → launch action 변환 측 pure helper 측 host venv 측 검증 가능 — 단위
test 측 (a) node kwargs 변환 + (b) rosbag2 CLI 변환 + (c) build_trial_launch_actions
+ (d) build_launch_description 측 cover. ROS 2 (`launch`, `launch_ros`) 측
unavailable 측 importorskip 측 graceful skip.

ROS 2 측 실 launch action 빌드 측 host venv 측 partial — `launch_ros.actions.Node`
+ `launch.actions.ExecuteProcess` instantiation 측 (rclpy 측 무관) host venv 측
pip install `launch`+`launch_ros` 측 성공 시 동작. Mac mini Docker 측 colcon
test 측 *실 ROS 2 환경* 측 ros2 launch service 측 정합 검증.
"""

from __future__ import annotations

import pytest

# ROS 2 launch (launch · launch_ros) 측 host venv 측 unavailable 측 graceful skip
# 가드. PYTHONPATH 측 `eval/baselines/launch/` 디렉토리 측 namespace package
# 'launch' 측 mask 가능 — pytest.importorskip('launch') 단독 측 false positive
# → 본 helper 측 *LaunchDescription / Node 실 import* 측 확인 측 명시적 skip.
def _ros2_launch_available() -> bool:
    try:
        from launch import LaunchDescription  # noqa: F401
        from launch.actions import ExecuteProcess  # noqa: F401
        from launch_ros.actions import Node  # noqa: F401
    except ImportError:
        return False
    return True


_ROS2_LAUNCH_REASON = 'ROS 2 launch · launch_ros 측 host venv 측 unavailable (Mac mini Docker colcon test 측 cover).'
ros2_required = pytest.mark.skipif(
    not _ros2_launch_available(),
    reason=_ROS2_LAUNCH_REASON,
)

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.b3_context_aug import b3_config
from eval_baselines.b4_full_loop import b4_config
from eval_baselines.schemas import BaselineConfig
from eval_faults.fault_scenario import FaultChannel, FaultScenario

from eval_runner.launch_composition import (
    NODE_NAME_ROSBAG,
    NodeSpec,
    compose_trial_node_specs,
)
from eval_runner.launch_description import (
    build_trial_launch_actions,
    build_launch_description,
    node_spec_to_node_kwargs,
    process_spec_to_cmd,
    rosbag_node_spec,
)
from eval_runner.schemas import TrialSpec


# -------------------------------------------------------------------- fixtures


def _make_none_fault() -> FaultScenario:
    return FaultScenario(
        name='test_none',
        description='test',
        channel=FaultChannel.NONE,
        variant=None,
        context_kwargs={},
        seed=42,
    )


def _make_trial(config: BaselineConfig, seed: int = 12345) -> TrialSpec:
    return TrialSpec(
        scenario_id='S5',
        baseline_config=config,
        fault_scenario=_make_none_fault(),
        episode_id=0,
        seed=seed,
    )


# -------------------------------------------------------------------- node_spec_to_node_kwargs


class TestNodeSpecToKwargs:
    def test_returns_required_keys(self) -> None:
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'mode': 'b2', 'scenario': 'S5'},
        )
        kwargs = node_spec_to_node_kwargs(spec)
        assert kwargs['package'] == 'tier1_filter'
        assert kwargs['executable'] == 'filter_node'
        assert kwargs['name'] == 'tier1_filter'
        assert kwargs['output'] == 'screen'

    def test_parameters_list_of_dict_convention(self) -> None:
        """ROS 2 convention — `parameters=[{...}]` (list-of-dict, 단일 dict wrap)."""
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'mode': 'b2'},
        )
        kwargs = node_spec_to_node_kwargs(spec)
        assert isinstance(kwargs['parameters'], list)
        assert len(kwargs['parameters']) == 1
        assert isinstance(kwargs['parameters'][0], dict)

    def test_use_sim_time_locked_true(self) -> None:
        """paper §C 측 sim 전용 — `use_sim_time=True` 잠금 (gz_clock source 측 정합)."""
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'mode': 'b2'},
        )
        kwargs = node_spec_to_node_kwargs(spec)
        assert kwargs['parameters'][0]['use_sim_time'] is True

    def test_spec_parameters_propagated(self) -> None:
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'mode': 'b2', 'scenario': 'S5', 'r_min': 0.9},
        )
        kwargs = node_spec_to_node_kwargs(spec)
        params = kwargs['parameters'][0]
        assert params['mode'] == 'b2'
        assert params['scenario'] == 'S5'
        assert params['r_min'] == 0.9

    def test_empty_parameters_still_has_use_sim_time(self) -> None:
        """parameters={} 측도 `use_sim_time` 잠금 보장."""
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={},
        )
        kwargs = node_spec_to_node_kwargs(spec)
        assert kwargs['parameters'][0] == {'use_sim_time': True}

    def test_spec_parameters_cannot_override_use_sim_time(self) -> None:
        """paper §C 측 sim 전용 trial 측 use_sim_time=True 잠금 — spec.parameters
        측 명시 측 ValueError raise 측 강제 차단 (PR #136 review C-3(b) 정합).
        """
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'use_sim_time': False},
        )
        with pytest.raises(ValueError, match='use_sim_time'):
            node_spec_to_node_kwargs(spec)

    def test_spec_parameters_cannot_override_use_sim_time_even_when_true(self) -> None:
        """use_sim_time=True 명시 측도 차단 — *암묵 잠금 의도* 측 *명시* 측 별도.
        paper §C narrative 측 launch_description.py 측 단독 책임 측 잠금 (PR #136
        review C-3(b)).
        """
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'use_sim_time': True},
        )
        with pytest.raises(ValueError, match='use_sim_time'):
            node_spec_to_node_kwargs(spec)

    def test_process_kind_rejected(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={'output': 'foo', 'topics': ('/clock',)},
        )
        with pytest.raises(ValueError, match="kind='node'"):
            node_spec_to_node_kwargs(spec)


# -------------------------------------------------------------------- process_spec_to_cmd


class TestProcessSpecToCmd:
    def test_rosbag_basic(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={
                'output': 'trial_foo',
                'topics': ('/clock', '/fmu/out/vehicle_local_position_v1'),
            },
        )
        cmd = process_spec_to_cmd(spec)
        assert cmd[:5] == ['ros2', 'bag', 'record', '-o', 'trial_foo']
        assert cmd[5:] == ['/clock', '/fmu/out/vehicle_local_position_v1']

    def test_node_kind_rejected(self) -> None:
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
        )
        with pytest.raises(ValueError, match="kind='process'"):
            process_spec_to_cmd(spec)

    def test_unsupported_executable_rejected(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='topic echo',
            name='echo_proc',
            kind='process',
            parameters={'output': 'foo', 'topics': ('/clock',)},
        )
        with pytest.raises(ValueError, match='미지원'):
            process_spec_to_cmd(spec)

    def test_missing_output_raises(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={'topics': ('/clock',)},
        )
        with pytest.raises(KeyError):
            process_spec_to_cmd(spec)

    def test_topic_pub_utterance(self) -> None:
        """ADR-0030 F5 발화 publisher — ros2 topic pub CLI 합성."""
        spec = NodeSpec(
            package='ros2',
            executable='topic pub',
            name='trial_utterance_pub',
            kind='process',
            parameters={
                'topic': '/intent/user_prompt_raw',
                'message': '소파 보여줘',
                'times': 5,
                'rate': 1.0,
            },
        )
        cmd = process_spec_to_cmd(spec)
        assert cmd[:7] == [
            'ros2', 'topic', 'pub', '--times', '5', '--rate', '1.0',
        ]
        assert cmd[7] == '/intent/user_prompt_raw'
        assert cmd[8] == 'std_msgs/msg/String'
        assert cmd[9] == '{data: "소파 보여줘"}'

    def test_topic_pub_relative_topic_rejected(self) -> None:
        spec = NodeSpec(
            package='ros2', executable='topic pub', name='u', kind='process',
            parameters={'topic': 'no_slash', 'message': 'x', 'times': 1, 'rate': 1.0},
        )
        with pytest.raises(ValueError, match='leading'):
            process_spec_to_cmd(spec)

    def test_missing_topics_raises(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={'output': 'foo'},
        )
        with pytest.raises(KeyError):
            process_spec_to_cmd(spec)

    def test_empty_topics_rejected(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={'output': 'foo', 'topics': ()},
        )
        with pytest.raises(ValueError, match='빈 sequence'):
            process_spec_to_cmd(spec)

    def test_topic_missing_leading_slash_rejected(self) -> None:
        """ROS 2 topic 측 절대 경로 의무 — leading '/' 누락 측 강제 raise."""
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={'output': 'foo', 'topics': ('/clock', 'no_slash_topic')},
        )
        with pytest.raises(ValueError, match="leading '/'"):
            process_spec_to_cmd(spec)


# -------------------------------------------------------------------- rosbag_node_spec helper


class TestRosbagNodeSpec:
    def test_returns_rosbag_spec(self) -> None:
        trial = _make_trial(b0_config())
        spec = rosbag_node_spec(trial)
        assert spec.name == NODE_NAME_ROSBAG
        assert spec.kind == 'process'
        assert spec.parameters['output'] == trial.trial_id

    def test_returns_for_all_baselines(self) -> None:
        for cfg_fn in (b0_config, b1a_config, b1b_config, b2_config, b3_config, b4_config):
            trial = _make_trial(cfg_fn())
            spec = rosbag_node_spec(trial)
            assert spec.name == NODE_NAME_ROSBAG


# -------------------------------------------------------------------- ROS 2 dependent — importorskip


@ros2_required
class TestBuildLaunchActions:
    """build_trial_launch_actions / build_launch_description 측 ROS 2 의존 영역.

    host venv 측 `launch`+`launch_ros` 측 미설치 측 *skip* (Mac mini Docker 측
    colcon test 측 cover). PYTHONPATH 측 `eval/baselines/launch/` 디렉토리 측
    namespace package mask 회피 측 `_ros2_launch_available()` helper 측 *LaunchDescription
    / Node 실 import* 측 확인.
    """

    def test_actions_count_b0(self) -> None:
        actions = build_trial_launch_actions(_make_trial(b0_config()))
        assert len(actions) == 5

    def test_actions_count_b4(self) -> None:
        actions = build_trial_launch_actions(_make_trial(b4_config()))
        assert len(actions) == 7

    def test_action_count_matches_node_spec_count(self) -> None:
        """build_trial_launch_actions 측 합성 결과 측 NodeSpec list 1:1 매칭."""
        for cfg_fn in (b0_config, b1a_config, b1b_config, b2_config, b3_config, b4_config):
            trial = _make_trial(cfg_fn())
            specs = compose_trial_node_specs(trial)
            actions = build_trial_launch_actions(trial)
            assert len(actions) == len(specs)

    def test_last_action_is_rosbag_execute_process(self) -> None:
        """compose_trial_node_specs 측 합성 순서 측 rosbag2 가 마지막 — launch
        actions 측 동일 순서 보장.
        """
        from launch.actions import ExecuteProcess

        actions = build_trial_launch_actions(_make_trial(b0_config()))
        assert isinstance(actions[-1], ExecuteProcess)

    def test_first_action_is_tier1_node(self) -> None:
        """compose_trial_node_specs 측 합성 순서 측 tier1_filter 가 첫번째 — launch
        actions 측 동일 순서 + launch_ros.actions.Node instance.
        """
        from launch_ros.actions import Node

        actions = build_trial_launch_actions(_make_trial(b0_config()))
        assert isinstance(actions[0], Node)

    def test_build_launch_description_returns_launch_description(self) -> None:
        from launch import LaunchDescription

        ld = build_launch_description(_make_trial(b2_config()))
        assert isinstance(ld, LaunchDescription)


# -------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_node_kwargs_deterministic(self) -> None:
        """동일 NodeSpec 측 node_spec_to_node_kwargs 호출 측 동일 결과 — TrialSpec
        seed 측 변경 없는 한 launch 측 reproducible.
        """
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1_filter',
            kind='node',
            parameters={'mode': 'b2', 'scenario': 'S5'},
        )
        assert node_spec_to_node_kwargs(spec) == node_spec_to_node_kwargs(spec)

    def test_process_cmd_deterministic(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag2',
            kind='process',
            parameters={'output': 'trial_foo', 'topics': ('/a', '/b')},
        )
        assert process_spec_to_cmd(spec) == process_spec_to_cmd(spec)
