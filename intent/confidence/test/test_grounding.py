"""grounding.py 단위 테스트 — s1 신호 소스 (OVD 후보 분포 → H → s1).

ADR-0020 C12 1차. referential 모호성(S5 mug 3개)을 s1 이 잡는지 + estimator
compute_g 결합 검증.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from intent_confidence.estimator import GInputs, compute_g
from intent_confidence.grounding import (
    dedup_overlapping_candidates,
    grounding_entropy,
    is_duplicate_box,
    referent_scores,
    s1_from_scores,
    spatial_weight,
    weighted_referent_scores,
)


@dataclass(frozen=True)
class _Det:
    """duck-typed detection (intent_ovd.Detection 정합)."""
    class_label: str
    confidence: float


@dataclass(frozen=True)
class _BoxDet:
    """duck-typed detection + 픽셀 bbox (cx, cy, w, h) — dedup(D7) 용."""
    class_label: str
    confidence: float
    bbox: tuple


@dataclass(frozen=True)
class _PosDet:
    """duck-typed detection + world 좌표 (위치 disambiguation 용)."""
    class_label: str
    confidence: float
    position: tuple


class TestGroundingEntropy:
    def test_single_candidate_zero(self) -> None:
        assert grounding_entropy([0.9]) == 0.0

    def test_uniform_max_entropy(self) -> None:
        # 3개 균일 → H = 1
        assert grounding_entropy([0.5, 0.5, 0.5]) == pytest.approx(1.0)

    def test_near_uniform_high(self) -> None:
        # S5 mug 3개 외형 동일 → 거의 균일 → H 높음
        h = grounding_entropy([0.90, 0.88, 0.91])
        assert h > 0.99

    def test_dominant_low(self) -> None:
        # 단일 dominant → H 낮음
        h = grounding_entropy([0.95, 0.05, 0.05])
        assert h < 0.5

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            grounding_entropy([])

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            grounding_entropy([0.5, -0.1])

    def test_all_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            grounding_entropy([0.0, 0.0])

    def test_range(self) -> None:
        for scores in ([0.9], [0.5, 0.5], [0.9, 0.1], [0.3, 0.3, 0.4]):
            assert 0.0 <= grounding_entropy(scores) <= 1.0


class TestS1FromScores:
    def test_s5_ambiguous_low_s1(self) -> None:
        # S5: mug 3개 동일 → s1 ≈ 0 (모호)
        assert s1_from_scores([0.90, 0.88, 0.91]) < 0.01

    def test_s7_clear_high_s1(self) -> None:
        # S7: 단일 dominant book → s1 = 1 (명확)
        assert s1_from_scores([0.95]) == pytest.approx(1.0)

    def test_s1_in_unit(self) -> None:
        assert 0.0 <= s1_from_scores([0.9, 0.5, 0.4]) <= 1.0


class TestReferentScores:
    def test_filters_by_label(self) -> None:
        dets = [_Det('mug', 0.9), _Det('mug', 0.88), _Det('sofa', 0.7)]
        assert referent_scores(dets, ['mug']) == [0.9, 0.88]

    def test_empty_when_no_match(self) -> None:
        dets = [_Det('sofa', 0.7)]
        assert referent_scores(dets, ['mug']) == []

    def test_composite_label_token_match(self) -> None:
        # direct mode 합성 라벨 'mug_cup' → 토큰 'cup' 으로 ovd class 'cup' 매칭
        # 복원 (완전일치였으면 [] → no_match → s1 absent). S5 vantage·grounding 정합.
        dets = [_Det('cup', 0.84), _Det('cup', 0.7), _Det('cup', 0.62)]
        assert referent_scores(dets, ['mug_cup']) == [0.84, 0.7, 0.62]

    def test_single_token_label_unchanged(self) -> None:
        # 단일 토큰 라벨('chair')은 토큰 확장에도 완전일치와 동일 — 회귀 방지.
        dets = [_Det('chair', 0.9), _Det('sofa', 0.5)]
        assert referent_scores(dets, ['chair']) == [0.9]

    def test_label_case_insensitive(self) -> None:
        # 대소문자 정규화 — 'Cup' detection ↔ 'mug_cup' 토큰 'cup' 매칭.
        dets = [_Det('Cup', 0.8)]
        assert referent_scores(dets, ['mug_cup']) == [0.8]

    def test_synonym_label_normalized(self) -> None:
        # 세션 62 llama S5 회귀: σ target_id='mug' (합성 아닌 단일 토큰 동의어 —
        # PR #283 토큰 분해로 복원 불가) → OVD_CLASS_SYNONYMS 단일 소스
        # ('mug'→'cup', scenario_params.scene)로 매칭 복원. 후보 3개 전부 잡혀
        # 엔트로피(모호성)가 s1 에 반영된다.
        dets = [_Det('cup', 0.84), _Det('cup', 0.7), _Det('cup', 0.62)]
        assert referent_scores(dets, ['mug']) == [0.84, 0.7, 0.62]

    def test_synonym_couch_normalized(self) -> None:
        # 'couch' → 정본 'sofa' (S6 단일 referent 경로에도 동일 표 적용).
        dets = [_Det('sofa', 0.9)]
        assert referent_scores(dets, ['couch']) == [0.9]


class TestS1ResolvesReferentialAmbiguity:
    """핵심: s2(self-consistency)가 못 잡는 referential 모호성을 s1 이 c로 반영.

    실측(2026-05-29): gpt-4o S5 → mug_left 8/8 일관 → s2(ρ)≈1.0 높음.
    s1 (OVD 후보 entropy)이 없으면 c 높음(위험). s1 있으면 c 낮음(안전).
    """

    def test_s5_self_consistent_but_low_c_via_s1(self) -> None:
        # S5: LLM self-consistency 높음(s2=1.0, mug_left 일관) + logprob 높음(s3=0.9)
        # but OVD 후보 flat → s1 낮음 → c 낮음 (안전).
        s5_dets = [_Det('mug', 0.90), _Det('mug', 0.88), _Det('mug', 0.91)]
        s1 = s1_from_scores(referent_scores(s5_dets, ['mug']))
        c = compute_g(GInputs(s1=s1, s2=1.0, s3=0.9))
        assert c < 0.05  # s1 ≈ 0 이 s2·s3 높아도 c 억제

    def test_s7_clear_high_c(self) -> None:
        # S7: 단일 dominant book → s1 ≈ 1 → c 높음.
        s7_dets = [_Det('book', 0.95)]
        s1 = s1_from_scores(referent_scores(s7_dets, ['book']))
        c = compute_g(GInputs(s1=s1, s2=1.0, s3=0.9))
        assert c > 0.85

    def test_s1_absent_when_no_referent(self) -> None:
        # referent 못 찾음 → 빈 후보 → caller 가 s1_absent → c=0 (보수).
        s1_absent = (referent_scores([_Det('sofa', 0.7)], ['mug']) == [])
        c = compute_g(GInputs(s1=0.0, s2=1.0, s3=0.9, s1_absent=s1_absent))
        assert c == 0.0


class TestSpatialWeight:
    def test_zero_distance_is_one(self) -> None:
        assert spatial_weight((1.0, 2.0, 3.0), (1.0, 2.0, 9.0)) == pytest.approx(1.0)

    def test_sigma_distance(self) -> None:
        # d_xy = σ = 0.5 → exp(-0.5) ≈ 0.6065
        assert spatial_weight((0.5, 0.0), (0.0, 0.0), sigma_m=0.5) == pytest.approx(
            math.exp(-0.5), abs=1e-6
        )

    def test_large_distance_near_zero(self) -> None:
        assert spatial_weight((10.0, 10.0), (0.0, 0.0), sigma_m=0.5) < 1e-6

    def test_z_ignored(self) -> None:
        # z 차이만 있으면 xy 거리 0 → weight 1 (위/아래 무관)
        assert spatial_weight((1.0, 1.0, 0.0), (1.0, 1.0, 5.0)) == pytest.approx(1.0)

    def test_nonpositive_sigma_raises(self) -> None:
        with pytest.raises(ValueError):
            spatial_weight((0.0, 0.0), (0.0, 0.0), sigma_m=0.0)


class TestWeightedReferentScores:
    def test_s7_anchor_disambiguates(self) -> None:
        # S7: book 2개 (거실탁자 위 / 식탁 위), anchor=거실탁자 → 가까운 book만 dominant.
        dets = [
            _PosDet('book', 0.90, (-1.8, 0.5, 0.45)),   # coffee_table 위
            _PosDet('book', 0.90, (2.0, -1.0, 0.80)),   # dining_table 위
        ]
        anchor = (-1.8, 0.5, 0.2)  # coffee_table xy
        scores = weighted_referent_scores(dets, ['book'], anchor=anchor)
        # 가까운 book ≈ 0.9, 먼 book ≈ 0 → 분포 dominant → s1 높음.
        assert scores[0] == pytest.approx(0.90, abs=1e-6)
        assert scores[1] < 1e-3
        assert s1_from_scores(scores) > 0.95

    def test_s5_no_anchor_stays_flat(self) -> None:
        # S5: mug 3개 동일 confidence, 위치 단서 없음(anchor=None) → flat → s1 낮음.
        dets = [
            _PosDet('mug', 0.90, (1.7, -1.0, 0.80)),
            _PosDet('mug', 0.88, (2.0, -1.0, 0.80)),
            _PosDet('mug', 0.91, (2.3, -1.0, 0.80)),
        ]
        scores = weighted_referent_scores(dets, ['mug'], anchor=None)
        assert scores == [0.90, 0.88, 0.91]  # weight=1 전부 (label-only)
        assert s1_from_scores(scores) < 0.01

    def test_filters_by_label(self) -> None:
        dets = [
            _PosDet('book', 0.9, (0.0, 0.0, 0.0)),
            _PosDet('sofa', 0.7, (0.0, 0.0, 0.0)),
        ]
        assert weighted_referent_scores(dets, ['book'], anchor=(0.0, 0.0, 0.0)) == [0.9]

    def test_position_absent_graceful_degrade(self) -> None:
        # .position 없는 후보 + anchor → weight=1 (좁히지 않음, label-only).
        dets = [_Det('book', 0.9), _Det('book', 0.8)]
        scores = weighted_referent_scores(dets, ['book'], anchor=(5.0, 5.0, 0.0))
        assert scores == [0.9, 0.8]

    def test_anchor_none_matches_referent_scores(self) -> None:
        dets = [_PosDet('mug', 0.9, (1.0, 1.0, 0.0)), _PosDet('mug', 0.8, (2.0, 2.0, 0.0))]
        assert weighted_referent_scores(dets, ['mug'], anchor=None) == referent_scores(
            dets, ['mug']
        )


class TestC12CIntegration:
    """위치 disambiguation → compute_g 결합 — S7 명확(c 높음) vs S5 모호(c 낮음)."""

    def test_s7_anchor_high_c(self) -> None:
        dets = [
            _PosDet('book', 0.90, (-1.8, 0.5, 0.45)),
            _PosDet('book', 0.90, (2.0, -1.0, 0.80)),
        ]
        s1 = s1_from_scores(
            weighted_referent_scores(dets, ['book'], anchor=(-1.8, 0.5, 0.2))
        )
        c = compute_g(GInputs(s1=s1, s2=1.0, s3=0.9))
        assert c > 0.85

    def test_s5_no_anchor_low_c(self) -> None:
        dets = [
            _PosDet('mug', 0.90, (1.7, -1.0, 0.80)),
            _PosDet('mug', 0.88, (2.0, -1.0, 0.80)),
            _PosDet('mug', 0.91, (2.3, -1.0, 0.80)),
        ]
        s1 = s1_from_scores(weighted_referent_scores(dets, ['mug'], anchor=None))
        c = compute_g(GInputs(s1=s1, s2=1.0, s3=0.9))
        assert c < 0.05


class TestDedupOverlappingCandidates:
    """ADR-0040 D7 — 동일 라벨 중복박스 dedup (세션 61 진단: OVD 2박스 아티팩트)."""

    def test_nested_same_label_merges_to_one(self) -> None:
        # 세션 61 S6 케이스 — 같은 sofa 가 전체 박스 + 좌측 부분 박스(nested IoU~0.5)
        # 로 2번 검출. dedup → confidence 높은 1개만 → s1 = 1.0 (단일 referent 보존).
        dets = [
            _BoxDet('sofa', 0.64, (183, 387, 364, 187)),   # 전체
            _BoxDet('sofa', 0.62, (101, 385, 202, 190)),   # 좌측 부분(nested)
        ]
        kept = dedup_overlapping_candidates(dets)
        assert len(kept) == 1 and kept[0].confidence == pytest.approx(0.64)
        s1 = s1_from_scores(referent_scores(dets, ['sofa']))
        assert s1 == pytest.approx(1.0)   # 붕괴 없음

    def test_disjoint_same_label_kept(self) -> None:
        # S7 류 — 분리된 의자 2개(겹침 없음)는 진짜 별개 후보 → 유지 → s1 낮음(모호).
        dets = [
            _BoxDet('chair', 0.80, (100, 300, 80, 120)),
            _BoxDet('chair', 0.78, (400, 300, 80, 120)),
        ]
        kept = dedup_overlapping_candidates(dets)
        assert len(kept) == 2
        s1 = s1_from_scores(referent_scores(dets, ['chair']))
        assert s1 < 0.05   # 거의 균일 → 모호 보존

    def test_different_labels_not_merged(self) -> None:
        # 라벨이 다르면 겹쳐도 병합 안 함(예 cup-on-table).
        dets = [
            _BoxDet('table', 0.70, (200, 300, 400, 200)),
            _BoxDet('cup', 0.65, (200, 280, 60, 60)),   # table 박스 안
        ]
        kept = dedup_overlapping_candidates(dets)
        assert len(kept) == 2

    def test_no_bbox_unchanged(self) -> None:
        # bbox 없는 후보는 dedup 대상 아님(순서·내용 보존, graceful).
        dets = [_Det('sofa', 0.6), _Det('sofa', 0.7)]
        kept = dedup_overlapping_candidates(dets)
        assert [c.confidence for c in kept] == [0.6, 0.7]

    def test_is_duplicate_box_nested_high_containment(self) -> None:
        # 작은 박스가 큰 박스에 거의 포함 → containment 높음 → 중복(IoU 낮아도).
        big = (183, 387, 364, 187)
        small = (101, 385, 202, 190)
        assert is_duplicate_box(big, small) is True

    def test_is_duplicate_box_disjoint_false(self) -> None:
        assert is_duplicate_box((100, 300, 80, 120), (400, 300, 80, 120)) is False

    def test_weighted_scores_dedup_applied(self) -> None:
        # weighted_referent_scores 경로에도 dedup 적용 — 중복 sofa → 점수 1개.
        dets = [
            _BoxDet('sofa', 0.64, (183, 387, 364, 187)),
            _BoxDet('sofa', 0.62, (101, 385, 202, 190)),
        ]
        scores = weighted_referent_scores(dets, ['sofa'], anchor=None)
        assert len(scores) == 1
