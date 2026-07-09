r"""명료화 루프 종료 정책 — Φ_9 / L4 안전 (순수 로직).

cmsm-proof §9.3 $\Phi_9$ + §10.4 (L4): confirm(=ask_user) 발동 후 $T_\text{resp}$
안에 사용자 응답이 단일 후보로 좁혀지지 않으면 reject + hover. [ADR-0016 D3](../../../docs/handover/decisions/0016-intent-disambiguation-stack.md)
의 "max 3회 또는 30 s 초과 시 자동 reject + hover" 를 *순수 결정 함수* 로 코드화.

## 안전 불변식 (L4)

루프가 **어떻게 끝나든** 안전한 처분만 산출한다:
- 의도가 명확(ask_user 아님) → EXECUTE (Tier 1·Tier 2 가 별도로 검증).
- 모호하고 한도 내 → CLARIFY (TTS 질의 + 재STT, 루프 계속).
- 한도 초과(턴 ≥ max 또는 경과 ≥ timeout) → **TIMEOUT_HOVER** (reject + hover).
  Tier 1 이 루프 내내 계속 활성이므로 응답 지연이 안전 약화가 아니다 (L4).

TIMEOUT_HOVER 가 안전 *하한* — 정책 버그로 CLARIFY 가 무한 반복돼도 한도에서
반드시 hover 로 떨어진다 (fail-safe-by-construction).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LoopDecision(str, Enum):
    """명료화 루프 한 스텝의 처분."""

    EXECUTE = "execute"            # 의도 명확 → 실행 (루프 종료)
    CLARIFY = "clarify"            # 모호 + 한도 내 → TTS 질의 + 재STT
    TIMEOUT_HOVER = "timeout_hover"  # 한도 초과 → reject + hover (L4 안전)


@dataclass(frozen=True)
class LoopConfig:
    r"""루프 종료 한도 — C9 / $\Phi_9$ ($T_\text{resp}$) 정합.

    max_turns: confirm(ask_user) 반복 최대 횟수 (ADR-0016 D3 = 3).
    timeout_s: 루프 시작부터 경과 한도 [s] ($T_\text{resp}$ = 30, cmsm-proof §9.3).
    """

    max_turns: int = 3
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError(f"max_turns 는 1 이상: {self.max_turns}")
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s 는 양수: {self.timeout_s}")


def decide(
    is_ask_user: bool,
    turn: int,
    elapsed_s: float,
    config: LoopConfig = LoopConfig(),
) -> LoopDecision:
    r"""명료화 루프 한 스텝 처분 결정 (순수).

    Args:
        is_ask_user: 직전 *의도해석기* 출력이 ask_user(=confirm 필요) 인가.
        turn: 지금까지 수행한 ask_user(confirm) 횟수 (0-기반, 첫 모호 응답 후 1).
        elapsed_s: 루프 시작부터 경과 시간 [s].
        config: 종료 한도.

    Returns:
        LoopDecision. 우선순위:
          1. 명확(not ask_user) → EXECUTE.
          2. 한도 초과(turn ≥ max_turns 또는 elapsed ≥ timeout_s) → TIMEOUT_HOVER.
          3. 그 외 → CLARIFY.

    안전: 1번이 아니면 한도 검사가 항상 hover 하한을 보장 (L4).
    """
    if not is_ask_user:
        return LoopDecision.EXECUTE
    if turn >= config.max_turns or elapsed_s >= config.timeout_s:
        return LoopDecision.TIMEOUT_HOVER
    return LoopDecision.CLARIFY
