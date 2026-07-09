"""eval_runner.schemas 단위 테스트.

ADR-0025 D3 격자 측 단일 cell (TrialSpec) 측 invariant 검증.
"""

from __future__ import annotations

import pytest

from eval_baselines.b0_passthrough import b0_config
from eval_baselines.b2_modulated import b2_config
from eval_faults.fault_scenario import FaultChannel, FaultScenario

from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec


def _make_none_fault() -> FaultScenario:
    return FaultScenario(
        name='test_none',
        description='test',
        channel=FaultChannel.NONE,
        variant=None,
        context_kwargs={},
        seed=42,
    )


def _make_hallucination_fault() -> FaultScenario:
    return FaultScenario(
        name='test_hall',
        description='test',
        channel=FaultChannel.HALLUCINATION,
        variant='position_gauss_low',
        context_kwargs={'r_min': 0.7},
        seed=42,
    )


class TestValidScenarioIds:
    def test_indoor_two(self) -> None:
        """ADR-0006 + ADR-0026 D6 + ADR-0039 D2 정합 — 거실 S5/S6 (S7 폐기·S8 paper-2)."""
        assert VALID_SCENARIO_IDS == ('S5', 'S6')

    def test_s3_excluded(self) -> None:
        """S3 (지붕, 실외) 측 paper §C 시뮬 범위 밖."""
        assert 'S3' not in VALID_SCENARIO_IDS


class TestTrialSpecValid:
    def test_constructs(self) -> None:
        spec = TrialSpec(
            scenario_id='S5',
            baseline_config=b0_config(),
            fault_scenario=_make_none_fault(),
            episode_id=0,
            seed=12345,
        )
        assert spec.scenario_id == 'S5'
        assert spec.episode_id == 0
        assert spec.seed == 12345

    def test_frozen(self) -> None:
        spec = TrialSpec(
            scenario_id='S5',
            baseline_config=b0_config(),
            fault_scenario=_make_none_fault(),
            episode_id=0,
            seed=0,
        )
        with pytest.raises((AttributeError, Exception)):
            spec.seed = 999  # type: ignore[misc]

    def test_uint32_max_boundary(self) -> None:
        """seed 측 2**32 - 1 측 허용 (upper bound inclusive)."""
        spec = TrialSpec(
            scenario_id='S5',
            baseline_config=b0_config(),
            fault_scenario=_make_none_fault(),
            episode_id=0,
            seed=2**32 - 1,
        )
        assert spec.seed == 2**32 - 1


class TestTrialSpecInvariant:
    def test_invalid_scenario_id(self) -> None:
        with pytest.raises(ValueError, match='scenario_id'):
            TrialSpec(
                scenario_id='S3',  # 실외, 제외 대상
                baseline_config=b0_config(),
                fault_scenario=_make_none_fault(),
                episode_id=0,
                seed=0,
            )

    def test_negative_episode_id(self) -> None:
        with pytest.raises(ValueError, match='episode_id'):
            TrialSpec(
                scenario_id='S5',
                baseline_config=b0_config(),
                fault_scenario=_make_none_fault(),
                episode_id=-1,
                seed=0,
            )

    def test_episode_id_type(self) -> None:
        with pytest.raises(TypeError, match='episode_id'):
            TrialSpec(
                scenario_id='S5',
                baseline_config=b0_config(),
                fault_scenario=_make_none_fault(),
                episode_id='0',  # type: ignore[arg-type]
                seed=0,
            )

    def test_episode_id_bool_rejected(self) -> None:
        """bool 측 int subclass 이나 episode_id 측 명시적 거부 (실수 회피)."""
        with pytest.raises(TypeError, match='episode_id'):
            TrialSpec(
                scenario_id='S5',
                baseline_config=b0_config(),
                fault_scenario=_make_none_fault(),
                episode_id=True,  # type: ignore[arg-type]
                seed=0,
            )

    def test_negative_seed(self) -> None:
        with pytest.raises(ValueError, match='seed'):
            TrialSpec(
                scenario_id='S5',
                baseline_config=b0_config(),
                fault_scenario=_make_none_fault(),
                episode_id=0,
                seed=-1,
            )

    def test_seed_overflow(self) -> None:
        """seed 측 2**32 측 거부 (uint32 범위 위)."""
        with pytest.raises(ValueError, match='seed'):
            TrialSpec(
                scenario_id='S5',
                baseline_config=b0_config(),
                fault_scenario=_make_none_fault(),
                episode_id=0,
                seed=2**32,
            )


class TestTrialId:
    def test_format_none_fault(self) -> None:
        spec = TrialSpec(
            scenario_id='S6',
            baseline_config=b0_config(),
            fault_scenario=_make_none_fault(),
            episode_id=9,
            seed=0,
        )
        assert spec.trial_id == 'S6__b0__none__none__ep09'

    def test_format_hallucination_fault(self) -> None:
        spec = TrialSpec(
            scenario_id='S5',
            baseline_config=b2_config(),
            fault_scenario=_make_hallucination_fault(),
            episode_id=0,
            seed=0,
        )
        assert spec.trial_id == 'S5__b2__hallucination__position_gauss_low__ep00'

    def test_episode_id_zero_padded(self) -> None:
        """episode_id 측 02d zero-pad — lexicographic sort 정합."""
        spec0 = TrialSpec(
            scenario_id='S5',
            baseline_config=b0_config(),
            fault_scenario=_make_none_fault(),
            episode_id=0,
            seed=0,
        )
        spec9 = TrialSpec(
            scenario_id='S5',
            baseline_config=b0_config(),
            fault_scenario=_make_none_fault(),
            episode_id=9,
            seed=0,
        )
        assert spec0.trial_id.endswith('ep00')
        assert spec9.trial_id.endswith('ep09')
