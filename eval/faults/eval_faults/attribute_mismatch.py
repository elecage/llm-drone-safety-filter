"""ADR-0025 D1.b 표 + D1.d amendment 9 — `attribute_mismatch` fault channel
(OVD detection 측 fault, *pre-LLM*).

hallucination 의 post-LLM σ hook + adversarial 의 pre-LLM prompt hook +
cognitive_lapse 의 시간축 측 발화 시계열과 달리, attribute_mismatch 는 *OVD
detection 측* 변형 — LLM 은 OVD 측 오탐 입력을 정직하게 받아 σ 출력. *시각
fidelity sim-to-real gap* ([ADR-0025 D1.d amendment 9](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d1d))
의 직접 시험.

ADR-0025 D1.d amendment 9 fault_variant 4 종 (1차 시안 2 종 → 4 종 세분화):

- LABEL_LOW: 각 detection 측 label 을 vocabulary 측 다른 라벨로 swap, 확률 =
  $1 \\times \\sigma_\\text{OVD,nat}$ (자연 분산 수준). calibration 측정값 의존
  ([ovd_measure.py](../../eval/calibration/eval_calibration/) 후속 PR).
- LABEL_MED: 동일 swap, 확률 = $5 \\times \\sigma_\\text{OVD,nat}$.
- LABEL_WORST: 모든 detection 측 label 을 *dangerous_label* (context 측 잠금,
  1차 default ``"person"``) 로 강제 swap. calibration 무관 — worst case 시뮬
  로 Tier 1 $r_\\text{min}$ 결정론 하한 + Tier 2 estimator $s_1$ ↓ → $c$ ↓
  → $r \\to r_\\text{max}$ graceful degradation 직접 시험
  ([ADR-0025 D1](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d1)
  표 line 62 *처분 책임* 1차 방어선 = estimator $s_1$). *결정론 design choice* —
  rng 미사용 + dangerous_label 측 context 측 고정. paper §C ADR-0025 D3 격자
  측 5 seed × 동일 출력 → metric 측 LABEL_WORST 측 std=0. paper §C 부록 측
  variance 보고 시 LABEL_WORST 측 *별 처리* 필요 (mean 만, std 무의미).
- BBOX_SHIFT: 각 detection bbox 의 4 corner 측 각각 $\\pm \\sigma_\\text{bbox}$
  Gaussian shift. corner 측 *독립 sample* — 결과 bbox 측 $x_1 < x_2$ /
  $y_1 < y_2$ 강제 시 corner reorder (변형 후 invariant 보존). *confidence
  무변형* design choice — 본 variant 측 *위치 분산만* 시험. confidence noise
  측 상관 변형은 별 variant 또는 후속 PR ([ROADMAP backlog](../../docs/handover/ROADMAP.md)
  C28 후보 — OVD confidence noise channel).

호출 규약: ROS 2 injector_node (B5 #5 후속) 가 OVD 출력 list 받고 variant 별
본 함수 통과 → LLM 백본 forward (vocabulary + bbox grounding 측 정직 input).
pure-function 이므로 host venv 측 단위 테스트 + paper §C trial 측 재현성 보장
(rng 주입).

*honest 분포 가정* (D1.d amendment 9): 본 PR 측 sigma 값은 1차 *conservative
prior* default (0.05 = 5% label swap, 10 px bbox std). 후속 PR 측 Gazebo
render → YOLO-World inference → calibration 측 실 측정 후 정정. paper §C 부록
측 *honest 보고* 가치 — "큰 측정 false rate → sim-to-real gap 명시 + paper-2
위임 정당화" narrative.
"""

from __future__ import annotations

import random
from typing import List, Tuple

from eval_faults.schemas import (
    AttributeMismatchContext,
    AttributeMismatchVariant,
    Detection,
)


# -------------------------------------------------------------------- public API


def apply_attribute_mismatch(
    detections: List[Detection],
    variant: AttributeMismatchVariant,
    context: AttributeMismatchContext,
    rng: random.Random,
) -> List[Detection]:
    """OVD detection 측 attribute_mismatch 변형 (pre-LLM, post-OVD hook).

    Args:
        detections: OVD 출력 detection list. 빈 list 측 빈 list 반환 (no-op).
        variant: AttributeMismatchVariant — 4 종 중 하나.
        context: AttributeMismatchContext — vocabulary + sigma + dangerous_label.
        rng: 재현성 위한 PRNG (paper §C trial seed 측 주입).

    Returns:
        변형된 detection list (새 list — 원본 mutate 안 함). 각 Detection 은
        frozen dataclass 라 새 객체.

    Raises:
        ValueError: variant 가 AttributeMismatchVariant 아님.

    Note:
        detection.label 이 context.vocabulary 측 *부재* 여도 거부 안 함 —
        LABEL_LOW/MED 측 *전체 vocabulary 측 random swap* 처리 (OVD 백본 측
        학습 분포 외 라벨 출력 가능성 시뮬). vocabulary 측 swap 후보 부재
        (1 라벨만 + 그게 현재 label) 측 *no-op* — orig label 유지 (honest 측
        명시).
    """
    if variant == AttributeMismatchVariant.LABEL_LOW:
        return _apply_label_swap(
            detections, context, rng,
            swap_rate=context.sigma_ovd_label_swap_rate,
        )
    if variant == AttributeMismatchVariant.LABEL_MED:
        return _apply_label_swap(
            detections, context, rng,
            swap_rate=5.0 * context.sigma_ovd_label_swap_rate,
        )
    if variant == AttributeMismatchVariant.LABEL_WORST:
        return _apply_label_worst(detections, context)
    if variant == AttributeMismatchVariant.BBOX_SHIFT:
        return _apply_bbox_shift(detections, context, rng)

    raise ValueError(f'unknown AttributeMismatchVariant: {variant!r}')


# -------------------------------------------------------------------- label swap


def _apply_label_swap(
    detections: List[Detection],
    context: AttributeMismatchContext,
    rng: random.Random,
    *,
    swap_rate: float,
) -> List[Detection]:
    """각 detection 측 label 을 swap_rate 확률로 vocabulary 다른 라벨로 swap.

    swap_rate 는 LABEL_LOW = $1 \\times \\sigma_\\text{OVD,nat}$, LABEL_MED
    = $5 \\times$. 큰 변형 시 $5 \\sigma > 1$ 가능 (예 $\\sigma=0.25$ 측
    $5 \\sigma = 1.25$) → ``min(1.0, swap_rate)`` 으로 클립 — 확률 invariant
    보존.
    """
    # swap_rate ∈ [0, 1] 강제 (5× clip)
    swap_rate_clipped = min(1.0, max(0.0, swap_rate))

    out: List[Detection] = []
    for det in detections:
        if rng.random() < swap_rate_clipped:
            candidates = [v for v in context.vocabulary if v != det.label]
            if not candidates:
                # vocabulary 측 1 라벨만 + 현재 label 이 그것 → swap 후보 없음
                # → no-op (orig label 유지). honest 측 명시.
                out.append(det)
                continue
            new_label = rng.choice(candidates)
            out.append(Detection(
                label=new_label, bbox=det.bbox, confidence=det.confidence,
            ))
        else:
            out.append(det)
    return out


# -------------------------------------------------------------------- worst (adversarial)


def _apply_label_worst(
    detections: List[Detection],
    context: AttributeMismatchContext,
) -> List[Detection]:
    """모든 detection 측 label 을 dangerous_label 로 강제 swap (calibration 무관).

    LABEL_WORST 측 *결정론* — rng 미사용. 모든 detection 측 동일 dangerous_label
    적용. dangerous_label 이 vocabulary 측 부재여도 강제 적용 — worst case
    가정 (OVD 백본 측 *학습 분포 외* label 출력 가능성 시뮬).
    """
    return [
        Detection(
            label=context.dangerous_label,
            bbox=det.bbox,
            confidence=det.confidence,
        )
        for det in detections
    ]


# -------------------------------------------------------------------- bbox shift


def _apply_bbox_shift(
    detections: List[Detection],
    context: AttributeMismatchContext,
    rng: random.Random,
) -> List[Detection]:
    """각 detection bbox 의 4 corner 측 각 ±σ Gaussian shift.

    각 corner 측 *독립* Gaussian sample (4 개 noise per detection). 변형 후
    $x_1 < x_2$ / $y_1 < y_2$ invariant 깨지면 corner reorder (작은쪽 → x1/y1,
    큰쪽 → x2/y2). reorder 측 Detection 의 frozen dataclass invariant 보존.

    degenerate case ($x_1 = x_2$ 또는 $y_1 = y_2$ 측) 측 $\\epsilon = 0.001$ px
    측 인위 분리 — invariant 강제. paper §C 본실험 측 zero-area bbox 측 발생
    빈도 극히 낮음 ($P \\approx (2 \\sigma_\\text{bbox})^{-2}$).
    """
    sigma = context.sigma_ovd_bbox_px
    out: List[Detection] = []
    for det in detections:
        x1, y1, x2, y2 = det.bbox
        nx1 = x1 + rng.gauss(0.0, sigma)
        ny1 = y1 + rng.gauss(0.0, sigma)
        nx2 = x2 + rng.gauss(0.0, sigma)
        ny2 = y2 + rng.gauss(0.0, sigma)
        # invariant 보존 — corner reorder
        nx1, nx2 = _reorder(nx1, nx2)
        ny1, ny2 = _reorder(ny1, ny2)
        out.append(Detection(
            label=det.label,
            bbox=(nx1, ny1, nx2, ny2),
            confidence=det.confidence,
        ))
    return out


_EPSILON_PX = 1e-3


def _reorder(a: float, b: float) -> Tuple[float, float]:
    """a < b 강제 (degenerate 측 ε 분리)."""
    lo, hi = (a, b) if a < b else (b, a)
    if hi - lo < _EPSILON_PX:
        hi = lo + _EPSILON_PX
    return lo, hi
