"""eval_calibration.schemas 단위 테스트 — host venv 실행."""

from __future__ import annotations

import pytest

from eval_calibration.schemas import (
    Backbone,
    CalibrationResult,
    SampleOutput,
    ScenarioSpec,
    SigmaLlmNat,
    TypedAction,
)


class TestBackbone:
    def test_two_calibration_backbones(self):
        # ADR-0025 D1.b amendment 8: GPT-4o + GPT-5.5 두 세대 양 끝점.
        assert Backbone.GPT_4O.value == 'gpt-4o-2024-05-13'
        assert Backbone.GPT_5_5.value == 'gpt-5.5'

    def test_no_claude_or_gemini(self):
        # ADR-0014 에 Claude / Gemini 없음. ADR-0025 amendment 8 정정 후 정합.
        names = {b.value for b in Backbone}
        assert not any('claude' in n.lower() for n in names)
        assert not any('gemini' in n.lower() for n in names)


class TestTypedAction:
    def test_valid_sigmas(self):
        for sigma in ('move_to', 'inspect', 'return_to_dock', 'emergency_land', 'ask_user'):
            action = TypedAction(sigma=sigma, theta={})
            assert action.sigma == sigma

    def test_invalid_sigma_rejected(self):
        with pytest.raises(ValueError, match='catalog'):
            TypedAction(sigma='fly_to_moon', theta={})


class TestScenarioSpec:
    def test_minimal_ambiguous_referent(self):
        spec = ScenarioSpec(
            scenario_id='S5',
            description='ambiguous',
            user_prompt='테이블 위 컵',
            known_objects=['cup_on_table', 'cup_on_tv_stand'],
        )
        assert spec.scenario_id == 'S5'
        assert spec.expected_action is None  # 모호 referent → None
        assert len(spec.known_objects) == 2

    def test_with_expected_action(self):
        action = TypedAction(sigma='ask_user', theta={'question': '어떤 컵?'})
        spec = ScenarioSpec(
            scenario_id='S5',
            description='x',
            user_prompt='x',
            expected_action=action,
        )
        assert spec.expected_action.sigma == 'ask_user'


class TestSigmaLlmNat:
    def test_four_measurements(self):
        # C1 amendment: no_call_rate 4번째 측정값 추가
        s = SigmaLlmNat(
            position_xyz_cm=5.2,
            target_swap_rate=0.04,
            unrelated_sigma_rate=0.0,
            no_call_rate=0.02,
        )
        assert s.position_xyz_cm == 5.2
        assert s.target_swap_rate == 0.04
        assert s.unrelated_sigma_rate == 0.0
        assert s.no_call_rate == 0.02

    def test_no_call_rate_default(self):
        # default = 0.0 (post-hoc 호환 — 기존 코드 무영향)
        s = SigmaLlmNat(position_xyz_cm=1.0, target_swap_rate=0.0, unrelated_sigma_rate=0.0)
        assert s.no_call_rate == 0.0


class TestCalibrationResult:
    def test_to_dict_roundtrip(self):
        sample = SampleOutput(
            prompt='테이블 위 컵',
            sigma=TypedAction(sigma='ask_user', theta={'question': 'q'}),
            expected_action=None,
            deltas={'position_xyz_cm': float('nan'), 'is_swap': False, 'is_unrelated': False},
        )
        result = CalibrationResult(
            backbone='gpt-4o-2024-05-13',
            scenario='S5',
            n_samples=1,
            timestamp='2026-05-27T00:00:00+00:00',
            sigma_llm_nat=SigmaLlmNat(
                position_xyz_cm=float('nan'),
                target_swap_rate=0.0,
                unrelated_sigma_rate=0.0,
            ),
            samples=[sample],
        )
        d = result.to_dict()
        assert d['backbone'] == 'gpt-4o-2024-05-13'
        assert d['scenario'] == 'S5'
        assert d['n_samples'] == 1
        assert len(d['samples']) == 1
        assert d['samples'][0]['prompt'] == '테이블 위 컵'
        assert d['samples'][0]['sigma']['sigma'] == 'ask_user'
