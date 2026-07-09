"""Confidence Estimator 단위 테스트 — ADR-0020 (곱셈형 $g$ + rate limiter).

순수 함수 검증만. ROS 2 노드 wiring (PR A3-3 예정) 의 통합 테스트는 별 파일.
"""

from __future__ import annotations

import math

import pytest

from intent_confidence.estimator import (
    GInputs,
    _validate_si,
    compute_g,
    rate_limit_step,
)


# ====================================================================== compute_g

class TestComputeG:
    def test_all_one(self) -> None:
        """세 신호 모두 1 → c = 1 (정의역 상한)."""
        assert compute_g(GInputs(1.0, 1.0, 1.0)) == pytest.approx(1.0)

    def test_all_zero(self) -> None:
        assert compute_g(GInputs(0.0, 0.0, 0.0)) == 0.0

    def test_mid_values(self) -> None:
        """ADR-0020 Consequences 의 under-estimation 예시 — s_i=0.8 시 c=0.512."""
        assert compute_g(GInputs(0.8, 0.8, 0.8)) == pytest.approx(0.512)

    def test_one_signal_zero_yields_c_zero(self) -> None:
        """ADR-0020 D3 fail-safe-by-construction — 어느 한 신호라도 0 → c=0."""
        assert compute_g(GInputs(0.0, 0.9, 0.9)) == 0.0
        assert compute_g(GInputs(0.9, 0.0, 0.9)) == 0.0
        assert compute_g(GInputs(0.9, 0.9, 0.0)) == 0.0

    def test_monotonic_in_each_signal(self) -> None:
        """단조성: 한 신호 증가 → c 비감소 (다른 두 고정)."""
        base = compute_g(GInputs(0.5, 0.5, 0.5))
        assert compute_g(GInputs(0.8, 0.5, 0.5)) >= base
        assert compute_g(GInputs(0.5, 0.8, 0.5)) >= base
        assert compute_g(GInputs(0.5, 0.5, 0.8)) >= base

    def test_codomain_in_unit_interval(self) -> None:
        """정의역 보전 — 임의 $s_i \\in [0,1]$ → $c \\in [0,1]$."""
        for s1 in (0.0, 0.1, 0.5, 0.9, 1.0):
            for s2 in (0.0, 0.1, 0.5, 0.9, 1.0):
                for s3 in (0.0, 0.1, 0.5, 0.9, 1.0):
                    c = compute_g(GInputs(s1, s2, s3))
                    assert 0.0 <= c <= 1.0

    def test_absent_signal_fallback(self) -> None:
        """ADR-0020 D3 — s_i_absent=True 이면 입력 값 무관 c=0."""
        c = compute_g(GInputs(0.9, 0.9, 0.9, s1_absent=True))
        assert c == 0.0
        c = compute_g(GInputs(0.9, 0.9, 0.9, s2_absent=True))
        assert c == 0.0
        c = compute_g(GInputs(0.9, 0.9, 0.9, s3_absent=True))
        assert c == 0.0

    def test_absent_ignores_invalid_input(self) -> None:
        """absent=True 면 s_i 값이 정의역 밖이어도 거부 안 함 (값 무시)."""
        # absent=True 면 _validate_si 호출 자체 skip — NaN/-1 도 허용.
        c = compute_g(GInputs(float("nan"), 0.5, 0.5, s1_absent=True))
        assert c == 0.0

    def test_s3_structural_excluded_neutral(self) -> None:
        """ADR-0020 D8 — s3_structural=True → s3 곱 제외 (neutral) → c=s1·s2.

        edge 백본 (logprob 무능력): c = s1·s2, s3 placeholder(1.0) 무시.
        """
        c = compute_g(GInputs(1.0, 0.9, 0.135, s3_structural=True))
        assert c == pytest.approx(0.9)  # s1·s2, NOT s1·s2·s3 (=0.1215)
        # s3 값이 무엇이든 무관 (곱 제외).
        c2 = compute_g(GInputs(0.8, 0.5, 0.0, s3_structural=True))
        assert c2 == pytest.approx(0.4)

    def test_s3_structural_does_not_validate_s3(self) -> None:
        """구조적 제외 시 s3 값 검증 생략 — placeholder NaN/범위밖도 허용."""
        c = compute_g(GInputs(1.0, 0.7, float("nan"), s3_structural=True))
        assert c == pytest.approx(0.7)

    def test_cloud_s3_real_signal(self) -> None:
        """s3_structural=False (cloud) → c = s1·s2·s3 (실 logprob)."""
        c = compute_g(GInputs(1.0, 0.9, 0.8, s3_structural=False))
        assert c == pytest.approx(0.72)

    def test_runtime_absent_priority_over_structural(self) -> None:
        """런타임 부재(s3_absent)가 구조적 제외보다 우선 → c=0 (D8)."""
        c = compute_g(GInputs(1.0, 0.9, 0.0, s3_absent=True, s3_structural=True))
        assert c == 0.0

    def test_structural_with_s1_or_s2_absent_still_zero(self) -> None:
        """구조적 제외라도 s1/s2 부재면 여전히 c=0 (D3 fail-safe 불변)."""
        assert compute_g(GInputs(0.9, 0.9, 1.0, s1_absent=True, s3_structural=True)) == 0.0
        assert compute_g(GInputs(0.9, 0.9, 1.0, s2_absent=True, s3_structural=True)) == 0.0

    def test_invalid_dtype_bool(self) -> None:
        """bool 거부 (isinstance(True, int) == True 함정 회피)."""
        with pytest.raises(TypeError, match="bool"):
            compute_g(GInputs(True, 0.5, 0.5))  # type: ignore[arg-type]

    def test_invalid_dtype_str(self) -> None:
        with pytest.raises(TypeError):
            compute_g(GInputs("0.5", 0.5, 0.5))  # type: ignore[arg-type]

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValueError):
            compute_g(GInputs(float("nan"), 0.5, 0.5))

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValueError):
            compute_g(GInputs(float("inf"), 0.5, 0.5))

    def test_out_of_domain_above(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            compute_g(GInputs(1.5, 0.5, 0.5))

    def test_out_of_domain_below(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            compute_g(GInputs(-0.1, 0.5, 0.5))


# ====================================================================== _validate_si

class TestValidateSi:
    def test_valid_endpoints(self) -> None:
        assert _validate_si(0.0, "s") == 0.0
        assert _validate_si(1.0, "s") == 1.0

    def test_int_accepted(self) -> None:
        """파이썬 int 는 float-호환 — 1 → 1.0 OK."""
        assert _validate_si(1, "s") == 1.0
        assert _validate_si(0, "s") == 0.0


# ====================================================================== rate_limit_step

class TestRateLimitStep:
    def test_no_change_when_target_equals_prev(self) -> None:
        """c_raw == c_tilde_prev → 결과 변동 없음."""
        c = rate_limit_step(0.5, 0.5, dt=0.1, c_dot_max=1.0)
        assert c == pytest.approx(0.5)

    def test_clamp_up_when_step_capped(self) -> None:
        """c_raw 가 prev 보다 훨씬 크고 dt·c_dot_max < 차이 → clamp."""
        # delta target = 0.8 - 0.0 = 0.8. max step = 0.1 * 1.0 = 0.1. 결과 = 0.1.
        c = rate_limit_step(0.8, 0.0, dt=0.1, c_dot_max=1.0)
        assert c == pytest.approx(0.1)

    def test_clamp_down_when_step_capped(self) -> None:
        # delta = 0.0 - 0.8 = -0.8. min step = -0.1. 결과 = 0.7.
        c = rate_limit_step(0.0, 0.8, dt=0.1, c_dot_max=1.0)
        assert c == pytest.approx(0.7)

    def test_reaches_target_when_step_large_enough(self) -> None:
        """dt · c_dot_max ≥ |Δ| 면 한 step 에 target 도달."""
        c = rate_limit_step(0.8, 0.0, dt=1.0, c_dot_max=1.0)
        assert c == pytest.approx(0.8)

    def test_idempotent_at_steady_state(self) -> None:
        """c_raw 가 prev 와 같으면 호출 횟수에 무관 동일 값 (idempotency)."""
        c_prev = 0.5
        for _ in range(10):
            c_prev = rate_limit_step(0.5, c_prev, dt=0.1, c_dot_max=1.0)
        assert c_prev == pytest.approx(0.5)

    def test_monotone_convergence_to_target(self) -> None:
        """cmsm-proof §6 — 시변 $\\tilde c(t)$ 가 target 으로 단조 수렴.

        Step 마다 |c_raw - c_tilde| 가 비증가. 충분히 많은 step 후 target 도달.
        """
        c_target = 1.0
        c_prev = 0.0
        last_dist = c_target - c_prev
        for _ in range(50):
            c_new = rate_limit_step(c_target, c_prev, dt=0.05, c_dot_max=1.0)
            new_dist = abs(c_target - c_new)
            assert new_dist <= last_dist + 1e-12
            last_dist = new_dist
            c_prev = c_new
        # 충분히 step 돌아 target 근처 도달 (0.05 * 1.0 * 50 = 2.5 ≥ 1.0).
        assert c_prev == pytest.approx(1.0)

    def test_codomain_clipped_to_unit_interval(self) -> None:
        """결과는 항상 [0, 1] — 입력이 정의역 안이면 자동 보장."""
        for c_raw in (0.0, 0.5, 1.0):
            for c_prev in (0.0, 0.5, 1.0):
                c = rate_limit_step(c_raw, c_prev, dt=0.5, c_dot_max=2.0)
                assert 0.0 <= c <= 1.0

    def test_step_size_proportional_to_dt_and_c_dot_max(self) -> None:
        """dt 또는 c_dot_max 가 2 배 → max_step 2 배."""
        # base: dt=0.1, c_dot_max=1.0 → max_step = 0.1. Δtarget = 0.5.
        c1 = rate_limit_step(0.5, 0.0, dt=0.1, c_dot_max=1.0)
        # dt 2배 → max_step=0.2. Δtarget = 0.5 → clamp 여전히 작동.
        c2 = rate_limit_step(0.5, 0.0, dt=0.2, c_dot_max=1.0)
        # c_dot_max 2배 → max_step=0.2.
        c3 = rate_limit_step(0.5, 0.0, dt=0.1, c_dot_max=2.0)
        assert c1 == pytest.approx(0.1)
        assert c2 == pytest.approx(0.2)
        assert c3 == pytest.approx(0.2)

    def test_invalid_dt_zero(self) -> None:
        with pytest.raises(ValueError, match="dt"):
            rate_limit_step(0.5, 0.3, dt=0.0, c_dot_max=1.0)

    def test_invalid_dt_negative(self) -> None:
        with pytest.raises(ValueError, match="dt"):
            rate_limit_step(0.5, 0.3, dt=-0.1, c_dot_max=1.0)

    def test_invalid_c_dot_max_zero(self) -> None:
        with pytest.raises(ValueError, match="c_dot_max"):
            rate_limit_step(0.5, 0.3, dt=0.1, c_dot_max=0.0)

    def test_invalid_c_dot_max_negative(self) -> None:
        with pytest.raises(ValueError, match="c_dot_max"):
            rate_limit_step(0.5, 0.3, dt=0.1, c_dot_max=-1.0)

    def test_invalid_c_raw_out_of_domain(self) -> None:
        with pytest.raises(ValueError, match="c_raw"):
            rate_limit_step(1.5, 0.5, dt=0.1, c_dot_max=1.0)

    def test_invalid_c_tilde_prev_out_of_domain(self) -> None:
        with pytest.raises(ValueError, match="c_tilde_prev"):
            rate_limit_step(0.5, -0.1, dt=0.1, c_dot_max=1.0)

    def test_invalid_dtype_bool(self) -> None:
        with pytest.raises(TypeError):
            rate_limit_step(True, 0.5, dt=0.1, c_dot_max=1.0)  # type: ignore[arg-type]


# ====================================================================== 통합 흐름

class TestPipelineIntegration:
    """compute_g + rate_limit_step 조합 시 정형 성질 입증."""

    def test_signal_absent_drives_c_tilde_to_zero_over_time(self) -> None:
        """ADR-0020 D3 *동역학 주의* — s1 부재 → c_raw=0 → c̃ 가 유한 시간에 0 으로 수렴.

        cmsm-proof §6 의 단조 수렴 성질이 D3 fallback 과 결합돼 안전한 쪽으로 자연 감쇠.
        """
        c_tilde = 0.9  # 시작 상태 (높은 신뢰도)
        dt = 0.1
        c_dot_max = 1.0
        # 신호 부재 발생 — c_raw = 0.
        c_raw_absent = compute_g(GInputs(0.9, 0.9, 0.9, s1_absent=True))
        assert c_raw_absent == 0.0
        # 시간이 지나면서 c̃ 가 0 으로 수렴.
        steps_to_zero = 0
        while c_tilde > 1e-6 and steps_to_zero < 100:
            c_tilde = rate_limit_step(c_raw_absent, c_tilde, dt=dt, c_dot_max=c_dot_max)
            steps_to_zero += 1
        assert c_tilde == pytest.approx(0.0, abs=1e-6)
        # 0.9 / (c_dot_max · dt) = 9 step 이론 → 정확히 9 step 만에 0 도달.
        assert steps_to_zero == 9

    def test_high_to_low_signal_transition(self) -> None:
        """raw 신호가 high → low 로 변화 시 c̃ 가 점진적으로 따라감."""
        c_tilde = 0.0
        dt = 0.1
        c_dot_max = 1.0
        # 초반: 고신뢰도 (수렴 단계).
        for _ in range(20):
            c_raw = compute_g(GInputs(0.9, 0.9, 0.9))  # = 0.729
            c_tilde = rate_limit_step(c_raw, c_tilde, dt=dt, c_dot_max=c_dot_max)
        assert c_tilde == pytest.approx(0.729, abs=1e-6)
        # 후반: 저신뢰도 (수렴 단계).
        for _ in range(20):
            c_raw = compute_g(GInputs(0.3, 0.3, 0.3))  # = 0.027
            c_tilde = rate_limit_step(c_raw, c_tilde, dt=dt, c_dot_max=c_dot_max)
        assert c_tilde == pytest.approx(0.027, abs=1e-6)
