r"""명료화 인터랙션 루프 오케스트레이터 — IO 주입 (ADR-0016 D3, B4).

[loop_policy](loop_policy.py) 의 순수 결정으로 STT→LLM→(ask_user)→TTS→재STT
사슬을 돈다. 모든 IO(STT/발행/의도수신/TTS/hover)는 *주입* — host venv 단위
테스트(mock IO) + 실 IO 연결(scripts/clarification_loop.py) 분리.

## 루프 (ADR-0016 D3 시퀀스)

```
발화(STT) → publish → 의도 수신(σ,θ,c)
  ├─ ask_user 아님 → EXECUTE (루프 종료, Tier 1·2 가 검증)
  ├─ ask_user + 한도 내 → CLARIFY: TTS 질의 → 재STT → 누적 → publish (반복)
  └─ 한도 초과 → TIMEOUT_HOVER (reject + hover, L4 안전)
```

누적 context(ADR-0016 D3 step 5)는 `accumulate` 콜백으로 직전 발화 + 응답을
합쳐 다음 publish 입력을 만든다 (구체 누적 전략은 주입 — 단순 연결 or 구조화).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from intent_loop.loop_policy import LoopConfig, LoopDecision, decide


@dataclass
class LoopIO:
    """루프가 쓰는 IO 콜백 묶음 (테스트 mock / 실 IO 주입).

    Attributes
    ----------
    capture_utterance : () -> str
        STT — 사용자 발화 텍스트 수신 (블로킹). 빈 문자열 = 무응답.
    publish_intent_input : (str) -> None
        발화(누적)를 의도해석기 입력 토픽으로 발행.
    receive_intent : () -> tuple[bool, str]
        의도 결과 수신 → (is_ask_user, message). is_ask_user=True 면 message 는
        TTS 질의 텍스트. False(=EXECUTE) 면 message 는 *실행 확인 문구* — 비어
        있지 않으면 실행 직전 음성 피드백으로 출력(빈 문자열이면 무음, 종전 동작).
    speak : (str) -> None
        TTS — 질의 또는 실행 확인 텍스트 음성 출력.
    hover : () -> None
        TIMEOUT_HOVER 안전 처분 (reject + hover 발행).
    now : () -> float
        단조 시계 [s] (테스트 주입 — 기본 time.monotonic).
    accumulate : (str, str) -> str
        (직전 누적 발화, 새 응답) → 다음 publish 입력. 기본 = 공백 연결.
    """

    capture_utterance: Callable[[], str]
    publish_intent_input: Callable[[str], None]
    receive_intent: Callable[[], "tuple[bool, str]"]
    speak: Callable[[str], None]
    hover: Callable[[], None]
    now: Callable[[], float] = time.monotonic
    accumulate: Callable[[str, str], str] = lambda prev, resp: f"{prev} {resp}".strip()


@dataclass
class LoopResult:
    """루프 종료 결과 — paper §C 측정·디버깅용."""

    outcome: LoopDecision           # EXECUTE 또는 TIMEOUT_HOVER (CLARIFY 로 안 끝남)
    turns: int                      # 수행한 ask_user(confirm) 횟수
    elapsed_s: float                # 루프 총 경과
    transcript: List[str] = field(default_factory=list)  # 누적 발화 이력


def run_clarification_loop(
    io: LoopIO,
    config: LoopConfig = LoopConfig(),
) -> LoopResult:
    r"""명료화 루프 실행 → LoopResult.

    첫 발화를 받아 publish → 의도 수신 → 정책 결정. ask_user 면 질의 음성 출력 후
    재STT → 누적 → 재publish (turn++). EXECUTE/TIMEOUT_HOVER 에서 종료.

    안전: 어떤 경로든 EXECUTE(검증 위임) 또는 TIMEOUT_HOVER(hover)로만 종료.
    """
    start = io.now()
    transcript: List[str] = []

    utterance = io.capture_utterance()
    transcript.append(utterance)
    accumulated = utterance
    turn = 0

    while True:
        io.publish_intent_input(accumulated)
        is_ask_user, message = io.receive_intent()
        elapsed = io.now() - start
        decision = decide(is_ask_user, turn, elapsed, config)

        if decision is LoopDecision.EXECUTE:
            # 실행 직전 확인 음성 — message 가 채워져 있을 때만 (수락 피드백).
            # 빈 문자열이면 무음(종전 동작·mock 테스트 보존).
            if message:
                io.speak(message)
            return LoopResult(LoopDecision.EXECUTE, turn, elapsed, transcript)

        if decision is LoopDecision.TIMEOUT_HOVER:
            io.hover()
            return LoopResult(LoopDecision.TIMEOUT_HOVER, turn, elapsed, transcript)

        # CLARIFY — 질의 음성 출력 + 사용자 응답 재수신 + 누적.
        io.speak(message)
        response = io.capture_utterance()
        transcript.append(response)
        accumulated = io.accumulate(accumulated, response)
        turn += 1
