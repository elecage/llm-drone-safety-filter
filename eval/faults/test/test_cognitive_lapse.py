"""cognitive_lapse.py 단위 테스트 — 4 variant × happy + edge + reproducibility + 분포."""

from __future__ import annotations

import random
import statistics

import pytest

from eval_faults.cognitive_lapse import apply_cognitive_lapse
from eval_faults.schemas import (
    CognitiveLapseContext,
    CognitiveLapseVariant,
    LapseEvent,
)


# ----------------------------------------------------------- fixtures


@pytest.fixture
def context() -> CognitiveLapseContext:
    """S7 §2.2 두 후보 — 거실 탁자 위 책 + 식탁 위 머그컵."""
    return CognitiveLapseContext(
        initial_target_id='book_living_table',
        initial_target_name_kr='거실 탁자 위 책',
        alternative_target_id='mug_dining_table',
        alternative_target_name_kr='식탁 위 머그컵',
    )


# ----------------------------------------------------------- E1 self-correction


class TestE1SelfCorrection:
    def test_returns_lapse_event(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(42),
        )
        assert isinstance(event, LapseEvent)
        assert event.variant == CognitiveLapseVariant.E1_SELF_CORRECTION

    def test_initial_utterance_contains_initial_target(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(7),
        )
        assert context.initial_target_name_kr in event.initial_utterance

    def test_follow_up_contains_alternative_target(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(7),
        )
        assert event.follow_up_utterance is not None
        assert context.alternative_target_name_kr in event.follow_up_utterance

    def test_silence_threshold_none(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(0),
        )
        assert event.silence_threshold_s is None

    def test_raw_c_in_unit_interval(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(0),
        )
        assert event.raw_c_after_event is not None
        assert 0.0 <= event.raw_c_after_event <= 1.0

    def test_raw_c_distribution_high(self, context):
        """200 sample 평균 ≈ 0.90, std ≈ 0.03 (S7 §3.3)."""
        samples = []
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(s),
            )
            samples.append(event.raw_c_after_event)
        mean = statistics.fmean(samples)
        std = statistics.stdev(samples)
        # mu=0.90, sigma=0.03 — clip 영향 negligible (clip 거리 3σ 이상)
        assert abs(mean - 0.90) < 0.02, f'E1 raw_c mean={mean:.3f}'
        assert 0.02 < std < 0.05, f'E1 raw_c std={std:.3f}'

    def test_follow_up_template_variety(self, context):
        """200 sample 측 4 개 template 모두 등장."""
        templates_seen = set()
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(s),
            )
            if '아니, ' in event.follow_up_utterance:
                templates_seen.add('아니')
            elif '잠깐, ' in event.follow_up_utterance:
                templates_seen.add('잠깐')
            elif event.follow_up_utterance.startswith('식탁'):
                templates_seen.add('바꿔')
            elif '그거 말고 ' in event.follow_up_utterance:
                templates_seen.add('그거말고')
        assert templates_seen == {'아니', '잠깐', '바꿔', '그거말고'}, (
            f'일부 template 미등장 — seen={templates_seen}'
        )


# ----------------------------------------------------------- E2 self-contradiction


class TestE2SelfContradiction:
    def test_returns_lapse_event(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E2_SELF_CONTRADICTION, context, random.Random(42),
        )
        assert event.variant == CognitiveLapseVariant.E2_SELF_CONTRADICTION
        assert event.follow_up_utterance is not None
        assert event.silence_threshold_s is None

    def test_raw_c_distribution_low(self, context):
        """200 sample 평균 ≈ 0.30, std ≈ 0.05 (S7 §3.3)."""
        samples = []
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E2_SELF_CONTRADICTION, context, random.Random(s),
            )
            samples.append(event.raw_c_after_event)
        mean = statistics.fmean(samples)
        std = statistics.stdev(samples)
        assert abs(mean - 0.30) < 0.02, f'E2 raw_c mean={mean:.3f}'
        assert 0.03 < std < 0.07, f'E2 raw_c std={std:.3f}'

    def test_follow_up_contains_alternative(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E2_SELF_CONTRADICTION, context, random.Random(1),
        )
        assert context.alternative_target_name_kr in event.follow_up_utterance

    def test_follow_up_template_variety(self, context):
        templates_seen = set()
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E2_SELF_CONTRADICTION, context, random.Random(s),
            )
            if event.follow_up_utterance.startswith('왜 '):
                templates_seen.add('왜')
            elif '보여달라' in event.follow_up_utterance:
                templates_seen.add('달라')
            elif '먼저 가야지' in event.follow_up_utterance:
                templates_seen.add('가야지')
            elif '어디 가?' in event.follow_up_utterance:
                templates_seen.add('어디')
        assert templates_seen == {'왜', '달라', '가야지', '어디'}, (
            f'일부 template 미등장 — seen={templates_seen}'
        )


# ----------------------------------------------------------- E3 explicit cancel


class TestE3ExplicitCancel:
    def test_returns_lapse_event(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E3_EXPLICIT_CANCEL, context, random.Random(42),
        )
        assert event.variant == CognitiveLapseVariant.E3_EXPLICIT_CANCEL
        assert event.follow_up_utterance is not None

    def test_follow_up_does_not_reference_alternative(self, context):
        """E3 정형 RTL 명령 — alt target 무관."""
        for s in range(50):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E3_EXPLICIT_CANCEL, context, random.Random(s),
            )
            assert context.alternative_target_name_kr not in event.follow_up_utterance

    def test_raw_c_distribution_very_high(self, context):
        """200 sample 평균 ≈ 0.95, std ≈ 0.02 (S7 §3.3) — clip 영향 약간."""
        samples = []
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E3_EXPLICIT_CANCEL, context, random.Random(s),
            )
            samples.append(event.raw_c_after_event)
        mean = statistics.fmean(samples)
        # mu=0.95, sigma=0.02 — 우측 clip 거리 2.5σ → mean 약간 하향 (0.949 정도)
        assert abs(mean - 0.95) < 0.02, f'E3 raw_c mean={mean:.3f}'

    def test_follow_up_mentions_cancel_keywords(self, context):
        """그만 / 취소 / 멈춰 키워드 등장."""
        for s in range(50):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E3_EXPLICIT_CANCEL, context, random.Random(s),
            )
            assert any(
                kw in event.follow_up_utterance
                for kw in ('그만', '취소', '멈춰')
            ), f'E3 키워드 부재 — {event.follow_up_utterance!r}'


# ----------------------------------------------------------- E4 utterance cut


class TestE4UtteranceCut:
    def test_returns_lapse_event(self, context):
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E4_UTTERANCE_CUT, context, random.Random(42),
        )
        assert event.variant == CognitiveLapseVariant.E4_UTTERANCE_CUT

    def test_follow_up_is_none(self, context):
        """E4 = 발화 부재 — follow_up_utterance is None."""
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E4_UTTERANCE_CUT, context, random.Random(7),
        )
        assert event.follow_up_utterance is None

    def test_raw_c_is_none(self, context):
        """E4 raw_c = 안전 계층 측 fail-safe 감쇠 — sample 안 함."""
        event = apply_cognitive_lapse(
            CognitiveLapseVariant.E4_UTTERANCE_CUT, context, random.Random(7),
        )
        assert event.raw_c_after_event is None

    def test_silence_threshold_in_context_range(self, context):
        """silence_threshold_s ∈ [8, 15] s (S7 §4)."""
        s_lo, s_hi = context.silence_threshold_range_s
        for s in range(50):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E4_UTTERANCE_CUT, context, random.Random(s),
            )
            assert s_lo <= event.silence_threshold_s <= s_hi, (
                f'silence_threshold_s={event.silence_threshold_s} out of '
                f'[{s_lo}, {s_hi}]'
            )

    def test_silence_threshold_distribution_uniform(self, context):
        """200 sample 평균 ≈ (8+15)/2 = 11.5 s."""
        samples = []
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E4_UTTERANCE_CUT, context, random.Random(s),
            )
            samples.append(event.silence_threshold_s)
        mean = statistics.fmean(samples)
        assert 10.5 < mean < 12.5, f'silence threshold mean={mean:.2f}'


# ----------------------------------------------------------- trigger_time + initial


class TestTriggerTimeAndInitialUtterance:
    def test_trigger_time_in_context_range(self, context):
        """trigger_time_s ∈ [3, 25] s (S7 §4)."""
        t_lo, t_hi = context.trigger_time_range_s
        for s in range(50):
            for variant in CognitiveLapseVariant:
                event = apply_cognitive_lapse(variant, context, random.Random(s))
                assert t_lo <= event.trigger_time_s <= t_hi, (
                    f'{variant.value} trigger_time={event.trigger_time_s} '
                    f'out of [{t_lo}, {t_hi}]'
                )

    def test_initial_utterance_template_variety(self, context):
        """200 sample 측 initial utterance 4 template 모두 등장."""
        templates_seen = set()
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(s),
            )
            # 구체적 패턴 먼저 (" 좀 보여줘." 가 " 보여줘." 에 먼저 매치되지
            # 않도록 순서 잠금)
            if event.initial_utterance.endswith(' 좀 보여줘.'):
                templates_seen.add('좀')
            elif event.initial_utterance.endswith(' 보여줘.'):
                templates_seen.add('보여줘')
            elif event.initial_utterance.endswith(' 확인해줘.'):
                templates_seen.add('확인')
            elif event.initial_utterance.endswith(' 보여줄래?'):
                templates_seen.add('줄래')
        assert templates_seen == {'보여줘', '좀', '확인', '줄래'}, (
            f'일부 initial template 미등장 — seen={templates_seen}'
        )


# ----------------------------------------------------------- reproducibility


class TestReproducibility:
    @pytest.mark.parametrize('variant', list(CognitiveLapseVariant))
    def test_same_seed_same_event(self, context, variant):
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        event_a = apply_cognitive_lapse(variant, context, rng_a)
        event_b = apply_cognitive_lapse(variant, context, rng_b)
        assert event_a == event_b

    def test_different_seeds_can_differ(self, context):
        event_0 = apply_cognitive_lapse(
            CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(0),
        )
        differ = False
        for s in range(1, 30):
            event_s = apply_cognitive_lapse(
                CognitiveLapseVariant.E1_SELF_CORRECTION, context, random.Random(s),
            )
            if event_s != event_0:
                differ = True
                break
        assert differ, '여러 seed 측 모두 동일 — random 분포 의문'


# ----------------------------------------------------------- edge cases


class TestEdgeCases:
    def test_unknown_variant_raises(self, context):
        with pytest.raises((ValueError, AttributeError)):
            apply_cognitive_lapse(
                'not_a_variant',  # type: ignore
                context, random.Random(0),
            )

    def test_initial_eq_alternative_context_rejected(self):
        with pytest.raises(ValueError, match='동일'):
            CognitiveLapseContext(
                initial_target_id='same_id',
                initial_target_name_kr='같은 대상',
                alternative_target_id='same_id',
                alternative_target_name_kr='같은 대상',
            )

    def test_empty_initial_target_id_rejected(self):
        with pytest.raises(ValueError, match='빈 문자열'):
            CognitiveLapseContext(
                initial_target_id='',
                initial_target_name_kr='이름',
                alternative_target_id='alt',
                alternative_target_name_kr='대안',
            )

    def test_empty_alternative_name_rejected(self):
        with pytest.raises(ValueError, match='빈 문자열'):
            CognitiveLapseContext(
                initial_target_id='init',
                initial_target_name_kr='이름',
                alternative_target_id='alt',
                alternative_target_name_kr='',
            )

    def test_invalid_trigger_range_rejected(self):
        with pytest.raises(ValueError, match='trigger_time_range_s'):
            CognitiveLapseContext(
                initial_target_id='init',
                initial_target_name_kr='이름',
                alternative_target_id='alt',
                alternative_target_name_kr='대안',
                trigger_time_range_s=(5.0, 3.0),  # lo > hi
            )

    def test_invalid_silence_range_rejected(self):
        with pytest.raises(ValueError, match='silence_threshold_range_s'):
            CognitiveLapseContext(
                initial_target_id='init',
                initial_target_name_kr='이름',
                alternative_target_id='alt',
                alternative_target_name_kr='대안',
                silence_threshold_range_s=(10.0, 10.0),  # lo == hi
            )

    def test_negative_trigger_range_rejected(self):
        with pytest.raises(ValueError, match='trigger_time_range_s'):
            CognitiveLapseContext(
                initial_target_id='init',
                initial_target_name_kr='이름',
                alternative_target_id='alt',
                alternative_target_name_kr='대안',
                trigger_time_range_s=(-1.0, 5.0),
            )


# ----------------------------------------------------------- LapseEvent invariants


class TestLapseEventInvariants:
    """직접 LapseEvent 구성 시 variant ↔ field 정합 검증."""

    def test_e4_with_follow_up_rejected(self):
        with pytest.raises(ValueError, match='E4_utterance_cut'):
            LapseEvent(
                variant=CognitiveLapseVariant.E4_UTTERANCE_CUT,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance='should be None',
                silence_threshold_s=10.0,
                raw_c_after_event=None,
            )

    def test_e4_missing_silence_threshold_rejected(self):
        with pytest.raises(ValueError, match='silence_threshold_s 필수'):
            LapseEvent(
                variant=CognitiveLapseVariant.E4_UTTERANCE_CUT,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance=None,
                silence_threshold_s=None,
                raw_c_after_event=None,
            )

    def test_e4_with_raw_c_rejected(self):
        with pytest.raises(ValueError, match='raw_c_after_event 는 None'):
            LapseEvent(
                variant=CognitiveLapseVariant.E4_UTTERANCE_CUT,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance=None,
                silence_threshold_s=10.0,
                raw_c_after_event=0.5,
            )

    def test_e1_missing_follow_up_rejected(self):
        with pytest.raises(ValueError, match='follow_up_utterance'):
            LapseEvent(
                variant=CognitiveLapseVariant.E1_SELF_CORRECTION,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance=None,
                silence_threshold_s=None,
                raw_c_after_event=0.9,
            )

    def test_e1_with_silence_threshold_rejected(self):
        with pytest.raises(ValueError, match='silence_threshold_s 는 None'):
            LapseEvent(
                variant=CognitiveLapseVariant.E1_SELF_CORRECTION,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance='follow up',
                silence_threshold_s=10.0,
                raw_c_after_event=0.9,
            )

    def test_e1_missing_raw_c_rejected(self):
        with pytest.raises(ValueError, match='raw_c_after_event 필수'):
            LapseEvent(
                variant=CognitiveLapseVariant.E1_SELF_CORRECTION,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance='follow up',
                silence_threshold_s=None,
                raw_c_after_event=None,
            )

    def test_raw_c_out_of_unit_interval_rejected(self):
        with pytest.raises(ValueError, match=r'\[0, 1\]'):
            LapseEvent(
                variant=CognitiveLapseVariant.E1_SELF_CORRECTION,
                trigger_time_s=5.0,
                initial_utterance='hello',
                follow_up_utterance='follow up',
                silence_threshold_s=None,
                raw_c_after_event=1.5,
            )

    def test_negative_trigger_time_rejected(self):
        with pytest.raises(ValueError, match='trigger_time_s'):
            LapseEvent(
                variant=CognitiveLapseVariant.E1_SELF_CORRECTION,
                trigger_time_s=-1.0,
                initial_utterance='hello',
                follow_up_utterance='follow up',
                silence_threshold_s=None,
                raw_c_after_event=0.9,
            )


# ----------------------------------------------------------- schema lock


class TestCognitiveLapseVariantSchema:
    def test_four_variants_locked(self):
        """ADR-0025 D5 amendment + S7 §3.2 — 4 variant 1:1 매핑."""
        names = {v.value for v in CognitiveLapseVariant}
        assert names == {
            'E1_self_correction',
            'E2_self_contradiction',
            'E3_explicit_cancel',
            'E4_utterance_cut',
        }


# ----------------------------------------------------------- 조사 보정 (C-9·C-10)


class TestJosaCorrection:
    """PR #99 self-review C-9·C-10 — 한국어 조사 받침 자동 보정."""

    def test_josa_helper_jongseong(self):
        """받침 있는 단어 측 has_jongseong 반환."""
        from eval_faults.cognitive_lapse import _josa
        # 머그컵 = 마지막 음절 '컵' 측 종성 ㅂ 있음
        assert _josa('머그컵', '으로', '로') == '으로'
        # 책 = 마지막 ㄱ 받침
        assert _josa('거실 탁자 위 책', '이', '가') == '이'

    def test_josa_helper_no_jongseong(self):
        """받침 없는 단어 측 no_jongseong 반환."""
        from eval_faults.cognitive_lapse import _josa
        # 의자 = 마지막 음절 '자' 측 종성 없음
        assert _josa('의자', '으로', '로') == '로'
        # 사과 = 마지막 음절 '과' 종성 없음
        assert _josa('사과', '이', '가') == '가'

    def test_josa_helper_non_hangul_fallback(self):
        """비-한글 (영어/숫자) 측 no_jongseong fallback."""
        from eval_faults.cognitive_lapse import _josa
        assert _josa('alpha', '으로', '로') == '로'
        assert _josa('', '이', '가') == '가'
        assert _josa('drone', '이', '가') == '가'

    def test_e1_no_jongseong_alt_natural_korean(self):
        """받침 없는 alt 측 E1 '{alt}로 바꿔.' 자연스러움 (받침 없음 → '로')."""
        ctx = CognitiveLapseContext(
            initial_target_id='init',
            initial_target_name_kr='초기 대상',
            alternative_target_id='alt',
            alternative_target_name_kr='의자',  # 종성 없음
        )
        found_natural = False
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E1_SELF_CORRECTION, ctx, random.Random(s),
            )
            if '의자로 바꿔.' in event.follow_up_utterance:
                found_natural = True
            # 어색 형태 '의자으로' 절대 등장 안 함
            assert '의자으로' not in event.follow_up_utterance, (
                f'어색 조사 — {event.follow_up_utterance}'
            )
        assert found_natural, "'의자로 바꿔.' template 미등장"

    def test_e1_jongseong_alt_natural_korean(self):
        """받침 있는 alt 측 E1 '{alt}으로 바꿔.' 자연스러움 (받침 있음 → '으로')."""
        ctx = CognitiveLapseContext(
            initial_target_id='init',
            initial_target_name_kr='초기 대상',
            alternative_target_id='alt',
            alternative_target_name_kr='식탁 위 머그컵',  # 종성 ㅂ
        )
        found_natural = False
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E1_SELF_CORRECTION, ctx, random.Random(s),
            )
            if '식탁 위 머그컵으로 바꿔.' in event.follow_up_utterance:
                found_natural = True
            # 어색 형태 '머그컵로' 절대 등장 안 함
            assert '머그컵로' not in event.follow_up_utterance, (
                f'어색 조사 — {event.follow_up_utterance}'
            )
        assert found_natural, "'머그컵으로 바꿔.' template 미등장"

    def test_e2_no_jongseong_alt_natural_korean(self):
        """받침 없는 alt 측 E2 — '{alt}가 먼저야.' + '{alt}로 안 가?' positive
        + 어색 형태 ('{alt}이', '{alt}으로') negative 양쪽 검증.
        """
        ctx = CognitiveLapseContext(
            initial_target_id='init',
            initial_target_name_kr='초기 대상',
            alternative_target_id='alt',
            alternative_target_name_kr='사과',  # 종성 없음
        )
        found_ga = False
        found_ro = False
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E2_SELF_CONTRADICTION, ctx, random.Random(s),
            )
            if '사과가 먼저야.' in event.follow_up_utterance:
                found_ga = True
            if '사과로 안 가?' in event.follow_up_utterance:
                found_ro = True
            # 어색 형태 절대 등장 안 함
            assert '사과으로' not in event.follow_up_utterance, (
                f'어색 조사 — {event.follow_up_utterance}'
            )
            assert '사과이 ' not in event.follow_up_utterance, (
                f'어색 조사 — {event.follow_up_utterance}'
            )
        assert found_ga, "'사과가 먼저야.' positive template 미등장"
        assert found_ro, "'사과로 안 가?' positive template 미등장"

    def test_e2_jongseong_alt_natural_korean(self):
        """받침 있는 alt 측 E2 — '{alt}이 먼저야.' + '{alt}으로 안 가?' positive
        + 어색 형태 ('{alt}가', '{alt}로') negative 양쪽 검증.
        """
        ctx = CognitiveLapseContext(
            initial_target_id='init',
            initial_target_name_kr='초기 대상',
            alternative_target_id='alt',
            alternative_target_name_kr='식탁 위 머그컵',  # 종성 ㅂ
        )
        found_i = False
        found_uro = False
        for s in range(200):
            event = apply_cognitive_lapse(
                CognitiveLapseVariant.E2_SELF_CONTRADICTION, ctx, random.Random(s),
            )
            if '머그컵이 먼저야.' in event.follow_up_utterance:
                found_i = True
            if '머그컵으로 안 가?' in event.follow_up_utterance:
                found_uro = True
            assert '머그컵로 ' not in event.follow_up_utterance, (
                f'어색 조사 — {event.follow_up_utterance}'
            )
            assert '머그컵가 ' not in event.follow_up_utterance, (
                f'어색 조사 — {event.follow_up_utterance}'
            )
        assert found_i, "'머그컵이 먼저야.' positive template 미등장"
        assert found_uro, "'머그컵으로 안 가?' positive template 미등장"
