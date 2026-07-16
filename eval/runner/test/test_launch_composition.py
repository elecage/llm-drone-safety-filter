"""eval_runner.launch_composition 단위 테스트.

5 baseline 별 NodeSpec list 합성 검증 — node count + identity + parameter wiring
+ ablation chain 정합.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b1a_static_rmin import b1a_config
from eval_baselines.b1b_static_rmax import b1b_config
from eval_baselines.b2_modulated import b2_config
from eval_baselines.b3_context_aug import b3_config
from eval_baselines.b4_full_loop import b4_config
from eval_baselines.schemas import BaselineConfig, BaselineMode
from eval_faults.fault_scenario import (
    FAULT_CHANNEL_FAULTED_TOPIC,
    FaultChannel,
    FaultScenario,
)

from eval_runner.launch_composition import (
    NODE_NAME_CONF_PUBLISHER,
    NODE_NAME_CONTEXT_GRAPH,
    NODE_NAME_ESTIMATOR,
    NODE_NAME_INJECTOR,
    NODE_NAME_INTENT_LLM,
    NODE_NAME_ROSBAG,
    NODE_NAME_TIER1,
    NODE_NAME_TIER2_GATE,
    NODE_NAME_UTTERANCE,
    SYNTHETIC_C_TOPIC,
    VALID_NODE_KINDS,
    NodeSpec,
    compose_trial_node_specs,
    expected_node_count,
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


def _make_fault(channel: FaultChannel, variant: str = 'dummy_variant') -> FaultScenario:
    """non-NONE 채널 fault 사양. compose 는 channel 만 보므로 variant 는 임의 문자열
    (build_fault_context 의 enum 검증은 compose 경로에서 호출되지 않음)."""
    return FaultScenario(
        name=f'test_{channel.value}',
        description='test',
        channel=channel,
        variant=variant,
        context_kwargs={},
        seed=42,
    )


def _make_trial(config: BaselineConfig, seed: int = 12345,
                scenario_id: str = 'S5',
                fault: FaultScenario | None = None,
                confidence_source: str = 'live') -> TrialSpec:
    return TrialSpec(
        scenario_id=scenario_id,
        baseline_config=config,
        fault_scenario=fault if fault is not None else _make_none_fault(),
        episode_id=0,
        seed=seed,
        confidence_source=confidence_source,
    )


# -------------------------------------------------------------------- NodeSpec


class TestNodeSpec:
    def test_node_kind_valid(self) -> None:
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1',
            kind='node',
        )
        assert spec.kind == 'node'

    def test_process_kind_valid(self) -> None:
        spec = NodeSpec(
            package='ros2',
            executable='bag record',
            name='rosbag',
            kind='process',
        )
        assert spec.kind == 'process'

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match='kind'):
            NodeSpec(
                package='tier1_filter',
                executable='filter_node',
                name='tier1',
                kind='lifecycle',
            )

    def test_empty_executable_rejected(self) -> None:
        with pytest.raises(ValueError, match='executable'):
            NodeSpec(
                package='tier1_filter',
                executable='',
                name='tier1',
                kind='node',
            )

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match='name'):
            NodeSpec(
                package='tier1_filter',
                executable='filter_node',
                name='',
                kind='node',
            )

    def test_default_parameters_empty(self) -> None:
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1',
            kind='node',
        )
        assert spec.parameters == {}

    def test_frozen(self) -> None:
        spec = NodeSpec(
            package='tier1_filter',
            executable='filter_node',
            name='tier1',
            kind='node',
        )
        with pytest.raises((AttributeError, Exception)):
            spec.name = 'changed'  # type: ignore[misc]

    def test_valid_kinds_constant(self) -> None:
        assert VALID_NODE_KINDS == ('node', 'process')


# -------------------------------------------------------------------- node count


class TestNodeCount:
    # ADR-0030 F5 per-trial 발화 publisher 추가로 종전 5/5/5/6/7 → 6/6/6/6/7/8.
    def test_b0_six_nodes(self) -> None:
        trial = _make_trial(b0_config())
        specs = compose_trial_node_specs(trial)
        assert len(specs) == 6
        assert expected_node_count(trial) == 6

    def test_b1a_six_nodes(self) -> None:
        trial = _make_trial(b1a_config())
        specs = compose_trial_node_specs(trial)
        assert len(specs) == 6
        assert expected_node_count(trial) == 6

    def test_b1b_six_nodes(self) -> None:
        trial = _make_trial(b1b_config())
        specs = compose_trial_node_specs(trial)
        assert len(specs) == 6
        assert expected_node_count(trial) == 6

    def test_b2_six_nodes(self) -> None:
        trial = _make_trial(b2_config())
        specs = compose_trial_node_specs(trial)
        assert len(specs) == 6
        assert expected_node_count(trial) == 6

    def test_b3_seven_nodes(self) -> None:
        """B3 = + context_graph (6 → 7)."""
        trial = _make_trial(b3_config())
        specs = compose_trial_node_specs(trial)
        assert len(specs) == 7
        assert expected_node_count(trial) == 7

    def test_b4_eight_nodes(self) -> None:
        """B4 = + tier2_gate (7 → 8)."""
        trial = _make_trial(b4_config())
        specs = compose_trial_node_specs(trial)
        assert len(specs) == 8
        assert expected_node_count(trial) == 8


class TestUtterancePublisher:
    """ADR-0030 F5 — per-trial 발화 publisher 합성."""

    def test_utterance_spec_present_all_baselines(self) -> None:
        for cfg in (b0_config(), b1a_config(), b1b_config(), b2_config(), b3_config(), b4_config()):
            specs = compose_trial_node_specs(_make_trial(cfg))
            utt = [s for s in specs if s.name == NODE_NAME_UTTERANCE]
            assert len(utt) == 1
            assert utt[0].kind == 'process'
            assert utt[0].executable == 'topic pub'

    def test_utterance_message_from_scenario(self) -> None:
        from scenario_params.params import scenario_utterance
        for sid in ('S5', 'S6'):
            trial = _make_trial(b0_config(), scenario_id=sid)
            utt = next(
                s for s in compose_trial_node_specs(trial)
                if s.name == NODE_NAME_UTTERANCE
            )
            assert utt.parameters['message'] == scenario_utterance(sid)
            assert utt.parameters['topic'] == '/intent/user_prompt_raw'

    def test_utterance_is_last(self) -> None:
        """발화 publisher 가 마지막 — rosbag recorder 구독 establish 후 발화."""
        specs = compose_trial_node_specs(_make_trial(b0_config()))
        assert specs[-1].name == NODE_NAME_UTTERANCE


# -------------------------------------------------------------------- node identity


class TestNodeIdentity:
    def test_b0_node_names(self) -> None:
        specs = compose_trial_node_specs(_make_trial(b0_config()))
        names = {s.name for s in specs}
        assert names == {
            NODE_NAME_TIER1, NODE_NAME_INTENT_LLM,
            NODE_NAME_ESTIMATOR, NODE_NAME_INJECTOR, NODE_NAME_ROSBAG,
            NODE_NAME_UTTERANCE,
        }

    def test_b3_adds_context_graph(self) -> None:
        b2_names = {s.name for s in compose_trial_node_specs(_make_trial(b2_config()))}
        b3_names = {s.name for s in compose_trial_node_specs(_make_trial(b3_config()))}
        added = b3_names - b2_names
        assert added == {NODE_NAME_CONTEXT_GRAPH}

    def test_b4_adds_tier2_gate(self) -> None:
        b3_names = {s.name for s in compose_trial_node_specs(_make_trial(b3_config()))}
        b4_names = {s.name for s in compose_trial_node_specs(_make_trial(b4_config()))}
        added = b4_names - b3_names
        assert added == {NODE_NAME_TIER2_GATE}

    def test_all_node_names_unique_per_trial(self) -> None:
        """단일 trial 측 모든 NodeSpec 측 name unique — ROS 2 launch 측 conflict
        회피.
        """
        for cfg_fn in (b0_config, b1a_config, b1b_config, b2_config, b3_config, b4_config):
            specs = compose_trial_node_specs(_make_trial(cfg_fn()))
            names = [s.name for s in specs]
            assert len(names) == len(set(names))


# -------------------------------------------------------------------- parameter wiring


class TestParameterWiring:
    def test_tier1_mode_propagated(self) -> None:
        """tier1_filter node 측 'mode' parameter 측 BaselineConfig.tier1_mode 정합."""
        for cfg_fn, expected in (
            (b0_config, 'b0'),
            (b1a_config, 'b1'),
            (b1b_config, 'b1_max'),
            (b2_config, 'b2'),
            (b3_config, 'b2'),
            (b4_config, 'b2'),
        ):
            specs = compose_trial_node_specs(_make_trial(cfg_fn()))
            tier1 = next(s for s in specs if s.name == NODE_NAME_TIER1)
            assert tier1.parameters['mode'] == expected

    def test_brake_buffer_off_by_default(self) -> None:
        """TIER1_BRAKE_BUFFER_M 미설정 시 tier1 에 brake_buffer_m 파라미터 없음 (기존 거동)."""
        import os
        old = os.environ.pop('TIER1_BRAKE_BUFFER_M', None)
        try:
            specs = compose_trial_node_specs(_make_trial(b2_config()))
            tier1 = next(s for s in specs if s.name == NODE_NAME_TIER1)
            assert 'brake_buffer_m' not in tier1.parameters
        finally:
            if old is not None:
                os.environ['TIER1_BRAKE_BUFFER_M'] = old

    def test_brake_buffer_from_env(self) -> None:
        """TIER1_BRAKE_BUFFER_M 설정 시 tier1 brake_buffer_m 파라미터로 전파 (ADR-0050 D2)."""
        import os
        old = os.environ.get('TIER1_BRAKE_BUFFER_M')
        os.environ['TIER1_BRAKE_BUFFER_M'] = '0.15'
        try:
            specs = compose_trial_node_specs(_make_trial(b2_config()))
            tier1 = next(s for s in specs if s.name == NODE_NAME_TIER1)
            assert tier1.parameters['brake_buffer_m'] == 0.15
        finally:
            if old is None:
                os.environ.pop('TIER1_BRAKE_BUFFER_M', None)
            else:
                os.environ['TIER1_BRAKE_BUFFER_M'] = old

    def test_intent_llm_mode_direct_when_no_context(self) -> None:
        """context_aug=False 측 intent_llm wrapper mode='direct'."""
        for cfg_fn in (b0_config, b1a_config, b1b_config, b2_config):
            specs = compose_trial_node_specs(_make_trial(cfg_fn()))
            wrapper = next(s for s in specs if s.name == NODE_NAME_INTENT_LLM)
            assert wrapper.parameters['mode'] == 'direct'

    def test_intent_llm_mode_fusion_when_context(self) -> None:
        """context_aug=True 측 intent_llm wrapper mode='fusion'."""
        for cfg_fn in (b3_config, b4_config):
            specs = compose_trial_node_specs(_make_trial(cfg_fn()))
            wrapper = next(s for s in specs if s.name == NODE_NAME_INTENT_LLM)
            assert wrapper.parameters['mode'] == 'fusion'

    def test_scenario_propagated_to_all_scenario_aware_nodes(self) -> None:
        """tier1·context·intent_llm·tier2·estimator 측 'scenario' parameter 정합."""
        trial = _make_trial(b4_config())
        specs = compose_trial_node_specs(trial)
        scenario_aware = {
            NODE_NAME_TIER1, NODE_NAME_CONTEXT_GRAPH, NODE_NAME_INTENT_LLM,
            NODE_NAME_TIER2_GATE, NODE_NAME_ESTIMATOR,
        }
        for s in specs:
            if s.name in scenario_aware:
                assert s.parameters.get('scenario') == 'S5'

    def test_estimator_dot_c_max_from_scenario(self) -> None:
        """ADR-0020 D9 — estimator NodeSpec 에 시나리오별 dot_c_max(ADR-0023 파생)
        명시 전달. 변화율 제한기 단일화로 estimator 가 sentinel fallback(0.833)
        대신 filter_node 와 동일한 시나리오 값을 적용해 가용성(T2-4)이 정합한다.
        """
        from scenario_params.params import tier1_cbf_params
        for sid in ('S5', 'S6'):
            specs = compose_trial_node_specs(
                _make_trial(b2_config(), scenario_id=sid)
            )
            est = next(s for s in specs if s.name == NODE_NAME_ESTIMATOR)
            expected = tier1_cbf_params(sid)['dot_c_max']
            assert est.parameters['dot_c_max'] == expected, (
                f"{sid}: estimator dot_c_max={est.parameters.get('dot_c_max')} "
                f"!= tier1_cbf_params={expected}"
            )

    def test_estimator_mode_live(self) -> None:
        """ADR-0029 D1 / paper §7.6 — 본실험 estimator 는 live 모드(검출기 출력 →
        추정기). 종전 미전달 시 기본 synthesis 인데 scenario_file 도 없어 크래시.
        """
        for cfg in (b0_config(), b1a_config(), b1b_config(), b2_config(), b3_config(), b4_config()):
            specs = compose_trial_node_specs(_make_trial(cfg))
            est = next(s for s in specs if s.name == NODE_NAME_ESTIMATOR)
            assert est.parameters['estimator_mode'] == 'live'

    def test_trial_seed_in_injector(self) -> None:
        """ROADMAP C25 — fault_injector 측 trial.seed 입력.

        파라미터 키 = 'seed' (injector_node declare/read 이름). 'trial_seed'
        였던 종전 키는 injector 가 안 읽어 scenario.seed(42)로 fallback 되던
        버그 → per-trial seed 미적용. 본 test 가 키 정합 회귀 가드.
        """
        trial = _make_trial(b0_config(), seed=99999)
        specs = compose_trial_node_specs(trial)
        injector = next(s for s in specs if s.name == NODE_NAME_INJECTOR)
        assert injector.parameters['seed'] == 99999
        assert 'trial_seed' not in injector.parameters  # 옛 키 재유입 가드

    def test_injector_seed_param_matches_node_declared_name(self) -> None:
        """injector NodeSpec seed 키 ↔ injector_node 가 declare 하는 param 이름 정합.

        AST 로 injector_node.py 의 declare_parameter 호출을 추출해 'seed' 선언
        확인 — launch_composition 의 'seed' 키가 실제로 읽히는지 cross-check
        (C25 통합 가드, 'trial_seed' drift 재발 방지).
        """
        import ast
        from pathlib import Path
        src = (
            Path(__file__).resolve().parents[2]
            / 'faults' / 'eval_faults' / 'injector_node.py'
        ).read_text(encoding='utf-8')
        declared = {
            call.args[0].value
            for call in ast.walk(ast.parse(src))
            if isinstance(call, ast.Call)
            and getattr(call.func, 'attr', None) == 'declare_parameter'
            and call.args and isinstance(call.args[0], ast.Constant)
        }
        assert 'seed' in declared, f"injector_node declare_parameter 측 'seed' 부재 — {declared}"
        trial = _make_trial(b0_config(), seed=7)
        injector = next(
            s for s in compose_trial_node_specs(trial)
            if s.name == NODE_NAME_INJECTOR
        )
        assert set(injector.parameters) & declared >= {'seed'}, (
            "launch_composition seed 키가 injector_node declare 이름과 불일치"
        )

    def test_injector_scenario_file_path(self) -> None:
        """ADR-0030 F10 — injector 는 scenario_file(fault YAML 경로)+seed 만 (node
        선언 정합). 종전 fault_scenario_name·fault_channel 은 노드가 무시.
        """
        trial = _make_trial(b0_config())
        specs = compose_trial_node_specs(trial)
        injector = next(s for s in specs if s.name == NODE_NAME_INJECTOR)
        sf = injector.parameters['scenario_file']
        assert sf.endswith('.yaml') and trial.fault_scenario.name in sf
        # 노드가 안 읽는 종전 키 재유입 가드.
        assert 'fault_scenario_name' not in injector.parameters
        assert 'fault_channel' not in injector.parameters

    def test_rosbag_output_uses_trial_id(self) -> None:
        """rosbag2 output 측 trial.trial_id 정합 — bag 파일명·디렉토리 잠금."""
        trial = _make_trial(b0_config())
        specs = compose_trial_node_specs(trial)
        rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
        assert rosbag.parameters['output'] == trial.trial_id
        assert rosbag.kind == 'process'

    def test_rosbag_topics_lock_adr0025_d4(self) -> None:
        """ADR-0025 D4 잠금 토픽 셋 정합 (NONE 채널 — _faulted 추가 없음)."""
        trial = _make_trial(b0_config())
        specs = compose_trial_node_specs(trial)
        rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
        topics = set(rosbag.parameters['topics'])
        assert topics == {
            '/fmu/out/vehicle_local_position_v1',
            '/cmd/trajectory_setpoint_safe',
            '/intent/grounding_confidence',
            '/intent/estimator/report',
            '/tier2/decision',
            '/clock',
        }

    def test_rosbag_records_active_fault_channel_faulted_topic(self) -> None:
        """무결성 가드 (ADR-0025 amendment): 활성 fault 채널의 _faulted 출력
        토픽을 record 셋에 포함 — bag_integrity 가 ≥1 sample 로 미주입 검출."""
        for channel, topic in FAULT_CHANNEL_FAULTED_TOPIC.items():
            trial = _make_trial(b0_config(), fault=_make_fault(channel))
            specs = compose_trial_node_specs(trial)
            rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
            assert topic in rosbag.parameters['topics'], (
                f'채널 {channel.value} 의 _faulted 토픽 {topic} 미기록'
            )

    def test_rosbag_none_channel_no_faulted_topic(self) -> None:
        """NONE 채널 측 변형 출력 없음 → _faulted 토픽 추가 안 함."""
        trial = _make_trial(b0_config(), fault=_make_none_fault())
        specs = compose_trial_node_specs(trial)
        rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
        assert all('_faulted' not in t for t in rosbag.parameters['topics'])

    def test_rosbag_b4_records_gate_dispatch_topic(self) -> None:
        """B4(tier2): 게이트 accept-dispatch(SIGMA_FINAL=/intent/llm_sigma_raw)를
        record — C3 제안↔승인 로깅 + bag_integrity 의 accept 여부 판정 (세션 53)."""
        trial = _make_trial(b4_config())
        specs = compose_trial_node_specs(trial)
        rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
        assert '/intent/llm_sigma_raw' in rosbag.parameters['topics']

    def test_rosbag_non_b4_no_gate_dispatch_topic(self) -> None:
        """tier2 미사용 baseline(B0–B3)은 게이트 dispatch 토픽을 별도 record 안 함
        (NONE 채널 — hallucination 은 동 토픽이 _faulted 라 별도 경로)."""
        for cfg_fn in (b0_config, b1a_config, b1b_config, b2_config, b3_config):
            trial = _make_trial(cfg_fn(), fault=_make_none_fault())
            specs = compose_trial_node_specs(trial)
            rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
            assert '/intent/llm_sigma_raw' not in rosbag.parameters['topics']

    def test_rosbag_b4_hallucination_no_duplicate_dispatch_topic(self) -> None:
        """B4+hallucination: SIGMA_FINAL 이 이미 _faulted 로 포함 → 중복 추가 방지."""
        trial = _make_trial(b4_config(), fault=_make_fault(FaultChannel.HALLUCINATION))
        specs = compose_trial_node_specs(trial)
        rosbag = next(s for s in specs if s.name == NODE_NAME_ROSBAG)
        topics = list(rosbag.parameters['topics'])
        assert topics.count('/intent/llm_sigma_raw') == 1


# -------------------------------------------------------------------- chain invariant


class TestChainCountInvariant:
    """ablation chain 측 *node count 차이* 측 단축 의미 정합.

    PR #119 TestAblationChainInvariant + ablation_invariant.py 측 *3 축* 차이
    1 측 단축 의미 ↔ 본 test 측 *합성 결과 node count* 측 단축 의미 정합.
    """

    def test_b0_b1a_same_count_only_tier1_parameter_differs(self) -> None:
        """B0→B1a = tier1 'mode' parameter 만 차이 (node count 동일 6)."""
        b0_specs = compose_trial_node_specs(_make_trial(b0_config()))
        b1a_specs = compose_trial_node_specs(_make_trial(b1a_config()))
        assert len(b0_specs) == len(b1a_specs) == 6
        b0_tier1 = next(s for s in b0_specs if s.name == NODE_NAME_TIER1)
        b1a_tier1 = next(s for s in b1a_specs if s.name == NODE_NAME_TIER1)
        assert b0_tier1.parameters['mode'] != b1a_tier1.parameters['mode']

    def test_b1a_b1b_same_count_only_tier1_parameter_differs(self) -> None:
        """B1a→B1b = tier1 'mode' parameter 만 차이 (node count 동일 6)."""
        b1a_specs = compose_trial_node_specs(_make_trial(b1a_config()))
        b1b_specs = compose_trial_node_specs(_make_trial(b1b_config()))
        assert len(b1a_specs) == len(b1b_specs) == 6
        b1a_tier1 = next(s for s in b1a_specs if s.name == NODE_NAME_TIER1)
        b1b_tier1 = next(s for s in b1b_specs if s.name == NODE_NAME_TIER1)
        assert b1a_tier1.parameters['mode'] != b1b_tier1.parameters['mode']

    def test_b1b_b2_same_count_only_tier1_parameter_differs(self) -> None:
        b1b_specs = compose_trial_node_specs(_make_trial(b1b_config()))
        b2_specs = compose_trial_node_specs(_make_trial(b2_config()))
        assert len(b1b_specs) == len(b2_specs) == 6
        b1b_tier1 = next(s for s in b1b_specs if s.name == NODE_NAME_TIER1)
        b2_tier1 = next(s for s in b2_specs if s.name == NODE_NAME_TIER1)
        assert b1b_tier1.parameters['mode'] != b2_tier1.parameters['mode']

    def test_b2_b3_adds_one_node(self) -> None:
        """B2→B3 = +context_graph + intent_llm mode 변경 (5 → 6)."""
        b2_specs = compose_trial_node_specs(_make_trial(b2_config()))
        b3_specs = compose_trial_node_specs(_make_trial(b3_config()))
        assert len(b3_specs) == len(b2_specs) + 1

    def test_b3_b4_adds_one_node(self) -> None:
        """B3→B4 = +tier2_gate (6 → 7)."""
        b3_specs = compose_trial_node_specs(_make_trial(b3_config()))
        b4_specs = compose_trial_node_specs(_make_trial(b4_config()))
        assert len(b4_specs) == len(b3_specs) + 1


# -------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_same_trial_same_specs(self) -> None:
        """동일 TrialSpec 측 compose_trial_node_specs 호출 측 동일 결과."""
        trial = _make_trial(b4_config())
        specs1 = compose_trial_node_specs(trial)
        specs2 = compose_trial_node_specs(trial)
        assert specs1 == specs2


# -------------------------------------------------------------------- fault remap


def _spec_by_name(specs, name):
    return next(s for s in specs if s.name == name)


class TestFaultRemapParametersOverride:
    """ADR-0029 D-A4 — fault 채널별 소비자 NodeSpec parameters override.

    소비자(wrapper·estimator)가 토픽을 ROS 파라미터로 노출 → fault 활성 시 그
    파라미터를 injector 의 _faulted 출력으로 가리킨다 (launch_ros remappings 아님).
    """

    def test_hallucination_inline_routes_to_actuation(self) -> None:
        """인라인(세션 49): B0+hallucination 체인 wrapper→chain0→injector→raw.
        faulted σ 가 actuation(/intent/llm_sigma_raw) 도달 → sigma_bridge+estimator."""
        trial = _make_trial(b0_config(), fault=_make_fault(FaultChannel.HALLUCINATION))
        specs = compose_trial_node_specs(trial)
        wrapper = _spec_by_name(specs, NODE_NAME_INTENT_LLM)
        injector = _spec_by_name(specs, NODE_NAME_INJECTOR)
        # wrapper → chain0 → injector → /intent/llm_sigma_raw (actuation).
        assert wrapper.parameters['output_topic'] == '/intent/llm_sigma_chain0'
        assert injector.parameters['sigma_in_topic'] == '/intent/llm_sigma_chain0'
        assert injector.parameters['sigma_out_topic'] == '/intent/llm_sigma_raw'

    def test_b4_tier2_inline_gate(self) -> None:
        """B4(tier2): wrapper→chain0→gate→/intent/llm_sigma_raw. gate 가 σ 직렬
        검증(C3) + decision 토픽 eval 정합(/tier2/decision)."""
        trial = _make_trial(b4_config())
        specs = compose_trial_node_specs(trial)
        wrapper = _spec_by_name(specs, NODE_NAME_INTENT_LLM)
        gate = _spec_by_name(specs, NODE_NAME_TIER2_GATE)
        assert wrapper.parameters['output_topic'] == '/intent/llm_sigma_chain0'
        assert gate.parameters['command_topic'] == '/intent/llm_sigma_chain0'
        assert gate.parameters['dispatch_topic'] == '/intent/llm_sigma_raw'
        assert gate.parameters['decision_topic'] == '/tier2/decision'
        # 시나리오 geofence/known/dock 도출됨.
        assert 'known_objects_json' in gate.parameters
        assert 'geofence_xmax' in gate.parameters

    def test_b4_hallucination_double_chain(self) -> None:
        """B4+hallucination: wrapper→chain0→injector→chain1→gate→raw (이중 인라인)."""
        trial = _make_trial(b4_config(), fault=_make_fault(FaultChannel.HALLUCINATION))
        specs = compose_trial_node_specs(trial)
        injector = _spec_by_name(specs, NODE_NAME_INJECTOR)
        gate = _spec_by_name(specs, NODE_NAME_TIER2_GATE)
        assert injector.parameters['sigma_in_topic'] == '/intent/llm_sigma_chain0'
        assert injector.parameters['sigma_out_topic'] == '/intent/llm_sigma_chain1'
        assert gate.parameters['command_topic'] == '/intent/llm_sigma_chain1'
        assert gate.parameters['dispatch_topic'] == '/intent/llm_sigma_raw'

    def test_adversarial_overrides_wrapper_utterance_topic(self) -> None:
        trial = _make_trial(b0_config(), fault=_make_fault(FaultChannel.ADVERSARIAL))
        wrapper = _spec_by_name(compose_trial_node_specs(trial), NODE_NAME_INTENT_LLM)
        assert wrapper.parameters['utterance_topic'] == '/intent/user_prompt_faulted'

    def test_attribute_mismatch_overrides_estimator_ovd_topic(self) -> None:
        trial = _make_trial(
            b0_config(), fault=_make_fault(FaultChannel.ATTRIBUTE_MISMATCH))
        estimator = _spec_by_name(compose_trial_node_specs(trial), NODE_NAME_ESTIMATOR)
        assert estimator.parameters['ovd_detection_topic'] == '/intent/ovd/detections_faulted'

    def test_hallucination_does_not_touch_wrapper(self) -> None:
        trial = _make_trial(b0_config(), fault=_make_fault(FaultChannel.HALLUCINATION))
        wrapper = _spec_by_name(compose_trial_node_specs(trial), NODE_NAME_INTENT_LLM)
        assert 'utterance_topic' not in wrapper.parameters

    def test_adversarial_does_not_touch_estimator(self) -> None:
        trial = _make_trial(b0_config(), fault=_make_fault(FaultChannel.ADVERSARIAL))
        estimator = _spec_by_name(compose_trial_node_specs(trial), NODE_NAME_ESTIMATOR)
        assert 'sigma_raw_topic' not in estimator.parameters
        assert 'ovd_detection_topic' not in estimator.parameters

    def test_none_fault_no_topic_override(self) -> None:
        """none 채널 trial 은 raw 토픽 직접 — 어떤 override 도 없음."""
        specs = compose_trial_node_specs(_make_trial(b0_config()))
        estimator = _spec_by_name(specs, NODE_NAME_ESTIMATOR)
        wrapper = _spec_by_name(specs, NODE_NAME_INTENT_LLM)
        assert 'sigma_raw_topic' not in estimator.parameters
        assert 'ovd_detection_topic' not in estimator.parameters
        assert 'utterance_topic' not in wrapper.parameters

    def test_fault_remap_preserves_node_count(self) -> None:
        """fault remap 은 parameters 만 바꾸고 노드 수는 불변(영속 셸 노드 미추가)."""
        base = compose_trial_node_specs(_make_trial(b2_config()))
        for channel in (
            FaultChannel.HALLUCINATION,
            FaultChannel.ADVERSARIAL,
            FaultChannel.ATTRIBUTE_MISMATCH,
            FaultChannel.COGNITIVE_LAPSE,
        ):
            specs = compose_trial_node_specs(
                _make_trial(b2_config(), fault=_make_fault(channel)))
            assert len(specs) == len(base)

    def test_cognitive_lapse_no_consumer_override(self) -> None:
        """cognitive_lapse 소비자(wrapper/tier2) wiring 미구현 — override 없음(D-A4 별 항목)."""
        trial = _make_trial(b0_config(), fault=_make_fault(FaultChannel.COGNITIVE_LAPSE))
        specs = compose_trial_node_specs(trial)
        estimator = _spec_by_name(specs, NODE_NAME_ESTIMATOR)
        wrapper = _spec_by_name(specs, NODE_NAME_INTENT_LLM)
        assert 'sigma_raw_topic' not in estimator.parameters
        assert 'ovd_detection_topic' not in estimator.parameters
        assert 'utterance_topic' not in wrapper.parameters


class TestTrackBUserPositionInjection:
    """amendment 20 — Track B 사용자 지향 적대 변형은 시나리오별 사용자 world 위치를
    injector 에 주입(user_position_world override, scenario_params 단일 출처)."""

    def _track_b_fault(self) -> FaultScenario:
        return _make_fault(
            FaultChannel.HALLUCINATION, variant='position_worst_user_direct',
        )

    def test_livingroom_user_world_injected(self) -> None:
        """S5(거실) — user_position_world == scenario_params livingroom world."""
        trial = _make_trial(
            b0_config(), scenario_id='S5', fault=self._track_b_fault())
        injector = _spec_by_name(
            compose_trial_node_specs(trial), NODE_NAME_INJECTOR)
        assert injector.parameters['user_position_world'] == [0.0, 1.5, 1.1]

    def test_non_track_b_fault_no_injection(self) -> None:
        """넓은 격자 변형(target_swap_dangerous)은 user_position_world 미주입(하위 호환)."""
        trial = _make_trial(
            b0_config(), scenario_id='S5',
            fault=_make_fault(
                FaultChannel.HALLUCINATION, variant='target_swap_dangerous'))
        injector = _spec_by_name(
            compose_trial_node_specs(trial), NODE_NAME_INJECTOR)
        assert 'user_position_world' not in injector.parameters


class TestTier2GateConfidenceWiring:
    """B4 c-배선 정정(2026-06-22) — estimator 가 *게이트 입력*(pre-gate σ)을 읽어야
    c 가 게이트 결정 시점에 가용(게이트 출력=SIGMA_FINAL 은 accept 시만 발행→순환)."""

    def test_b4_estimator_reads_gate_input(self) -> None:
        """B4(tier2) — estimator sigma_raw_topic == gate command_topic(pre-gate σ)."""
        trial = _make_trial(b4_config())
        specs = compose_trial_node_specs(trial)
        est = _spec_by_name(specs, NODE_NAME_ESTIMATOR)
        gate = _spec_by_name(specs, NODE_NAME_TIER2_GATE)
        assert est.parameters['sigma_raw_topic'] == gate.parameters['command_topic']

    def test_b4_hallucination_estimator_reads_post_injector(self) -> None:
        """B4 + hallucination — gate 입력 = post-injector σ, estimator 도 그걸 읽음."""
        trial = _make_trial(
            b4_config(), fault=_make_fault(FaultChannel.HALLUCINATION))
        specs = compose_trial_node_specs(trial)
        est = _spec_by_name(specs, NODE_NAME_ESTIMATOR)
        gate = _spec_by_name(specs, NODE_NAME_TIER2_GATE)
        assert est.parameters['sigma_raw_topic'] == gate.parameters['command_topic']

    def test_non_tier2_estimator_default_sigma(self) -> None:
        """비-tier2(B2) — estimator sigma_raw_topic 미설정(기본 SIGMA_FINAL 그대로)."""
        for cfg in (b0_config(), b1a_config(), b2_config(), b3_config()):
            specs = compose_trial_node_specs(_make_trial(cfg))
            est = _spec_by_name(specs, NODE_NAME_ESTIMATOR)
            assert 'sigma_raw_topic' not in est.parameters


# -------------------------------------------- synthetic confidence (ADR-0050 D7)


class TestSyntheticConfidence:
    """confidence_source='synthetic:<profile>' → publisher_node 추가 +
    estimator external 모드 (ADR-0050 D7 안 B)."""

    def test_live_default_no_publisher(self) -> None:
        """기본 live — publisher_node 없음, estimator live 모드."""
        specs = compose_trial_node_specs(_make_trial(b2_config()))
        names = [s.name for s in specs]
        assert NODE_NAME_CONF_PUBLISHER not in names
        est = next(s for s in specs if s.name == NODE_NAME_ESTIMATOR)
        assert est.parameters['estimator_mode'] == 'live'

    def test_synthetic_adds_publisher(self) -> None:
        """synthetic:c_constant_1 — publisher_node 추가."""
        specs = compose_trial_node_specs(
            _make_trial(b2_config(), confidence_source='synthetic:c_constant_1')
        )
        pub = next((s for s in specs if s.name == NODE_NAME_CONF_PUBLISHER), None)
        assert pub is not None, 'publisher_node 미추가'
        assert pub.package == 'intent_confidence'
        assert pub.executable == 'publisher_node'
        assert pub.parameters['scenario_file'] == 'c_constant_1.yaml'
        assert pub.parameters['output_topic'] == SYNTHETIC_C_TOPIC

    def test_synthetic_estimator_external(self) -> None:
        """synthetic — estimator external 모드 + external_c_topic 배선."""
        specs = compose_trial_node_specs(
            _make_trial(b2_config(), confidence_source='synthetic:c_stall')
        )
        est = next(s for s in specs if s.name == NODE_NAME_ESTIMATOR)
        assert est.parameters['estimator_mode'] == 'external'
        assert est.parameters['external_c_topic'] == SYNTHETIC_C_TOPIC
        # live 전용 remap 미적용
        assert 'ovd_detection_topic' not in est.parameters

    def test_synthetic_trial_id_suffix(self) -> None:
        """trial_id 에 __c-<profile> 접미, live 는 접미 없음."""
        live = _make_trial(b2_config())
        synth = _make_trial(b2_config(), confidence_source='synthetic:c_constant_mid')
        assert '__c-' not in live.trial_id
        assert synth.trial_id.endswith('__c-c_constant_mid')
        # base(접미 제외) 는 동일
        assert synth.trial_id.rsplit('__c-', 1)[0] == live.trial_id

    def test_synthetic_isolation_baselines(self) -> None:
        """B0/B1a/B1b/B2 전부 synthetic 분기에서 estimator external·publisher 동반."""
        for cfg in (b0_config(), b1a_config(), b1b_config(), b2_config()):
            trial = _make_trial(cfg, confidence_source='synthetic:c_constant_1')
            specs = compose_trial_node_specs(trial)
            names = [s.name for s in specs]
            assert NODE_NAME_CONF_PUBLISHER in names
            est = next(s for s in specs if s.name == NODE_NAME_ESTIMATOR)
            assert est.parameters['estimator_mode'] == 'external'
            # publisher +1 → base 6 → 7, count helper 정합
            assert len(specs) == expected_node_count(trial) == 7
