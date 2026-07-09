"""SigmaLlmNat 통계 계산 — sample N → 4 측정값 (C1·C3 amendment).

ADR-0025 D1.b 의 측정 + PR #82 review C1·C3 amendment:
  - position_xyz_cm = std of |θ_LLM.position - expected_position| (cm)
  - target_swap_rate = |{i : sample[i].is_swap}| / N
  - unrelated_sigma_rate = |{i : is_unrelated == True}| / |{i : is_unrelated is not None}|
                          ambiguous (expected=ask_user) 시나리오는 NaN
  - no_call_rate = |{i : sample[i].is_no_call}| / N (NATURAL 모드 fail-gracefully)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from eval_calibration.schemas import SampleOutput, SigmaLlmNat


def _std(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return float('nan')
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)  # sample std
    return math.sqrt(variance)


def _rate_of_truthy(samples: List[SampleOutput], key: str) -> float:
    """sample.deltas[key] 가 True 인 비율. None 은 분모 제외 (C3 amendment)."""
    valid = [s for s in samples if s.deltas.get(key) is not None]
    if not valid:
        return float('nan')
    return sum(1 for s in valid if s.deltas[key]) / len(valid)


def compute_sigma_llm_nat(samples: List[SampleOutput]) -> SigmaLlmNat:
    """샘플 list → 4 측정값.

    Args:
        samples: 한 (백본, 시나리오) 의 N 회 sample.

    Returns:
        SigmaLlmNat — position_xyz_cm / target_swap_rate /
        unrelated_sigma_rate (NaN 가능) / no_call_rate.
    """
    if not samples:
        return SigmaLlmNat(
            position_xyz_cm=float('nan'),
            target_swap_rate=float('nan'),
            unrelated_sigma_rate=float('nan'),
            no_call_rate=float('nan'),
        )

    position_deltas_cm = [
        s.deltas['position_xyz_cm']
        for s in samples
        if isinstance(s.deltas.get('position_xyz_cm'), (int, float))
        and not math.isnan(s.deltas['position_xyz_cm'])
    ]

    n = len(samples)
    swap_rate = sum(1 for s in samples if s.deltas.get('is_swap')) / n
    no_call_rate = sum(1 for s in samples if s.deltas.get('is_no_call')) / n
    # is_unrelated 가 None 인 sample (ambiguous 시나리오) 은 분모 제외 — C3 amendment.
    unrelated_rate = _rate_of_truthy(samples, 'is_unrelated')

    return SigmaLlmNat(
        position_xyz_cm=_std(position_deltas_cm) if position_deltas_cm else float('nan'),
        target_swap_rate=swap_rate,
        unrelated_sigma_rate=unrelated_rate,
        no_call_rate=no_call_rate,
    )


def derive_fault_variant_sigma(sigma_llm_nat_cm: float) -> dict:
    """ADR-0025 D1.b 의 fault_variant Gaussian σ mapping.

    Args:
        sigma_llm_nat_cm: position_xyz_cm (calibration 측정값).

    Returns:
        {variant_name → sigma_cm} dict.
        - position_noise_gauss_low = 1× sigma_llm_nat
        - position_noise_gauss_med = 5× sigma_llm_nat
        - position_noise_worst_geofence = boundary 강제 (별 값, calibration 무관)
    """
    if math.isnan(sigma_llm_nat_cm) or sigma_llm_nat_cm <= 0:
        return {
            'position_noise_gauss_low': float('nan'),
            'position_noise_gauss_med': float('nan'),
            'position_noise_worst_geofence': 'calibration 무관 — boundary 방향 강제',
        }
    return {
        'position_noise_gauss_low': sigma_llm_nat_cm,
        'position_noise_gauss_med': 5.0 * sigma_llm_nat_cm,
        'position_noise_worst_geofence': 'calibration 무관 — boundary 방향 강제',
    }


def position_delta_cm(
    actual_position: Optional[tuple],
    expected_position: Optional[tuple],
) -> float:
    """|actual - expected| L2 norm (cm).

    None 입력 → NaN.
    """
    if actual_position is None or expected_position is None:
        return float('nan')
    if len(actual_position) != 3 or len(expected_position) != 3:
        return float('nan')
    try:
        sq_sum = sum(
            (float(a) - float(e)) ** 2
            for a, e in zip(actual_position, expected_position)
        )
    except (TypeError, ValueError):
        return float('nan')  # LLM 응답 type 이상 (C8 robust)
    return math.sqrt(sq_sum) * 100.0  # m → cm


def compute_axis_sigma(
    moves: List[Tuple[float, float, float]],
) -> Dict[str, Dict[str, float]]:
    """move_to position 리스트 → 축별 (sigma_cm, mean_m).

    positional σ_LLM,nat 의 *축 분해* — context augmentation 효과를 x/y/z 별로
    본다 (ADR-0025 amend 12/13). expected_position 대비 L2 Δ (position_delta_cm)
    와 달리 referent 좌표를 몰라도 산출 가능 — 두 조건(provided/absent) 대조의
    분산 척도.

    Args:
        moves: 한 (probe, 조건) 의 move_to position 리스트 [(x, y, z), ...].

    Returns:
        {'x': {'sigma_cm', 'mean_m'}, 'y': {...}, 'z': {...}}.
        n<2 면 sigma_cm=NaN (표본 표준편차 정의 불가), mean_m 은 단일값.
        moves 비면 sigma_cm·mean_m 모두 NaN.
    """
    out: Dict[str, Dict[str, float]] = {}
    for i, axis in enumerate('xyz'):
        vals = [float(m[i]) for m in moves]
        sigma_cm = _std(vals) * 100.0 if len(vals) >= 2 else float('nan')
        mean_m = sum(vals) / len(vals) if vals else float('nan')
        out[axis] = {'sigma_cm': sigma_cm, 'mean_m': mean_m}
    return out
