r"""live 모드 estimator 신호 소스 — pure 파싱·변환 로직 (rclpy 무관).

[ADR-0020 Amendment (2026-05-31)](../../../docs/handover/decisions/0020-confidence-estimator-g-form-lock.md)
의 *live* 모드 입력 source. estimator_node 가 ROS 메시지에서 추출한 *plain 값* 을
본 함수들에 넣어 세 신호 $s_1, s_2, s_3$ 를 산출한다. host venv 에서 rclpy 없이
단위 테스트 가능 (synthesis 모드의 `signal_scenario.py` 와 동일한 분리 원칙).

신호 경로 (ADR-0020 Amendment Interface contract):
  - $s_1$ ← `/intent/ovd/detections` (Detection2DArray) 의 referent 매칭 후보
    confidence 분포 → `grounding` 의 $1 - H$. referent 는 LLM 출력 `theta.target_id`
    ([ADR-0027 D9](../../../docs/handover/decisions/0027-intent-output-schema-grounding.md)
    정합 — LLM 은 좌표 아닌 의미 선택만 출력).
  - $s_2$ ← `/intent/llm_sigma_raw` 의 `signals['s2_self_consistency']` ($\rho$).
  - $s_3$ ← 같은 토픽의 `signals['s3_logprob']` — wrapper 는 *raw* 로그우도 평균
    $\overline{\log p_t}$ 를 발행한다. 정본 $s_3 = \ell = \exp(\overline{\log p_t})
    \in [0,1]$ ([cmsm-proof §2.1](../../../paper/cmsm-proof.md)) 이므로 본 모듈이
    $\exp$ 후 $[0,1]$ clamp 해 신호로 변환한다 ([ADR-0020 amendment 2026-06-11
    — s3 정규화](../../../docs/handover/decisions/0020-confidence-estimator-g-form-lock.md)).

fail-safe (ADR-0020 D3): 어느 신호든 *부재* → 해당 $s_i$ absent → estimator
측 $c = 0$. 부재 사유:
  - $s_1$: OVD detection 0 개 · 토픽 stale(미수신) · referent 미상(direction
    명령 등 target_id 없음) · referent 매칭 후보 0 개.
  - $s_2$: sigma_raw 토픽 stale·latch 만료 · `signals` 에 키 부재·범위 밖.
  - $s_3$: 키 부재 · 비수치·NaN/inf (`s3_logprob` 가 logprob 이므로 음수도 유효 —
    범위 검사 없이 $\exp$ 후 clamp). 단 이는 *런타임* 부재(가용한데 이번 호출만
    누락)에 한함.

s3 구조적 부재 (ADR-0020 D8): edge 백본(ollama)은 token logprob 을 *원천적으로*
못 내므로 wrapper 가 `s3_capability: false` 를 명시 발행한다. 종전 sentinel
$-2.0 \to \exp \to 0.135$ *상수* 천장 경로는 폐기. 본 모듈은 capability 플래그를
읽어 구조적 부재면 `s3_structural=True` 로 표지하고 $s_3$ 를 neutral placeholder(1.0)
로 둔다 — 소비자(estimator `compute_g`)가 곱에서 *제외* → edge $c = s_1 \cdot s_2$
로 정상 변조 (C1 LLM-불가지 보전). cloud(gpt-4o)는 `s3_capability: true` + 실 logprob.
플래그 부재(구버전 payload) → True 가정(가용) → 런타임 경로.

referent latch (ADR-0020 amendment 2026-06-11 — 발견 A): LLM 의도(sigma_raw)는
*발화당 1회* 이벤트인 반면 OVD detection 은 연속이다. estimator 가 sigma 를 OVD 와
같은 짧은 timeout 으로 stale 처리하면 발화 직후 referent 가 사라져 본실험 sweep 에서
$c$ 가 의도 갱신 순간만 살아있다. `resolve_active_sigma` 가 *활성 명령* (마지막
sigma) 을 새 sigma 로 대체될 때까지 (또는 선택적 TTL) latch 유지한다 — OVD 의
연속 staleness 와 분리.

C12 위치 수식어 disambiguation(anchor) 은 후속 — 현재 label-only 매칭
(`weighted_referent_scores` 에 anchor=None).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from intent_confidence.grounding import s1_from_scores, weighted_referent_scores

# intent_llm.interface 의 SIGNAL_* 키와 정합. intent_confidence → intent_llm
# 패키지 의존을 만들지 않기 위해 문자열을 복제 (값이 바뀌면 양쪽 동기 — interface.py
# 의 VALID_SIGNAL_KEYS 가 단일 진실 소스, 본 상수는 그 미러).
SIGNAL_SELF_CONSISTENCY = 's2_self_consistency'  # rho — LLM OVD 후보 인덱스 self-consistency
SIGNAL_LOGPROB = 's3_logprob'                    # ell — 토큰 로그확률 기하평균
SIGNAL_S3_CAPABILITY = 's3_capability'           # bool — s3 구조적 능력 (ADR-0020 D8)


@dataclass(frozen=True)
class DetectionCandidate:
    """grounding 함수가 기대하는 duck-typed 후보 (class_label/confidence/position/bbox).

    estimator_node 가 vision_msgs/Detection2D 를 본 dataclass 로 변환해 넘긴다.

    ``bbox`` (ADR-0040 D7): 이미지 픽셀 박스 ``(cx, cy, w, h)``. 동일 라벨 중복박스
    dedup(grounding.dedup_overlapping_candidates)용. None 이면 dedup 대상에서 제외
    (graceful — bbox 없는 후보는 병합 판단 불가, 결함 주입 등 publisher 호환).
    """

    class_label: str
    confidence: float
    position: Optional[tuple] = None
    bbox: Optional[tuple] = None


def sanitize_detection_score(score: float) -> tuple[float, bool]:
    """OVD detection score 위생 처리 — 비유한값 복구 + $[0, 1]$ clamp.

    OVD 자체 출력(`intent_ovd.detector.Detection`)은 생성 시 도메인 검증하지만,
    estimator 가 구독하는 Detection2DArray 토픽엔 결함 주입기 등 다른 publisher
    도 쓸 수 있다. 비유한(NaN/Inf) score 가 ``compute_g`` 도메인 검증까지
    흘러가면 timer 콜백 예외 → 노드 사망 경로 — ingestion 에서 차단한다
    (2026-06-12 세션 34 전체 리뷰 후속; tier1 ``sanitize_confidence`` 와 동일 정책).

    Returns:
        ``(score, finite)`` — ``score`` 는 $[0, 1]$ 보장값. ``finite`` 가 False 면
        입력이 NaN/Inf 여서 0.0(보수)으로 복구했다는 뜻 (호출자 로깅용).
    """
    s = float(score)
    if not math.isfinite(s):
        return 0.0, False
    return max(0.0, min(1.0, s)), True


@dataclass(frozen=True)
class S1Result:
    """$s_1$ 산출 결과 + 진단 (ADR-0020 D3 — 부재 사유 분리 보고)."""

    s1: float
    absent: bool
    reason: str          # 'ok' | 'no_detections' | 'no_referent' | 'no_match'
    n_matched: int       # referent 에 매칭된 후보 수
    n_detections: int    # 전체 detection 수


@dataclass(frozen=True)
class ParsedSigma:
    """sigma_raw JSON 파싱 결과 — $s_2, s_3$ + referent (target_id).

    $s_3$ 는 이미 $\\exp(\\overline{\\log p_t})$ 정규화된 $[0,1]$ 값 (raw logprob 아님).
    `s3_structural` (ADR-0020 D8) 이면 백본이 logprob 을 *원천적으로* 못 내는 경우
    (edge ollama) — $s_3$ 값은 neutral placeholder (1.0) 이고 소비자가 곱에서 제외한다.
    """

    s2: float
    s3: float
    s2_absent: bool
    s3_absent: bool
    referent_labels: tuple   # theta.target_id → grounding referent (없으면 빈 tuple)
    parse_ok: bool           # JSON 파싱 성공 여부
    s3_structural: bool = False  # s3 구조적 부재 (백본 무능력) → 곱 제외(neutral)


@dataclass(frozen=True)
class ActiveSigma:
    """latch 해소 결과 — 현재 tick 에서 estimator 가 소비할 sigma 신호 상태.

    `resolve_active_sigma` 가 산출. 활성 명령(latch 된 sigma)이 있으면 그 $s_2, s_3$·
    referent 를, 없으면(미수신·TTL 만료) 부재 신호를 담는다.
    """

    s2: float
    s3: float
    s2_absent: bool
    s3_absent: bool
    referent_labels: tuple
    latched: bool        # 활성 sigma 존재 여부
    age_s: float         # 마지막 sigma 수신 후 경과 [s] (-1 = 미수신)
    s3_structural: bool = False  # s3 구조적 부재 전파 (ADR-0020 D8)


def compute_s1(
    detections: Sequence[DetectionCandidate],
    referent_labels: Sequence[str],
    anchor=None,
    sigma_m: Optional[float] = None,
) -> S1Result:
    r"""OVD 후보 + referent → $s_1 = 1 - H$ (ADR-0020 Amendment $s_1$ 경로).

    Args:
        detections: 현재 프레임의 OVD 후보들 (``DetectionCandidate``).
        referent_labels: LLM ``theta.target_id`` 에서 온 referent class label 들.
            빈 시퀀스면 referent 미상 (direction 명령 등) → absent.
        anchor: 위치 수식어 referent 의 world 좌표 (C12 후속 — 현재 None).
        sigma_m: anchor 거리 가중 스케일. None 이면 grounding 기본값.

    Returns:
        ``S1Result`` — $s_1$ + 부재 플래그/사유/카운트.

    fail-safe (ADR-0020 D3): detection 0 · referent 미상 · 매칭 0 → absent
    (estimator 측 $c = 0$).
    """
    n_det = len(detections)
    if n_det == 0:
        return S1Result(0.0, True, 'no_detections', 0, 0)

    labels = [str(x).strip() for x in referent_labels if str(x).strip()]
    if not labels:
        # referent 미상 (direction 명령 등 target_id 없음) — grounding 모호성 정의
        # 불가 → fail-safe absent (ADR-0020 D3 정합).
        return S1Result(0.0, True, 'no_referent', 0, n_det)

    if sigma_m is not None:
        scores = weighted_referent_scores(detections, labels, anchor, sigma_m)
    else:
        scores = weighted_referent_scores(detections, labels, anchor)

    if not scores:
        return S1Result(0.0, True, 'no_match', 0, n_det)

    return S1Result(s1_from_scores(scores), False, 'ok', len(scores), n_det)


def _coerce_signal(value) -> Optional[float]:
    """[0,1] 신호 값 → float | None (None·비수치·범위 밖 → None = absent).

    $s_2$ ($\\rho$) 처럼 이미 $[0,1]$ 로 정의된 신호 전용.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 은 의도 모호 — 부재 취급
        return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if f != f or f in (float('inf'), float('-inf')):  # NaN/inf
        return None
    if not (0.0 <= f <= 1.0):
        return None
    return f


def _coerce_logprob(value) -> Optional[float]:
    r"""raw 로그우도 평균 → $s_3 = \mathrm{clamp}_{[0,1]}(\exp(\overline{\log p_t}))$.

    `s3_logprob` 는 *logprob* (보통 음수) 이므로 범위 검사 없이 $\exp$ 후 $[0,1]$
    clamp. None·비수치·NaN/inf → None (absent). 양의 logprob(이론상 비정상)도
    $\exp > 1$ → clamp 1.0 으로 수용. (ADR-0020 amendment 2026-06-11.)
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if f != f or f in (float('inf'), float('-inf')):  # NaN/inf
        return None
    try:
        e = math.exp(f)
    except OverflowError:  # 비정상적 큰 양수 logprob → exp 발산 → clamp 1.0
        return 1.0
    return max(0.0, min(1.0, e))


def parse_sigma_raw(json_str: str) -> ParsedSigma:
    r"""sigma_raw JSON ({sigma, theta, c, signals}) → $s_2, s_3$ + referent.

    [wrapper_payload](../../llm/intent_llm/wrapper_payload.py) 의 직렬화 스키마 정합.

    Args:
        json_str: ``/intent/llm_sigma_raw`` String 메시지 본문.

    Returns:
        ``ParsedSigma``. 파싱 실패·키 부재·값 무효 → 해당 항목 absent / 빈 referent.
    """
    try:
        payload = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return ParsedSigma(0.0, 0.0, True, True, (), False)
    if not isinstance(payload, dict):
        return ParsedSigma(0.0, 0.0, True, True, (), False)

    signals = payload.get('signals')
    if not isinstance(signals, dict):
        signals = {}
    s2 = _coerce_signal(signals.get(SIGNAL_SELF_CONSISTENCY))

    # ADR-0020 D8 — s3 *구조적* 능력 플래그. 키 부재 → True(가용 가정, backward
    # compat). 정확히 False(bool) 면 백본 logprob 무능력(edge) → 곱에서 제외.
    s3_structural = signals.get(SIGNAL_S3_CAPABILITY, True) is False
    if s3_structural:
        # 구조적 부재 — logprob 값 무시, neutral placeholder(1.0), 런타임 부재 아님.
        s3_val, s3_absent = 1.0, False
    else:
        s3 = _coerce_logprob(signals.get(SIGNAL_LOGPROB))  # logprob → exp(·) ∈ [0,1]
        s3_val, s3_absent = (0.0 if s3 is None else s3), (s3 is None)

    theta = payload.get('theta')
    referent: tuple = ()
    if isinstance(theta, dict):
        # 지시 대상 매칭은 *클래스* 기준 (ADR-0029 블로커 1) — 인스턴스 id(target_id,
        # 예 'chair_left')는 검출기 출력 클래스('chair')와 입도가 달라 완전일치가
        # 안 됨(no_match → s1=0). wrapper 가 σ.theta.target_class 로 OVD 클래스를
        # 실어 보내면 그것을 referent 로 우선 사용 → 동일 클래스 검출 다수 시 분포
        # 엔트로피가 s1 에 반영(C2 모호성 신호). 부재 시 target_id 로 폴백
        # (backward compat / 클래스 미상).
        target_class = theta.get('target_class')
        target_id = theta.get('target_id')
        if isinstance(target_class, str) and target_class.strip():
            referent = (target_class,)
        elif isinstance(target_id, str) and target_id.strip():
            referent = (target_id,)

    return ParsedSigma(
        s2=0.0 if s2 is None else s2,
        s3=s3_val,
        s2_absent=s2 is None,
        s3_absent=s3_absent,
        referent_labels=referent,
        parse_ok=True,
        s3_structural=s3_structural,
    )


def resolve_active_sigma(
    latest_sigma: Optional[ParsedSigma],
    sigma_age_ns: Optional[int],
    latch_timeout_ns: int,
) -> ActiveSigma:
    r"""latch 해소 — 현재 활성 명령(sigma)의 $s_2, s_3$·referent 산출 (발견 A).

    LLM 의도(sigma_raw)는 발화당 1회 이벤트이므로 OVD 연속 staleness 와 분리해
    *새 sigma 로 대체될 때까지* latch 유지한다. estimator timer 가 매 tick 호출.

    Args:
        latest_sigma: 마지막 수신 ``ParsedSigma`` (미수신이면 None).
        sigma_age_ns: 마지막 sigma 수신 후 경과 [ns] (미수신이면 None).
        latch_timeout_ns: latch TTL [ns]. **0 이하 = 무한(대체까지 지속)**.
            양수면 그 시간 경과 후 만료 → 부재.

    Returns:
        ``ActiveSigma`` — latch 활성 시 sigma 값, 비활성(미수신·만료) 시 부재 신호.

    fail-safe: 미수신(None) 또는 TTL 만료 → $s_2, s_3$ absent + 빈 referent →
    estimator 측 $c = 0$ (ADR-0020 D3).
    """
    if latest_sigma is None or sigma_age_ns is None:
        # 미수신 — *런타임* 부재 (구조적 아님): s3_structural=False → c=0.
        return ActiveSigma(0.0, 0.0, True, True, (), latched=False, age_s=-1.0)

    age_s = sigma_age_ns * 1e-9
    expired = latch_timeout_ns > 0 and sigma_age_ns > latch_timeout_ns
    if expired:
        return ActiveSigma(0.0, 0.0, True, True, (), latched=False, age_s=age_s)

    return ActiveSigma(
        s2=latest_sigma.s2,
        s3=latest_sigma.s3,
        s2_absent=latest_sigma.s2_absent,
        s3_absent=latest_sigma.s3_absent,
        referent_labels=latest_sigma.referent_labels,
        latched=True,
        age_s=age_s,
        s3_structural=latest_sigma.s3_structural,
    )


# ---------------------------------------------------------------------------
# grounding 시점 s1 latch (ADR-0029 블로커 2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroundedS1:
    """grounding 시점 s1 latch 적용 결과."""

    s1: float
    absent: bool
    reason: str          # 'ok' | 'latched' | live reason ('stale'|'no_detections'|...)
    n_matched: int
    n_detections: int


def resolve_grounded_s1(
    live: S1Result,
    sigma_active: bool,
    command_key,
    latch: Optional[tuple],
    now_ns: int = 0,
    freeze_window_ns: int = 0,
    min_persist_frames: int = 1,
) -> tuple:
    r"""grounding 시점 s1 latch — 명령(referent) 단위로 grounding 성공 s1 을 유지.

    카메라가 동체 고정이라 inspect 이동 중 대상이 FOV 를 벗어나면 OVD 가 끊겨 live
    s1 이 부재(stale/no_detections)로 떨어진다 (ADR-0029 블로커 2 — 발견 C). 그러나
    c 는 *명령 해석 시점의 grounding 신뢰도* 이므로, 같은 명령(``command_key`` 동일)에
    대해 grounding 성공('ok') s1 을 freeze 하고 이후엔 그 값을 유지한다. latch *파기*
    는 referent 변경(``command_key`` 변경 = 진짜 새 명령) 시로 한정한다 — σ 비활성
    tick 에서는 latch 를 소비하지 않되(live 반환) *보존* 한다 (아래 참조).

    **command_key (ADR-0040 Phase 2)**: latch 동일성 키. 종전엔 σ 수신 stamp 였으나,
    같은 발화의 σ 가 ~6s 주기로 *재발행*(새 stamp)되면 latch 가 매번 리셋돼 360° 검색
    스윕 중 grounding 이 끊겼다(세션 60 진단 — c 붕괴). 따라서 키를 **referent 동일성**
    (referent label 튜플)으로 잡아, *같은 referent* 의 σ 재발행에는 latch 를 유지하고
    *다른 referent*(진짜 새 명령)에만 재 grounding 한다. 호출자가 referent 식별자를 넘긴다.

    **σ 비활성 시 latch 보존 (세션 62 진단 — PR #297 referent-key 취지의 완성)**:
    같은 발화의 σ 재발행이 inspect 경로를 다시 타면 sigma_bridge 가
    ``_set_grounding_gate(False)`` 를 재발행하고, estimator 는 gate 닫힘을
    ``sigma_active=False`` 로 취급한다. 종전엔 이 tick 에서 latch 를 *파기* 해
    같은 referent 의 grounding 이력이 소실됐다 (gemma S5 trial c 영구 붕괴 1건
    실측). referent-key latch 의 취지대로 파기는 *referent 변경* 시로 한정하고,
    σ 비활성 tick 에서는 latch 를 보존한다(소비는 안 함 — live 부재 반환 → c 보수
    방향). 안전 방향: latch 보존은 c 를 유지시키는 쪽일 뿐이며 최소 안전 마진
    $r_\text{min}$ 은 결정론적으로 불변 — 안전 성질 무영향. referent 가 실제로
    바뀌면 (활성 tick 에서 ``command_key`` 불일치) 즉시 파기된다.

    **freeze 인 이유**: 드론이 inspect 로 한 대상에 *접근* 하면 시야 후보가 줄어 live
    s1 이 1.0 으로 오르는데, 이는 *사용자 명료화* 가 아니라 *드론 이동* 에 의한 거짓
    해소다. 따라서 grounding 값을 동결해, 모호성은 새 명령(또는 명료화 루프)으로만
    갱신되게 한다(C2).

    **안정 윈도우 (ADR-0038 D2, 세션 58)**: 첫 'ok' 을 *즉시* 동결하면 vantage 도달
    직후 카메라/OVD 가 아직 후보를 다 잡기 전(예 S7 의자 2개 중 1개만 검출)의 값을
    freeze 해 모호성을 거짓 단일화한다(s1=1.0). 이를 막기 위해 첫 'ok' 후
    ``freeze_window_ns`` 동안은 *더 낮은 s1*(더 모호 = 후보 더 많음)으로만 갱신하고,
    윈도우 만료 후 동결한다. *드론 접근* 거짓 해소는 s1 *상승* 이라 min 규칙이
    무시하므로 C2 보존, *도달 직후 검출 안정* 에 의한 후보 증가(s1 하강)만 반영한다.
    ``freeze_window_ns=0`` 이면 종전대로 첫 'ok' 즉시 동결(back-compat).

    **min-rule 시간 debounce (ADR-0040 D8, 세션 61)**: 안정 윈도우 내 *더 낮은 s1*
    갱신을 **``min_persist_frames`` 연속 'ok' 프레임** 동안 유지될 때만 반영한다. 진단
    (세션 61): OVD 가 같은 객체를 일시적으로 2박스 중복 검출(전구간의 ~0.75%, 1 프레임
    flicker)하면 그 프레임 s1 이 'ok' 인 채 붕괴하는데, 종전 즉시-min 규칙이 그 단일
    프레임을 latch 해 trial c 를 영구 파괴했다. debounce 는 *지속적* 모호성(S5/S7 — 여러
    프레임 유지)만 반영하고 *단일 프레임 아티팩트* 는 기각한다. 더 높은 'ok' 관측이 오면
    low streak 리셋(아티팩트 종료), 부재(no_det/no_match/stale)는 중립(streak 보존).
    ``min_persist_frames<=1`` 이면 종전대로 첫 낮은 프레임 즉시 반영(back-compat).
    (D7 dedup 이 알려진 2박스 아티팩트는 원천 제거 → D8 은 임의 transient 방어심층.)

    Args:
        live: 현 tick OVD 기반 ``S1Result``.
        sigma_active: 현재 활성 σ(referent 존재) 여부.
        command_key: 활성 명령 동일성 키(referent label 튜플 등 hashable). σ 비활성이면
            None. 같은 키면 σ 재발행에도 latch 유지, 키 변경 시 재 grounding.
        latch: 이전 latch 상태 ``(s1, command_key, deadline_ns, pend_min_s1, pend_count)``
            또는 None. (3-tuple 도 허용 — pending 0 으로 패딩, back-compat.)
        now_ns: 현재 시각 [ns] (안정 윈도우 판정용).
        freeze_window_ns: 첫 'ok' 후 min-s1 갱신 윈도우 [ns]. 0 = 즉시 동결.
        min_persist_frames: 더 낮은 s1 을 latch 에 반영하기 전 요구하는 연속 'ok'
            프레임 수. 1(기본) = 즉시(back-compat). >1 = debounce.

    Returns:
        ``(GroundedS1, new_latch)``. new_latch 는
        ``(s1, command_key, deadline_ns, pend_min_s1, pend_count)`` 또는 None.

    fail-safe: σ 비활성 → latch 미소비(live 그대로, 보존만). 같은 명령 grounding
    이력 없으면 live(부재) 그대로 → c=0 (대상 한 번도 못 봄 = 보수).
    """
    def _live(reason: str = None) -> GroundedS1:
        return GroundedS1(
            live.s1, live.absent, reason or live.reason,
            live.n_matched, live.n_detections,
        )

    # σ 비활성(gate 닫힘·σ 미수신 등) → grounding 판단 보류. latch 는 *보존*
    # (파기는 referent 변경 시로 한정 — 세션 62, PR #297 referent-key 완성).
    # 이 경로에서 latch 는 소비되지 않고(live 반환 → 부재 시 c 보수) 같은
    # command_key 로 σ 가 재활성화되면 재개된다.
    if not sigma_active or command_key is None:
        return _live(), latch

    # referent 변경(진짜 새 명령) → 기존 latch 파기, 재 grounding.
    if latch is not None and latch[1] != command_key:
        latch = None

    # 이미 이 명령(referent)에 grounding 됨.
    if latch is not None and latch[1] == command_key:
        frozen_s1 = latch[0]
        deadline_ns = latch[2]
        pend_min = latch[3] if len(latch) > 3 else None    # 진행 중 low streak 최저 s1
        pend_count = latch[4] if len(latch) > 4 else 0     # low streak 연속 'ok' 수
        threshold = max(1, min_persist_frames)

        lower_ok = (
            now_ns < deadline_ns
            and (not live.absent)
            and live.reason == 'ok'
            and live.s1 < frozen_s1
        )
        if lower_ok:
            # 더 낮은(더 모호) 'ok' — debounce 누적. threshold 연속 시에만 반영.
            new_min = live.s1 if pend_min is None else min(pend_min, live.s1)
            new_count = pend_count + 1
            if new_count >= threshold:
                return (
                    GroundedS1(new_min, False, 'latched',
                               live.n_matched, live.n_detections),
                    (new_min, command_key, deadline_ns, None, 0),
                )
            # 아직 지속 미달 → frozen 유지, pending 누적(다음 프레임 판정).
            return (
                GroundedS1(frozen_s1, False, 'latched', 0, live.n_detections),
                (frozen_s1, command_key, deadline_ns, new_min, new_count),
            )

        # 더 낮은 'ok' 아님:
        #   'ok' & s1>=frozen (깨끗한 상위 관측) → low streak 리셋(아티팩트 종료).
        #   부재(no_det/no_match/stale) → 중립(streak 보존, frozen 유지).
        if (not live.absent) and live.reason == 'ok':
            return (
                GroundedS1(frozen_s1, False, 'latched', 0, live.n_detections),
                (frozen_s1, command_key, deadline_ns, None, 0),
            )
        return (
            GroundedS1(frozen_s1, False, 'latched', 0, live.n_detections),
            (frozen_s1, command_key, deadline_ns, pend_min, pend_count),
        )

    # 이 명령 첫 grounding('ok') → freeze 시작 (안정 윈도우 deadline 설정).
    if (not live.absent) and live.reason == 'ok':
        return (
            GroundedS1(live.s1, False, 'ok', live.n_matched, live.n_detections),
            (live.s1, command_key, now_ns + freeze_window_ns, None, 0),
        )

    # 아직 grounding 못함 → live(부재) 그대로.
    return _live(), latch
