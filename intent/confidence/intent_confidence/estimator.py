"""Confidence Estimator — 곱셈형 $g$ + 변화율 제한기 (rate limiter) pure functions.

ADR-0020 정형 잠금 ([decisions/0020-confidence-estimator-g-form-lock.md]):

- D1: $c = g(s_1, s_2, s_3) := s_1 \\cdot s_2 \\cdot s_3$, $s_i \\in [0, 1]$.
- D2: 정의역 $g : [0, 1]^3 \\to [0, 1]$. 정규화는 *호출자* 책임.
- D3: 신호 부재 시 $s_i := 0$ — fail-safe-by-construction.
- D4: rate limiter = cmsm-proof §6 그대로 ($|\\dot{\\tilde c}| \\leq \\dot c_\\text{max}$).

본 모듈은 *pure function 만* — 상태 (이전 $\\tilde c$, time stamp 등) 는 *호출자* 가
관리. ROS 2 노드 (PR A3-3 예정) 가 본 함수들을 wiring.

cmsm-proof §2.1 (raw 신호) / §6 (시변 $\\tilde c$ 전방불변성 정리) / §10.5 CA-3
(modality 가용성) cross-ref.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GInputs:
    """곱셈형 $g$ 의 세 신호 입력 + 진단 채널.

    cmsm-proof §2.1 의 세 raw 신호. 각 $s_i \\in [0, 1]$.

    Args:
        s1: 의미 접지 집중도 $= 1 - H$. OVD detection 분포의 엔트로피 보수.
        s2: LLM K-shot sampling 자기일관성 마진 $= (n_1 - n_2) / K$.
        s3: 응답 토큰 로그우도 기하평균 $= \\exp(\\overline{\\log p_t})$.
        s1_absent / s2_absent / s3_absent: ADR-0020 D3 *동역학 주의* — 입력
            *부재* 와 *낮은 confidence* 를 paper §C 분석 시 분리하기 위한 진단
            채널. 안전 처분은 부재 = 0 으로 동일 처리되어 무영향.
        s3_structural: ADR-0020 D8 — s3 *구조적* 부재 (백본 logprob 무능력,
            edge ollama). True 면 곱에서 s3 를 제외(neutral=1) → $c = s_1 s_2$.
            *런타임* 부재(s3_absent)와 구별 — 런타임은 $c=0$ fail-safe, 구조적은
            능력 한계라 영구 0 이 아니어야 함(C1 LLM-불가지 보전). s3_absent 가
            우선 (런타임 부재면 구조적 제외 무시하고 0).
    """

    s1: float
    s2: float
    s3: float
    s1_absent: bool = False
    s2_absent: bool = False
    s3_absent: bool = False
    s3_structural: bool = False


def _validate_si(s: float, name: str) -> float:
    """단일 신호 $s_i$ 검증 + ADR-0020 D3 부재 → 0 fallback.

    Raises:
        TypeError: s 가 float-호환 아님 (bool 제외 — bool 은 int 의 subclass 라
            isinstance(True, int) 가 True 인 함정 회피).
        ValueError: s 가 NaN / inf / $[0, 1]$ 밖.
    """
    if isinstance(s, bool):
        raise TypeError(f"{name} 는 float — bool 거부 (의도 모호): {s!r}")
    if not isinstance(s, (int, float)):
        raise TypeError(f"{name} 는 float 이어야 함: {s!r}")
    s_f = float(s)
    if math.isnan(s_f) or math.isinf(s_f):
        raise ValueError(f"{name} 는 유한 실수: {s_f}")
    if not (0.0 <= s_f <= 1.0):
        raise ValueError(f"{name} 는 [0, 1]: {s_f}")
    return s_f


def compute_g(inputs: GInputs) -> float:
    """곱셈형 $g$ — ADR-0020 D1 + D8.

    $$c = g(s_1, s_2, s_3) := s_1 \\cdot s_2 \\cdot s_3 \\in [0, 1]$$

    D3 fail-safe: 어느 한 신호라도 *런타임 부재* (`s_i_absent=True`) 면 해당
    $s_i := 0$ 으로 환원되어 자동 $c = 0$.

    D8 구조적 제외: `s3_structural=True` (백본 logprob 무능력, edge ollama) 면
    s3 를 곱에서 *제외*(neutral=1) → $c = s_1 \\cdot s_2$. 단 *런타임* 부재
    (`s3_absent=True`)가 우선 — 그 경우 구조적 제외 무시하고 $s_3 := 0$ → $c = 0$.
    s1·s2 의 D3 fail-safe 는 구조적 제외와 무관하게 유지 (둘 중 하나라도 0 이면 $c=0$).

    Args:
        inputs: ``GInputs`` (s1, s2, s3 ∈ [0,1] + 부재 플래그 + s3_structural).

    Returns:
        $c \\in [0, 1]$.

    Raises:
        TypeError / ValueError: ``GInputs`` 의 각 신호가 정의역·dtype 위반 시
            (단 구조적 제외된 s3 는 검증 생략 — placeholder 값).
    """
    s1 = 0.0 if inputs.s1_absent else _validate_si(inputs.s1, "s1")
    s2 = 0.0 if inputs.s2_absent else _validate_si(inputs.s2, "s2")
    # D8 — s3 구조적 부재(런타임 부재 아님): 곱에서 제외 (neutral=1).
    if inputs.s3_structural and not inputs.s3_absent:
        return s1 * s2
    s3 = 0.0 if inputs.s3_absent else _validate_si(inputs.s3, "s3")
    return s1 * s2 * s3


def rate_limit_step(
    c_raw: float,
    c_tilde_prev: float,
    dt: float,
    c_dot_max: float,
) -> float:
    """변화율 제한기 한 step — cmsm-proof §6 / ADR-0020 D4.

    $$\\tilde c(t + dt) = \\tilde c(t) + \\text{clamp}(c_\\text{raw} - \\tilde c(t),\\ -\\dot c_\\text{max} \\cdot dt,\\ +\\dot c_\\text{max} \\cdot dt)$$

    즉 $|\\dot{\\tilde c}| \\leq \\dot c_\\text{max}$ 보장. cmsm-proof §6 의 시변
    $\\tilde c(t)$ 전방불변성 정리가 이 step 의 *누적* 동역학에 적용됨.

    Args:
        c_raw: 현 step 의 raw $c$ (compute_g 출력 또는 외부 신호). $[0, 1]$.
        c_tilde_prev: 직전 step 의 $\\tilde c$. $[0, 1]$ (초기값 호출자 책임).
        dt: time step (초, $> 0$).
        c_dot_max: 변화율 상한 $\\dot c_\\text{max}$ (단위: $\\text{s}^{-1}$, $> 0$).
            시나리오마다 결정 (ROADMAP §6 C11).

    Returns:
        새 $\\tilde c \\in [0, 1]$.

    Raises:
        ValueError: dt 또는 c_dot_max 가 양수 아님, c_raw / c_tilde_prev 가
            $[0, 1]$ 밖.

    Notes:
        - $dt \\cdot \\dot c_\\text{max}$ 가 $|c_\\text{raw} - \\tilde c_\\text{prev}|$
          이상이면 clamp 안 걸리고 $\\tilde c = c_\\text{raw}$ 한 step 에 도달.
        - 결과는 $[0, 1]$ 으로 한 번 더 clip — c_raw 가 정의역 안이고 c_tilde_prev
          가 정의역 안이면 clamp 후 항상 $[0, 1]$ 이지만 부동소수 안전을 위해.
    """
    if not isinstance(c_raw, (int, float)) or isinstance(c_raw, bool):
        raise TypeError(f"c_raw 는 float: {c_raw!r}")
    if not isinstance(c_tilde_prev, (int, float)) or isinstance(c_tilde_prev, bool):
        raise TypeError(f"c_tilde_prev 는 float: {c_tilde_prev!r}")
    if not isinstance(dt, (int, float)) or isinstance(dt, bool):
        raise TypeError(f"dt 는 float: {dt!r}")
    if not isinstance(c_dot_max, (int, float)) or isinstance(c_dot_max, bool):
        raise TypeError(f"c_dot_max 는 float: {c_dot_max!r}")

    c_raw_f = float(c_raw)
    c_tilde_prev_f = float(c_tilde_prev)
    dt_f = float(dt)
    c_dot_max_f = float(c_dot_max)

    if not (0.0 <= c_raw_f <= 1.0):
        raise ValueError(f"c_raw 는 [0, 1]: {c_raw_f}")
    if not (0.0 <= c_tilde_prev_f <= 1.0):
        raise ValueError(f"c_tilde_prev 는 [0, 1]: {c_tilde_prev_f}")
    if not (dt_f > 0.0):
        raise ValueError(f"dt 는 양수: {dt_f}")
    if not (c_dot_max_f > 0.0):
        raise ValueError(f"c_dot_max 는 양수: {c_dot_max_f}")

    delta = c_raw_f - c_tilde_prev_f
    max_step = c_dot_max_f * dt_f
    if delta > max_step:
        delta = max_step
    elif delta < -max_step:
        delta = -max_step

    new_c = c_tilde_prev_f + delta
    # [0, 1] 안전 clip — 입력이 정의역 안이라면 무영향. 부동소수 잔차 보호.
    if new_c < 0.0:
        new_c = 0.0
    elif new_c > 1.0:
        new_c = 1.0
    return new_c
