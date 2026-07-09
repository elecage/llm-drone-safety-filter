"""attribute_mismatch.py 단위 테스트 — 4 variant × happy + edge + reproducibility + 분포."""

from __future__ import annotations

import random
import statistics

import pytest

from eval_faults.attribute_mismatch import apply_attribute_mismatch
from eval_faults.schemas import (
    AttributeMismatchContext,
    AttributeMismatchVariant,
    Detection,
)


# ----------------------------------------------------------- fixtures


@pytest.fixture
def context() -> AttributeMismatchContext:
    """ADR-0006 S5 거실 + S8 yard 측 vocabulary 합집합 + COCO distractor."""
    return AttributeMismatchContext(
        vocabulary=['cup', 'book', 'mug', 'chair', 'table', 'person', 'cat'],
        sigma_ovd_label_swap_rate=0.05,
        sigma_ovd_bbox_px=10.0,
        dangerous_label='person',
    )


@pytest.fixture
def detections() -> list:
    """OVD 측 정상 detection 3 개 (cup + book + chair)."""
    return [
        Detection(label='cup', bbox=(100.0, 100.0, 150.0, 150.0), confidence=0.9),
        Detection(label='book', bbox=(200.0, 200.0, 280.0, 240.0), confidence=0.85),
        Detection(label='chair', bbox=(50.0, 300.0, 180.0, 420.0), confidence=0.95),
    ]


# ----------------------------------------------------------- LABEL_LOW


class TestLabelLow:
    def test_returns_same_length(self, context, detections):
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_LOW, context, random.Random(42),
        )
        assert len(out) == len(detections)

    def test_bbox_and_confidence_preserved(self, context, detections):
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_LOW, context, random.Random(0),
        )
        for orig, new in zip(detections, out):
            assert new.bbox == orig.bbox
            assert new.confidence == orig.confidence

    def test_low_swap_rate_approximate(self, context, detections):
        """200 trial × 3 detection = 600 sample 측 swap 비율 ≈ σ=0.05."""
        n_swaps = 0
        n_total = 0
        for s in range(200):
            out = apply_attribute_mismatch(
                detections, AttributeMismatchVariant.LABEL_LOW, context, random.Random(s),
            )
            for orig, new in zip(detections, out):
                n_total += 1
                if new.label != orig.label:
                    n_swaps += 1
        rate = n_swaps / n_total
        # σ=0.05, N=600 sample 측 binomial 95% CI ≈ 0.05 ± 0.018
        assert 0.03 < rate < 0.08, f'LABEL_LOW swap rate={rate:.3f} (expect ≈ 0.05)'

    def test_swapped_label_in_vocabulary(self, context, detections):
        """swap 시 새 label 은 vocabulary 측 + 원본 label 과 다름."""
        for s in range(50):
            out = apply_attribute_mismatch(
                detections, AttributeMismatchVariant.LABEL_LOW, context, random.Random(s),
            )
            for orig, new in zip(detections, out):
                assert new.label in context.vocabulary
                if new.label != orig.label:
                    assert new.label in context.vocabulary


# ----------------------------------------------------------- LABEL_MED


class TestLabelMed:
    def test_med_swap_rate_5x(self, context, detections):
        """200 trial × 3 = 600 sample 측 swap rate ≈ 5σ = 0.25."""
        n_swaps = 0
        n_total = 0
        for s in range(200):
            out = apply_attribute_mismatch(
                detections, AttributeMismatchVariant.LABEL_MED, context, random.Random(s),
            )
            for orig, new in zip(detections, out):
                n_total += 1
                if new.label != orig.label:
                    n_swaps += 1
        rate = n_swaps / n_total
        # 5σ = 0.25, N=600 binomial 95% CI ≈ 0.25 ± 0.035
        assert 0.21 < rate < 0.29, f'LABEL_MED swap rate={rate:.3f} (expect ≈ 0.25)'

    def test_med_higher_than_low(self, context, detections):
        """LABEL_MED swap 빈도 > LABEL_LOW (5× 강도 검증)."""
        def count_swaps(variant, n=100):
            total = 0
            for s in range(n):
                out = apply_attribute_mismatch(
                    detections, variant, context, random.Random(s),
                )
                for orig, new in zip(detections, out):
                    if new.label != orig.label:
                        total += 1
            return total

        n_low = count_swaps(AttributeMismatchVariant.LABEL_LOW)
        n_med = count_swaps(AttributeMismatchVariant.LABEL_MED)
        assert n_med > n_low * 3, f'MED ({n_med}) 가 LOW ({n_low}) 의 3× 이상 — 강도 차이 검증'

    def test_swap_rate_clipped_at_1(self, detections):
        """5σ > 1 측 clip — context 측 σ=0.3 → 5σ=1.5 → clip=1.0 → 모든 detection swap."""
        ctx = AttributeMismatchContext(
            vocabulary=['cup', 'book', 'mug', 'chair', 'person'],
            sigma_ovd_label_swap_rate=0.3,  # 5× = 1.5
        )
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_MED, ctx, random.Random(0),
        )
        # 모든 detection swap (확률 1.0)
        for orig, new in zip(detections, out):
            assert new.label != orig.label, f'5σ clip 후 모든 swap 기대 — {orig.label} → {new.label}'


# ----------------------------------------------------------- LABEL_WORST


class TestLabelWorst:
    def test_all_labels_to_dangerous(self, context, detections):
        """모든 detection 측 label = dangerous_label (결정론)."""
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_WORST, context, random.Random(0),
        )
        for new in out:
            assert new.label == context.dangerous_label

    def test_bbox_and_confidence_preserved(self, context, detections):
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_WORST, context, random.Random(0),
        )
        for orig, new in zip(detections, out):
            assert new.bbox == orig.bbox
            assert new.confidence == orig.confidence

    def test_deterministic_no_rng_dependency(self, context, detections):
        """결정론 — 다른 seed 측 동일 출력."""
        out_a = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_WORST, context, random.Random(0),
        )
        out_b = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_WORST, context, random.Random(999),
        )
        assert out_a == out_b

    def test_dangerous_label_not_in_vocabulary_still_applied(self, detections):
        """dangerous_label 이 vocabulary 측 부재여도 강제 적용 (worst case 가정)."""
        ctx = AttributeMismatchContext(
            vocabulary=['cup', 'book'],
            dangerous_label='person',  # vocabulary 부재
        )
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_WORST, ctx, random.Random(0),
        )
        for new in out:
            assert new.label == 'person'


# ----------------------------------------------------------- BBOX_SHIFT


class TestBboxShift:
    def test_returns_same_length(self, context, detections):
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.BBOX_SHIFT, context, random.Random(42),
        )
        assert len(out) == len(detections)

    def test_label_and_confidence_preserved(self, context, detections):
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.BBOX_SHIFT, context, random.Random(0),
        )
        for orig, new in zip(detections, out):
            assert new.label == orig.label
            assert new.confidence == orig.confidence

    def test_bbox_corner_invariant_preserved(self, context, detections):
        """변형 후 모든 bbox 측 $x_1 < x_2$ / $y_1 < y_2$ 강제."""
        for s in range(100):
            out = apply_attribute_mismatch(
                detections, AttributeMismatchVariant.BBOX_SHIFT, context, random.Random(s),
            )
            for new in out:
                x1, y1, x2, y2 = new.bbox
                assert x1 < x2, f'bbox invariant 깨짐 — x1={x1}, x2={x2}'
                assert y1 < y2, f'bbox invariant 깨짐 — y1={y1}, y2={y2}'

    def test_bbox_shift_distribution(self, context, detections):
        """각 corner 측 shift 분포 std ≈ σ=10 px (200 sample)."""
        x1_shifts = []
        for s in range(200):
            out = apply_attribute_mismatch(
                detections, AttributeMismatchVariant.BBOX_SHIFT, context, random.Random(s),
            )
            # 첫 detection 측 x1 corner 측 shift
            orig_x1 = detections[0].bbox[0]
            x1_shifts.append(out[0].bbox[0] - orig_x1)
        # corner reorder 측 std 영향 가능 — corner sample 측 |shift| 의 분포
        # 가 *순수 Gaussian σ=10* 에서 약간 다를 수 있음 (reorder 측 truncate
        # 영향). 200 sample 측 std 7 ~ 13 안 OK.
        std = statistics.stdev(x1_shifts)
        assert 7.0 < std < 13.0, f'x1 shift std={std:.2f} (expect ≈ 10)'

    def test_zero_sigma_no_shift(self, detections):
        """σ_bbox=0 측 corner 측 변형 없음 (Gaussian 0 std → 0)."""
        ctx = AttributeMismatchContext(
            vocabulary=['cup'], sigma_ovd_bbox_px=0.0,
        )
        out = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.BBOX_SHIFT, ctx, random.Random(0),
        )
        # σ=0 → corner 측 그대로 (degenerate 측 ε 분리 없음, bbox 원본 valid)
        for orig, new in zip(detections, out):
            assert new.bbox == orig.bbox


# ----------------------------------------------------------- empty + edge


class TestEdgeCases:
    def test_empty_detections_returns_empty(self, context):
        """빈 list 측 빈 list 반환 (no-op)."""
        for variant in AttributeMismatchVariant:
            out = apply_attribute_mismatch(
                [], variant, context, random.Random(0),
            )
            assert out == []

    def test_unknown_variant_raises(self, context, detections):
        with pytest.raises((ValueError, AttributeError)):
            apply_attribute_mismatch(
                detections, 'not_a_variant',  # type: ignore
                context, random.Random(0),
            )

    def test_label_swap_with_single_vocabulary_no_swap(self, detections):
        """vocabulary 측 1 라벨만 + 그게 현재 label → swap 후보 없음 → orig 유지."""
        ctx = AttributeMismatchContext(
            vocabulary=['cup'],
            sigma_ovd_label_swap_rate=1.0,  # swap 시도 100%
        )
        # detection[0].label = 'cup' (vocabulary 와 동일)
        out = apply_attribute_mismatch(
            detections[:1], AttributeMismatchVariant.LABEL_LOW, ctx, random.Random(0),
        )
        # swap 후보 부재 → orig 'cup' 유지
        assert out[0].label == 'cup'

    def test_label_swap_with_label_outside_vocabulary(self):
        """detection.label 이 vocabulary 측 *부재* → 전체 vocabulary 측 random
        swap (PR #102 review A-1 — docstring Note 측 동작 명시적 lock).

        OVD 백본 측 학습 분포 외 라벨 출력 가능성 시뮬 — silent swap 동작이
        의도된 design choice (거부 안 함).
        """
        ctx = AttributeMismatchContext(
            vocabulary=['cup', 'book'],
            sigma_ovd_label_swap_rate=1.0,  # swap 시도 100%
        )
        # detection.label = 'unknown_label' (vocabulary 외)
        det = Detection(
            label='unknown_label',
            bbox=(0.0, 0.0, 10.0, 20.0),
            confidence=0.7,
        )
        out = apply_attribute_mismatch(
            [det], AttributeMismatchVariant.LABEL_LOW, ctx, random.Random(0),
        )
        # 전체 vocabulary 측 swap (orig 'unknown_label' 제외 → ['cup', 'book'])
        assert out[0].label in {'cup', 'book'}
        assert out[0].label != 'unknown_label'


# ----------------------------------------------------------- reproducibility


class TestReproducibility:
    @pytest.mark.parametrize('variant', list(AttributeMismatchVariant))
    def test_same_seed_same_output(self, context, detections, variant):
        out_a = apply_attribute_mismatch(
            detections, variant, context, random.Random(42),
        )
        out_b = apply_attribute_mismatch(
            detections, variant, context, random.Random(42),
        )
        assert out_a == out_b

    def test_different_seeds_can_differ_label_low(self, context, detections):
        """다른 seed 측 LABEL_LOW 결과 다를 수 있음 (확률적)."""
        out_0 = apply_attribute_mismatch(
            detections, AttributeMismatchVariant.LABEL_LOW, context, random.Random(0),
        )
        differ = False
        for s in range(1, 60):
            out_s = apply_attribute_mismatch(
                detections, AttributeMismatchVariant.LABEL_LOW, context, random.Random(s),
            )
            if out_s != out_0:
                differ = True
                break
        assert differ, '여러 seed 측 모두 동일 — random 분포 의문'


# ----------------------------------------------------------- Detection 검증


class TestDetectionDataclass:
    def test_valid_detection(self):
        d = Detection(label='cup', bbox=(0.0, 0.0, 10.0, 20.0), confidence=0.5)
        assert d.label == 'cup'

    def test_empty_label_rejected(self):
        with pytest.raises(ValueError, match='label'):
            Detection(label='', bbox=(0.0, 0.0, 10.0, 20.0), confidence=0.5)

    def test_bbox_invariant_rejected(self):
        with pytest.raises(ValueError, match='bbox corner'):
            Detection(label='cup', bbox=(10.0, 0.0, 5.0, 20.0), confidence=0.5)
        with pytest.raises(ValueError, match='bbox corner'):
            Detection(label='cup', bbox=(0.0, 20.0, 10.0, 5.0), confidence=0.5)

    def test_bbox_wrong_arity(self):
        with pytest.raises(ValueError, match=r'4-tuple'):
            Detection(label='cup', bbox=(0.0, 0.0, 10.0), confidence=0.5)  # type: ignore

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError, match='confidence'):
            Detection(label='cup', bbox=(0.0, 0.0, 10.0, 20.0), confidence=1.5)
        with pytest.raises(ValueError, match='confidence'):
            Detection(label='cup', bbox=(0.0, 0.0, 10.0, 20.0), confidence=-0.1)


# ----------------------------------------------------------- Context 검증


class TestContextValidation:
    def test_empty_vocabulary_rejected(self):
        with pytest.raises(ValueError, match='vocabulary'):
            AttributeMismatchContext(vocabulary=[])

    def test_duplicate_vocabulary_rejected(self):
        with pytest.raises(ValueError, match='중복'):
            AttributeMismatchContext(vocabulary=['cup', 'cup', 'book'])

    def test_non_string_vocabulary_rejected(self):
        with pytest.raises(ValueError, match='vocabulary'):
            AttributeMismatchContext(vocabulary=['cup', ''])

    def test_sigma_label_out_of_range_rejected(self):
        with pytest.raises(ValueError, match='sigma_ovd_label_swap_rate'):
            AttributeMismatchContext(vocabulary=['cup'], sigma_ovd_label_swap_rate=-0.1)
        with pytest.raises(ValueError, match='sigma_ovd_label_swap_rate'):
            AttributeMismatchContext(vocabulary=['cup'], sigma_ovd_label_swap_rate=1.5)

    def test_negative_sigma_bbox_rejected(self):
        with pytest.raises(ValueError, match='sigma_ovd_bbox_px'):
            AttributeMismatchContext(vocabulary=['cup'], sigma_ovd_bbox_px=-1.0)

    def test_empty_dangerous_label_rejected(self):
        with pytest.raises(ValueError, match='dangerous_label'):
            AttributeMismatchContext(vocabulary=['cup'], dangerous_label='')


# ----------------------------------------------------------- schema lock


class TestAttributeMismatchVariantSchema:
    def test_four_variants_locked(self):
        """ADR-0025 D1.d amendment 9 — 4 variant 잠금."""
        names = {v.value for v in AttributeMismatchVariant}
        assert names == {
            'attribute_mismatch_label_low',
            'attribute_mismatch_label_med',
            'attribute_mismatch_label_worst',
            'attribute_mismatch_bbox_shift',
        }
