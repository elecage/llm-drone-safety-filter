"""trial_meta.yaml YAML loader → TrialMetadata frozen dataclass.

[ADR-0025 D4](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d4)
정합 — rosbag2 측 trial bag 옆 `trial_meta.yaml` 측 metadata 측 추출.

## YAML 스키마

```
scenario: 'S5' | 'S6' | 'S7' | 'S8'
baseline: 'B0' | 'B1' | 'B2' | 'B3' | 'B4'
fault_class: 'none' | 'hallucination' | 'adversarial' | 'cognitive_lapse' | 'attribute_mismatch'
fault_variant: <str>   # none 측 '' 또는 'none', 그 외 channel 측 variant string
seed: <int>
wall_clock_s: <float>  # 실 측정 episode 길이 [s]
bag_status: 'complete' | 'incomplete' | 'fault_not_applicable'  # 선택 키 — 부재(legacy) 측 'unknown' 로드
```

`bag_status` 는 *선택* 키 (세션 34 리뷰 P2 후속 도입) — 도입 전 기록된 legacy
trial_meta.yaml 호환을 위해 부재 시 `'unknown'` 으로 로드. **메트릭 집계는
bag_status != 'complete' trial 을 명시 보고해야 한다 (개수 + trial id) —
조용한 제외(silent drop) 금지.** run 전체 스캔 helper =
`eval_runner.bag_integrity.scan_trial_bag_statuses`.

[fault_scenario.py](../../eval/faults/eval_faults/fault_scenario.py) 측 *fault
spec* YAML 과 *별개* — trial_meta 측 *trial 측 메타데이터*, fault_scenario 측
*fault injection plan*. 한 trial 측 1 trial_meta.yaml + 1 fault_scenario YAML
(`fault_class='none'` 측 fault_scenario 측 ``none_baseline.yaml`` 측 참조).
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml

from eval_metrics.schemas import TrialMetadata


_REQUIRED_KEYS = frozenset({
    'scenario', 'baseline', 'fault_class', 'fault_variant', 'seed', 'wall_clock_s',
})
# 선택 키 — bag_status 도입(세션 34 리뷰 P2 후속) 전 legacy meta 호환.
_OPTIONAL_KEYS = frozenset({'bag_status'})


def load_trial_metadata(path: Union[str, Path]) -> TrialMetadata:
    """trial_meta.yaml → TrialMetadata.

    Args:
        path: YAML 파일 경로.

    Returns:
        TrialMetadata — frozen dataclass, __post_init__ 측 검증 통과.

    Raises:
        FileNotFoundError: 파일 부재.
        yaml.YAMLError: YAML parse 실패.
        KeyError: 필수 키 부재.
        ValueError: YAML root 측 dict 아님 또는 TrialMetadata invariant 위반
            (scenario/baseline/fault_class allowed lists 외 등).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'trial_meta YAML 부재 — {path}')

    with open(path, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            f'YAML root 는 dict — got {type(raw).__name__} ({path})'
        )

    missing = _REQUIRED_KEYS - set(raw.keys())
    if missing:
        raise KeyError(
            f'trial_meta YAML 측 필수 키 부재: {sorted(missing)!r} ({path})'
        )

    allowed = _REQUIRED_KEYS | _OPTIONAL_KEYS
    extra = set(raw.keys()) - allowed
    if extra:
        raise ValueError(
            f'unknown YAML keys: {sorted(extra)!r} '
            f'(허용 = {sorted(allowed)!r}, {path}) — '
            f'typo 또는 schema 외 키. silent default 회피 위해 거부.'
        )

    return TrialMetadata(
        scenario=str(raw['scenario']),
        baseline=str(raw['baseline']),
        fault_class=str(raw['fault_class']),
        fault_variant=str(raw['fault_variant']),
        seed=int(raw['seed']),
        wall_clock_s=float(raw['wall_clock_s']),
        # legacy meta (키 부재) 측 'unknown' — TrialMetadata docstring 측 집계
        # 명시 보고 의무. silent 'complete' 승격 금지.
        bag_status=str(raw.get('bag_status', 'unknown')),
    )
