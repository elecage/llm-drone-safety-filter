"""cmsm-proof §9.4 — 게이트 결정 함수 G.

(σ, θ, c) → {accept, confirm, reject}. 5 cases 그대로:

  Case 1: reject  — CC-1 또는 CC-2 위반 (catalog.validate_command)
  Case 2: reject  — c < c_lo (Φ_4) — 단 inspect 면제 (ADR-0034, 아래 _PHI4_EXEMPT)
  Case 3: reject  — Φ_1·Φ_2·Φ_3·Φ_5·Φ_6·Φ_7·Φ_8·Φ_9 중 하나 위반 (specs.violations)
  Case 4: confirm — contradicts(σ; σ_prev) (Φ_10, ADR-0019)
  Case 5: confirm — c_lo ≤ c < c_hi 이고 action-class ≠ monitoring
  Case 6: accept  — 그 외

순서 중요 — case 1 위반은 case 2 이전에 reject. 결정론적·pure function.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from tier2_gate.catalog import (
    SKILL_ACTION_CLASS,
    ActionClass,
    Geofence,
    validate_command,
)
from tier2_gate.contradicts import Activity, contradicts
from tier2_gate.specs import GateState
from tier2_gate.specs import violations as spec_violations
from tier2_gate.thresholds import Thresholds


class Decision(str, Enum):
    ACCEPT = 'accept'
    CONFIRM = 'confirm'
    REJECT = 'reject'


# Φ_4(저신뢰도 reject) 면제 행동 — ADR-0034 (gate-before-vantage 교착 해소).
# inspect 의 신뢰도 c 는 s1(지각 grounding)에서 오는데, 관측 *전* 엔 카메라가 대상을
# 못 봐 s1=0 → c=0 이다. 게이트가 c<c_lo 로 관측 σ 를 막으면 sigma_bridge 가 σ 를
# 못 받아 vantage 비행→grounding 이 영영 불가(순환: 못 봤으니 신뢰도 0, 신뢰도 0이라
# 못 보러 감). inspect 는 카메라 전용·tier-1 회피 영역 하한이 물리 안전을 결정론적으로
# 보장(RQ1)하므로 저신뢰도 관측도 안전 → Φ_4 면제. move_to(명시 위치, grounding 불요)
# 와 return 류는 *비면제* — 적대적 move_to 의 게이트 방어 유지. Case 5(중간 신뢰도
# confirm)가 이미 monitoring 을 면제하는 것과 같은 결의 정형 변경(cmsm-proof §9.4).
_PHI4_EXEMPT: frozenset[str] = frozenset({'inspect'})


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    reason: str = ''
    violations: tuple[str, ...] = ()


def select_confidence(
    latest_c: float | None,
    payload: Mapping[str, Any],
    default_c: float,
) -> float:
    """게이트가 결정에 쓸 신뢰도 c 선택 — 우선순위 estimator > payload > default.

    - ``latest_c`` — estimator 가 발행한 최신 *합성·변화율 제한된* c (= 티어 1 CBF
      가 쓰는 정본). 아키텍처 §4.3 (추정기→티어 2) 에 따라 *최우선*. None = 미수신.
    - ``payload['c']`` — σ payload 의 raw 'c'. estimator 부재(단독 운용·단위 test)
      fallback 으로만. wrapper(intent_llm)는 이 키에 confidence_raw 를 항상 싣지만,
      그 raw 값이 아니라 estimator 의 합성 c 가 정본이므로 estimator 가용 시 무시.
    - ``default_c`` — 둘 다 없을 때 (첫 c 수신 전 startup) fallback.

    세션 52 sim e2e 가 드러낸 결함 정정: 종전 'payload 'c' 우선' 순서는 wrapper 의
    raw c 를 항상 채택해 estimator 의 _latest_c 를 무력화 (+ hallucination 경로는
    injector 가 c 를 strip 해 _latest_c 사용 → 결함 채널마다 c 출처 불일치). 우선순위
    역전으로 양 경로를 estimator c 로 통일. ``float(payload['c'])`` 는 호출측 try
    에서 ValueError/TypeError 를 reject 로 처리.
    """
    if latest_c is not None:
        return float(latest_c)
    if 'c' in payload:
        return float(payload['c'])
    return float(default_c)


def gate(
    sigma: str,
    theta: Mapping[str, Any],
    c: float,
    *,
    sigma_prev: str | None,
    theta_prev: Mapping[str, Any] | None,
    activity: Activity,
    geofence: Geofence,
    known_objects: frozenset[str],
    state: GateState,
    thresholds: Thresholds,
) -> DecisionResult:
    """cmsm-proof §9.4 G — 5 cases 결정.

    sigma_prev=None 은 세션 첫 명령 (모순 평가 대상 없음).
    activity 는 mutual exclusive (IDLE | INSPECT | RETURN), ADR-0019 D3.
    """
    # Case 1 — CC-1·CC-2.
    cc = validate_command(
        sigma, theta, geofence=geofence, known_objects=known_objects
    )
    if not cc.valid:
        return DecisionResult(Decision.REJECT, cc.reason)

    # Case 2 — c < c_lo (Φ_4). inspect 면제 (ADR-0034 — 관측-grounding 순환 차단).
    if c < thresholds.c_lo and sigma not in _PHI4_EXEMPT:
        return DecisionResult(
            Decision.REJECT, f'Φ_4: c={c} < c_lo={thresholds.c_lo}', ('Φ_4',)
        )

    # Case 3 — Φ_1..Φ_3·Φ_5..Φ_9 사양 위반.
    vs = spec_violations(
        sigma, theta, geofence=geofence, state=state, thresholds=thresholds
    )
    if vs:
        return DecisionResult(
            Decision.REJECT, f'spec violation: {",".join(vs)}', tuple(vs)
        )

    # Case 4 — Φ_10 contradicts (위반 시 *confirm*, 비대칭 처분).
    if contradicts(
        sigma_prev, theta_prev, sigma, theta,
        activity=activity, thresholds=thresholds,
    ):
        return DecisionResult(
            Decision.CONFIRM, 'Φ_10: contradicts previous command', ('Φ_10',)
        )

    # Case 5 — c ∈ [c_lo, c_hi) and non-monitoring → confirm.
    if c < thresholds.c_hi and SKILL_ACTION_CLASS[sigma] != ActionClass.MONITORING:
        return DecisionResult(
            Decision.CONFIRM,
            f'c={c} ∈ [{thresholds.c_lo}, {thresholds.c_hi}) and '
            f'action-class={SKILL_ACTION_CLASS[sigma].value} ≠ monitoring',
        )

    # Case 6 — accept.
    return DecisionResult(Decision.ACCEPT)
