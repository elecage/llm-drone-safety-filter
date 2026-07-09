"""ADR-0025 D2 — task success rate (SR).

정의: episode 종료 시 *목적 만족 binary* 합산 / total episodes.

ADR-0025 D2 측 "한 trial 단위 + 격자 단위 두 수준 계산" 명시:
- **trial 단위 SR**: trivial — episode 종료 측 boolean (True/False) *그대로*.
  별 function 측 없음 (boolean cast 측 충분).
- **격자 단위 SR**: 본 function `task_success_rate(list[bool])` 측 mean.
  여러 episodes 측 binary list → success ratio $\\in [0, 1]$.

```
SR = (#{episodes : success == True}) / |episodes|
```

다른 metric (safety / overconservativeness / autonomy / query / latency) 측
*trial 단위* 측 시계열 / 카운트 → float 측 *non-trivial* 측 function 필요.
SR 만 trial 단위 측 trivial — *격자 단위만* 본 모듈 측 구현.
"""

from __future__ import annotations

from typing import List


def task_success_rate(success_per_episode: List[bool]) -> float:
    """SR = success 합산 / 총 episode 수.

    Args:
        success_per_episode: 한 격자 측 episode 별 success binary.

    Returns:
        $\\text{SR} \\in [0, 1]$.

    Raises:
        ValueError: 빈 list — 격자 측 episode 없으면 SR 정의 안 됨.
        TypeError: success 측 bool 아님.
    """
    if not success_per_episode:
        raise ValueError(
            'task_success_rate 측 빈 list 거부 — 격자 측 episode 측 1 이상 필요'
        )
    for i, s in enumerate(success_per_episode):
        if not isinstance(s, bool):
            raise TypeError(
                f'success_per_episode[{i}] 측 bool 필요 — got {type(s).__name__}'
            )
    return sum(success_per_episode) / len(success_per_episode)
