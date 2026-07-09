"""analyze.py 단위 테스트 — sigma_llm_nat 계산."""

from __future__ import annotations

import math

import pytest

from eval_calibration.analyze import (
    compute_axis_sigma,
    compute_sigma_llm_nat,
    derive_fault_variant_sigma,
    position_delta_cm,
)
from eval_calibration.schemas import SampleOutput, TypedAction


def _mk_sample(
    position_cm: float,
    is_swap: bool,
    is_unrelated,  # bool 또는 None (C3 amendment — ambiguous 시나리오 NaN)
    is_no_call: bool = False,
) -> SampleOutput:
    return SampleOutput(
        prompt='x',
        sigma=TypedAction(sigma='move_to', theta={}),
        expected_action=None,
        deltas={
            'position_xyz_cm': position_cm,
            'is_swap': is_swap,
            'is_unrelated': is_unrelated,
            'is_no_call': is_no_call,
        },
    )


class TestComputeSigmaLlmNat:
    def test_empty_samples_returns_nan(self):
        result = compute_sigma_llm_nat([])
        assert math.isnan(result.position_xyz_cm)
        assert math.isnan(result.target_swap_rate)
        assert math.isnan(result.unrelated_sigma_rate)
        assert math.isnan(result.no_call_rate)

    def test_ambiguous_scenario_unrelated_nan(self):
        # C3 amendment: is_unrelated=None 인 sample 만 있으면 unrelated_sigma_rate NaN
        samples = [
            _mk_sample(5.0, False, None),
            _mk_sample(5.0, False, None),
        ]
        result = compute_sigma_llm_nat(samples)
        assert math.isnan(result.unrelated_sigma_rate)

    def test_no_call_rate(self):
        # C1 amendment: NATURAL 모드의 자연 fail-gracefully 측정
        samples = [
            _mk_sample(float('nan'), False, None, is_no_call=True),
            _mk_sample(5.0, False, False, is_no_call=False),
            _mk_sample(float('nan'), False, None, is_no_call=True),
        ]
        result = compute_sigma_llm_nat(samples)
        assert result.no_call_rate == pytest.approx(2 / 3)

    def test_mixed_unrelated_none_and_bool(self):
        # 일부 sample 만 expected_action 있어 is_unrelated 측정 가능
        samples = [
            _mk_sample(5.0, False, None),     # ambiguous, 분모 제외
            _mk_sample(5.0, False, True),     # 1
            _mk_sample(5.0, False, False),    # 0
            _mk_sample(5.0, False, False),    # 0
        ]
        result = compute_sigma_llm_nat(samples)
        # 분모 = 3 (None 제외), 분자 = 1 → 1/3
        assert result.unrelated_sigma_rate == pytest.approx(1 / 3)

    def test_position_std(self):
        # std of [5, 7, 9] = 2.0
        samples = [_mk_sample(5.0, False, False), _mk_sample(7.0, False, False), _mk_sample(9.0, False, False)]
        result = compute_sigma_llm_nat(samples)
        assert result.position_xyz_cm == pytest.approx(2.0, abs=1e-6)
        assert result.target_swap_rate == 0.0
        assert result.unrelated_sigma_rate == 0.0

    def test_target_swap_rate(self):
        # 3 중 1 swap → 1/3.
        samples = [
            _mk_sample(5.0, True, False),
            _mk_sample(5.0, False, False),
            _mk_sample(5.0, False, False),
        ]
        assert compute_sigma_llm_nat(samples).target_swap_rate == pytest.approx(1 / 3)

    def test_unrelated_rate(self):
        # 4 중 2 unrelated → 0.5.
        samples = [
            _mk_sample(5.0, False, True),
            _mk_sample(5.0, False, True),
            _mk_sample(5.0, False, False),
            _mk_sample(5.0, False, False),
        ]
        assert compute_sigma_llm_nat(samples).unrelated_sigma_rate == 0.5

    def test_nan_positions_filtered_out(self):
        # NaN position 은 std 계산에서 제외 — swap/unrelated 분모는 N 유지.
        samples = [
            _mk_sample(float('nan'), False, False),
            _mk_sample(5.0, False, False),
            _mk_sample(7.0, False, False),
        ]
        result = compute_sigma_llm_nat(samples)
        assert result.position_xyz_cm == pytest.approx(math.sqrt(2.0), abs=1e-6)
        # swap_rate 분모는 N=3 (NaN 제외 X)
        assert result.target_swap_rate == 0.0

    def test_single_sample_position_nan(self):
        # std 계산은 N >= 2 — 1 sample 이면 NaN.
        samples = [_mk_sample(5.0, False, False)]
        result = compute_sigma_llm_nat(samples)
        assert math.isnan(result.position_xyz_cm)


class TestDeriveFaultVariantSigma:
    def test_normal_mapping(self):
        out = derive_fault_variant_sigma(5.0)
        assert out['position_noise_gauss_low'] == 5.0
        assert out['position_noise_gauss_med'] == 25.0
        assert 'calibration 무관' in str(out['position_noise_worst_geofence'])

    def test_nan_input_yields_nan_mapping(self):
        out = derive_fault_variant_sigma(float('nan'))
        assert math.isnan(out['position_noise_gauss_low'])
        assert math.isnan(out['position_noise_gauss_med'])

    def test_zero_or_negative_input_yields_nan(self):
        for bad in (0.0, -1.0):
            out = derive_fault_variant_sigma(bad)
            assert math.isnan(out['position_noise_gauss_low'])


class TestPositionDeltaCm:
    def test_basic_l2_norm(self):
        # |(1,0,0) - (0,0,0)| = 1 m = 100 cm
        assert position_delta_cm((1.0, 0.0, 0.0), (0.0, 0.0, 0.0)) == pytest.approx(100.0)

    def test_three_d(self):
        # |(1,2,2) - (0,0,0)| = 3 m = 300 cm
        assert position_delta_cm((1.0, 2.0, 2.0), (0.0, 0.0, 0.0)) == pytest.approx(300.0)

    def test_none_inputs_yield_nan(self):
        assert math.isnan(position_delta_cm(None, (0.0, 0.0, 0.0)))
        assert math.isnan(position_delta_cm((0.0, 0.0, 0.0), None))
        assert math.isnan(position_delta_cm(None, None))

    def test_wrong_dimension_yields_nan(self):
        assert math.isnan(position_delta_cm((1.0, 0.0), (0.0, 0.0)))
        assert math.isnan(position_delta_cm((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))

    def test_same_position_zero(self):
        assert position_delta_cm((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == 0.0


class TestComputeAxisSigma:
    def test_zero_variance_fixed_position(self):
        moves = [(2.0, -1.0, 0.8)] * 4
        axis = compute_axis_sigma(moves)
        assert axis['x']['sigma_cm'] == pytest.approx(0.0)
        assert axis['y']['sigma_cm'] == pytest.approx(0.0)
        assert axis['z']['sigma_cm'] == pytest.approx(0.0)
        assert axis['x']['mean_m'] == pytest.approx(2.0)
        assert axis['y']['mean_m'] == pytest.approx(-1.0)

    def test_nonzero_variance(self):
        # x = [1.0, 2.0] → sample std = 0.7071 m = 70.71 cm
        moves = [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        axis = compute_axis_sigma(moves)
        assert axis['x']['sigma_cm'] == pytest.approx(70.71, abs=0.1)
        assert axis['x']['mean_m'] == pytest.approx(1.5)
        assert axis['y']['sigma_cm'] == pytest.approx(0.0)

    def test_single_move_sigma_nan_mean_defined(self):
        axis = compute_axis_sigma([(2.0, -1.0, 0.8)])
        assert math.isnan(axis['x']['sigma_cm'])
        assert axis['x']['mean_m'] == pytest.approx(2.0)

    def test_empty_moves_all_nan(self):
        axis = compute_axis_sigma([])
        for ax in 'xyz':
            assert math.isnan(axis[ax]['sigma_cm'])
            assert math.isnan(axis[ax]['mean_m'])
