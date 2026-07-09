"""fault_scenario.py 단위 테스트 — YAML loader + 4 channel polymorphic dispatch.

rclpy 의존성 *없이* host venv 측 통과. injector_node (B5 #5b) 측 rclpy timer/
subscriber 등 wrapper logic 은 colcon test 측 분리.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_faults.fault_scenario import (
    FaultChannel,
    FaultScenario,
    build_fault_context,
    load_fault_scenario,
)
from eval_faults.schemas import (
    AdversarialVariant,
    AttributeMismatchContext,
    AttributeMismatchVariant,
    CognitiveLapseContext,
    CognitiveLapseVariant,
    FaultContext,
    FaultVariant,
)


SCENARIO_DIR = Path(__file__).parent.parent / 'scenarios'
# positional hallucination YAML 은 격자 glob(scenarios/*.yaml)에서 제외하고
# Track B(ADR-0028) 전용으로 비-glob 하위 디렉토리에 보존 (ADR-0025 amendment,
# 2026-06-14). 격자 default 5종에는 referential 대표(target_swap_dangerous) 사용.
TRACK_B_DIR = SCENARIO_DIR / 'track_b'


# ----------------------------------------------------------- FaultChannel enum


class TestFaultChannelEnum:
    def test_five_channels_locked(self):
        """ADR-0018 D2 + ADR-0025 D1 — 5 fault_class enum."""
        names = {c.value for c in FaultChannel}
        assert names == {
            'none', 'hallucination', 'adversarial',
            'cognitive_lapse', 'attribute_mismatch',
        }


# ----------------------------------------------------------- FaultScenario dataclass


class TestFaultScenarioDataclass:
    def test_valid_none_scenario(self):
        s = FaultScenario(
            name='none_baseline',
            description='baseline',
            channel=FaultChannel.NONE,
            variant=None,
            context_kwargs={},
        )
        assert s.channel == FaultChannel.NONE

    def test_valid_hallucination_scenario(self):
        s = FaultScenario(
            name='h_low',
            description='',
            channel=FaultChannel.HALLUCINATION,
            variant='position_noise_gauss_low',
            context_kwargs={'user_position': [0.0, 0.0, 1.0]},
        )
        assert s.variant == 'position_noise_gauss_low'

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match='name'):
            FaultScenario(
                name='', description='', channel=FaultChannel.NONE, variant=None,
            )

    def test_none_channel_with_variant_rejected(self):
        with pytest.raises(ValueError, match='channel=none'):
            FaultScenario(
                name='bad', description='', channel=FaultChannel.NONE,
                variant='something',
            )

    def test_none_channel_with_context_rejected(self):
        with pytest.raises(ValueError, match='channel=none'):
            FaultScenario(
                name='bad', description='', channel=FaultChannel.NONE,
                variant=None,
                context_kwargs={'key': 'val'},
            )

    def test_non_none_channel_missing_variant_rejected(self):
        with pytest.raises(ValueError, match='variant 필수'):
            FaultScenario(
                name='bad', description='', channel=FaultChannel.HALLUCINATION,
                variant=None,
            )

    def test_non_none_channel_empty_variant_rejected(self):
        with pytest.raises(ValueError, match='variant 필수'):
            FaultScenario(
                name='bad', description='', channel=FaultChannel.HALLUCINATION,
                variant='   ',
            )


# ----------------------------------------------------------- YAML loader


class TestLoadFaultScenario:
    def test_loads_none_baseline(self):
        scenario = load_fault_scenario(SCENARIO_DIR / 'none_baseline.yaml')
        assert scenario.name == 'none_baseline'
        assert scenario.channel == FaultChannel.NONE
        assert scenario.variant is None
        assert scenario.context_kwargs == {}
        assert scenario.seed == 42

    def test_loads_hallucination_target_swap_dangerous(self):
        """격자 hallucination 대표 — referential 자연 환각 (ADR-0025 amendment)."""
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'hallucination_target_swap_dangerous.yaml',
        )
        assert scenario.channel == FaultChannel.HALLUCINATION
        assert scenario.variant == 'target_swap_dangerous'
        assert 'known_objects' in scenario.context_kwargs
        assert scenario.context_kwargs['r_min'] == 0.7

    def test_loads_hallucination_position_worst_user_direct_track_b(self):
        """사용자 지향 적대 setpoint (스킬 무관) — Track B, 격자 glob 제외 (amendment 20)."""
        scenario = load_fault_scenario(
            TRACK_B_DIR / 'hallucination_position_worst_user_direct.yaml',
        )
        assert scenario.channel == FaultChannel.HALLUCINATION
        assert scenario.variant == 'position_worst_user_direct'
        assert 'known_objects' in scenario.context_kwargs
        assert scenario.context_kwargs['r_min'] == 0.9

    def test_loads_adversarial_geofence(self):
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'adversarial_geofence.yaml',
        )
        assert scenario.channel == FaultChannel.ADVERSARIAL
        assert scenario.variant == 'prompt_injection_geofence'

    def test_loads_cognitive_lapse_self_correction(self):
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'cognitive_lapse_self_correction.yaml',
        )
        assert scenario.channel == FaultChannel.COGNITIVE_LAPSE
        assert scenario.variant == 'E1_self_correction'
        assert scenario.context_kwargs['initial_target_name_kr'] == '거실 탁자 위 책'

    def test_loads_attribute_mismatch_label_low(self):
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'attribute_mismatch_label_low.yaml',
        )
        assert scenario.channel == FaultChannel.ATTRIBUTE_MISMATCH
        assert scenario.variant == 'attribute_mismatch_label_low'
        assert 'cup' in scenario.context_kwargs['vocabulary']

    def test_missing_file_raises_filenotfound(self):
        with pytest.raises(FileNotFoundError):
            load_fault_scenario(SCENARIO_DIR / 'nonexistent.yaml')

    def test_yaml_root_not_dict_rejected(self, tmp_path):
        bad = tmp_path / 'bad.yaml'
        bad.write_text('- list_root\n', encoding='utf-8')
        with pytest.raises(ValueError, match='YAML root'):
            load_fault_scenario(bad)

    def test_missing_required_key_rejected(self, tmp_path):
        bad = tmp_path / 'bad.yaml'
        bad.write_text('description: no name\nchannel: none\n', encoding='utf-8')
        with pytest.raises(KeyError, match='name'):
            load_fault_scenario(bad)

    def test_unknown_channel_rejected(self, tmp_path):
        bad = tmp_path / 'bad.yaml'
        bad.write_text('name: x\nchannel: not_a_channel\n', encoding='utf-8')
        with pytest.raises(ValueError, match='unknown FaultChannel'):
            load_fault_scenario(bad)

    def test_unknown_yaml_key_rejected(self, tmp_path):
        """PR #104 review B-5 — typo 또는 schema 외 키 silent ignore 회피.

        예: ``seeed: 42`` typo → silent default 42 사용 회피.
        """
        bad = tmp_path / 'bad.yaml'
        bad.write_text(
            'name: x\nchannel: none\nseeed: 99\n',  # typo
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='unknown YAML keys'):
            load_fault_scenario(bad)

    def test_multiple_unknown_keys_listed(self, tmp_path):
        """unknown 키 *복수* 측 모두 error message 측 listed."""
        bad = tmp_path / 'bad.yaml'
        bad.write_text(
            'name: x\nchannel: none\npriority: high\nlevel: 5\n',
            encoding='utf-8',
        )
        with pytest.raises(ValueError, match='unknown YAML keys') as exc_info:
            load_fault_scenario(bad)
        msg = str(exc_info.value)
        assert 'priority' in msg
        assert 'level' in msg

    def test_seed_loaded_from_yaml(self, tmp_path):
        """PR #104 review B-13 — seed 측 default 42 외 값 정합 검증."""
        path = tmp_path / 'with_seed.yaml'
        path.write_text(
            'name: x\nchannel: none\nseed: 123\n', encoding='utf-8',
        )
        scenario = load_fault_scenario(path)
        assert scenario.seed == 123


# ----------------------------------------------------------- build_fault_context


class TestBuildFaultContextNone:
    def test_none_returns_double_none(self):
        scenario = load_fault_scenario(SCENARIO_DIR / 'none_baseline.yaml')
        ctx, variant = build_fault_context(scenario)
        assert ctx is None
        assert variant is None


class TestBuildFaultContextHallucination:
    def test_hallucination_referential_builds_fault_context(self):
        """격자 hallucination 대표 — referential variant build."""
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'hallucination_target_swap_dangerous.yaml',
        )
        ctx, variant = build_fault_context(scenario)
        assert isinstance(ctx, FaultContext)
        assert variant == FaultVariant.TARGET_SWAP_DANGEROUS
        # dangerous variant 측 swap 후보 ≥1 (known_objects 비어 있지 않음).
        assert len(ctx.known_objects) >= 1

    def test_hallucination_positional_builds_fault_context_track_b(self):
        scenario = load_fault_scenario(
            TRACK_B_DIR / 'hallucination_position_worst_user_direct.yaml',
        )
        ctx, variant = build_fault_context(scenario)
        assert isinstance(ctx, FaultContext)
        assert variant == FaultVariant.POSITION_WORST_USER_DIRECT

    def test_yaml_list_to_tuple_conversion(self):
        scenario = load_fault_scenario(
            TRACK_B_DIR / 'hallucination_position_worst_user_direct.yaml',
        )
        ctx, _ = build_fault_context(scenario)
        # user_position 측 list → tuple 변환 (거실 world placeholder)
        assert isinstance(ctx.user_position, tuple)
        assert ctx.user_position == (0.0, 1.5, 1.1)
        # geofence 측 tuple
        assert isinstance(ctx.geofence, tuple)
        assert len(ctx.geofence) == 6
        # known_objects 측 dict[str, tuple]
        for pos in ctx.known_objects.values():
            assert isinstance(pos, tuple)
            assert len(pos) == 3

    def test_unknown_hallucination_variant_rejected(self):
        scenario = FaultScenario(
            name='bad', description='', channel=FaultChannel.HALLUCINATION,
            variant='not_a_variant',
            context_kwargs={
                'known_objects': {'a': [0.0, 0.0, 0.0]},
                'user_position': [0.0, 0.0, 1.0],
            },
        )
        with pytest.raises(ValueError):
            build_fault_context(scenario)

    def test_minimal_kwargs_builds_with_defaults(self):
        """PR #104 review B-16 — HALLUCINATION 측 context_kwargs={} 측
        모든 필드 default → build 성공.

        *meaningful trial 아님* (known_objects={} 측 referential variant 측 swap
        후보 부재 → hallucination.py no-op fallback) — docstring 측 비대칭 명시
        검증.
        """
        scenario = FaultScenario(
            name='min', description='',
            channel=FaultChannel.HALLUCINATION,
            variant='position_noise_gauss_low',
            context_kwargs={},  # 모든 필드 default
        )
        ctx, variant = build_fault_context(scenario)
        # default 값 적용
        assert ctx.known_objects == {}
        assert ctx.user_position == (0.0, 0.0, 0.0)
        assert ctx.r_min == 0.7
        assert variant.value == 'position_noise_gauss_low'


class TestBuildFaultContextAdversarial:
    def test_adversarial_builds_fault_context(self):
        scenario = load_fault_scenario(SCENARIO_DIR / 'adversarial_geofence.yaml')
        ctx, variant = build_fault_context(scenario)
        assert isinstance(ctx, FaultContext)
        assert variant == AdversarialVariant.PROMPT_INJECTION_GEOFENCE


class TestBuildFaultContextCognitiveLapse:
    def test_cognitive_lapse_builds_context(self):
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'cognitive_lapse_self_correction.yaml',
        )
        ctx, variant = build_fault_context(scenario)
        assert isinstance(ctx, CognitiveLapseContext)
        assert variant == CognitiveLapseVariant.E1_SELF_CORRECTION
        assert ctx.initial_target_name_kr == '거실 탁자 위 책'

    def test_range_tuples_converted(self):
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'cognitive_lapse_self_correction.yaml',
        )
        ctx, _ = build_fault_context(scenario)
        assert isinstance(ctx.trigger_time_range_s, tuple)
        assert ctx.trigger_time_range_s == (3.0, 25.0)
        assert isinstance(ctx.silence_threshold_range_s, tuple)
        assert ctx.silence_threshold_range_s == (8.0, 15.0)

    def test_missing_required_field_raises(self):
        scenario = FaultScenario(
            name='bad', description='', channel=FaultChannel.COGNITIVE_LAPSE,
            variant='E1_self_correction',
            context_kwargs={'initial_target_id': 'a'},  # 나머지 필수 필드 부재
        )
        with pytest.raises(KeyError):
            build_fault_context(scenario)


class TestBuildFaultContextAttributeMismatch:
    def test_attribute_mismatch_builds_context(self):
        scenario = load_fault_scenario(
            SCENARIO_DIR / 'attribute_mismatch_label_low.yaml',
        )
        ctx, variant = build_fault_context(scenario)
        assert isinstance(ctx, AttributeMismatchContext)
        assert variant == AttributeMismatchVariant.LABEL_LOW
        assert ctx.vocabulary == [
            'cup', 'book', 'mug', 'chair', 'table', 'person', 'cat',
        ]
        assert ctx.sigma_ovd_label_swap_rate == 0.05
        assert ctx.dangerous_label == 'person'

    def test_default_values_applied(self):
        scenario = FaultScenario(
            name='min', description='', channel=FaultChannel.ATTRIBUTE_MISMATCH,
            variant='attribute_mismatch_label_low',
            context_kwargs={'vocabulary': ['cup', 'book']},  # 다른 필드 default
        )
        ctx, _ = build_fault_context(scenario)
        assert ctx.sigma_ovd_label_swap_rate == 0.05  # default
        assert ctx.sigma_ovd_bbox_px == 10.0  # default
        assert ctx.dangerous_label == 'person'  # default


# ----------------------------------------------------------- 5 scenarios 측 통합


class TestAllScenariosLoad:
    """scenarios/ 의 격자 default 5 YAML 모두 load + build 측 통과.

    hallucination 대표 = referential(target_swap_dangerous) — ADR-0025 amendment
    (2026-06-14). positional 은 Track B(track_b/)로 분리되어 격자 glob 제외.
    """

    @pytest.mark.parametrize('yaml_name', [
        'none_baseline.yaml',
        'hallucination_target_swap_dangerous.yaml',
        'adversarial_geofence.yaml',
        'cognitive_lapse_self_correction.yaml',
        'attribute_mismatch_label_low.yaml',
    ])
    def test_load_and_build(self, yaml_name):
        scenario = load_fault_scenario(SCENARIO_DIR / yaml_name)
        ctx, variant = build_fault_context(scenario)
        # NONE 측 (None, None), 그 외 측 (context, variant) 모두 non-None
        if scenario.channel == FaultChannel.NONE:
            assert ctx is None and variant is None
        else:
            assert ctx is not None and variant is not None
