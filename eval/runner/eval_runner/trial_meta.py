"""TrialSpec → trial_meta.yaml 자동 생성 (write side).

[B7 #12 분할 2d](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
— `eval_metrics.trial_meta.load_trial_metadata` (read side) 측 roundtrip 정합.
runner.py 측 trial 실행 직후 본 모듈 측 trial_meta.yaml 작성 → bag 옆 저장 →
post-hoc 분석 측 `eval_metrics.load_trial_metadata` 측 재로드 + metric 계산.

## YAML 스키마 (ADR-0025 D4 + eval_metrics.schemas.TrialMetadata 정합)

```
scenario: 'S5' | 'S6'
baseline: 'B0' | 'B1' | 'B2' | 'B3' | 'B4'
fault_class: 'none' | 'hallucination' | 'adversarial' | 'cognitive_lapse' | 'attribute_mismatch'
fault_variant: <str>   # none 측 'none' 또는 ''
seed: <int>
wall_clock_s: <float>  # 실 측정 episode 길이 [s]
bag_status: 'complete' | 'incomplete' | 'fault_not_applicable'  # bag 무결성 판정 (eval_runner.bag_integrity)
```

`bag_status` 는 trial 종료 직후 `bag_integrity.check_bag_integrity` 판정 결과
— 'incomplete' trial 은 runner resume 측 재실행 대상 + 메트릭 집계 측 명시
보고 대상 (조용한 제외(silent drop) 금지). 'fault_not_applicable' (제3 범주,
ADR-0037 amend) 은 의도 계층의 명료화 후퇴로 주입이 정의되지 않은 trial —
결함 통계 산입 금지(별도 보고) + resume 재실행 대상 아님(결정론적 거동).
write side 는 'unknown' 을 절대 쓰지 않음 ('unknown' = read side 측 legacy
meta 분류 전용).

## 책임 분리 (write side ↔ read side)

| 모듈 | 입력 | 출력 | 역할 |
|---|---|---|---|
| `eval_runner.trial_meta` (write, 본 모듈) | TrialSpec + wall_clock_s + bag_status | YAML 파일 | runner.py 측 trial 실행 직후 |
| `eval_metrics.trial_meta` (read) | YAML 파일 | TrialMetadata | post-hoc 분석 측 metric 계산 |

## baseline value 측 case 변환

`eval_baselines.schemas.BaselineMode` value 측 *lowercase* ('b0', 'b1', ...) ↔
`eval_metrics.schemas.TrialMetadata._ALLOWED_BASELINES` 측 *uppercase* ('B0', ...).
본 모듈 측 `.upper()` 변환 책임. TrialSpec.trial_id (lowercase) 측 grep-friendly +
TrialMetadata (uppercase) 측 paper §C 표 정합 — 두 표기법 공존 *의도*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Union

import yaml

from eval_runner.bag_integrity import (
    BAG_STATUS_COMPLETE,
    BAG_STATUS_FAULT_NOT_APPLICABLE,
    BAG_STATUS_INCOMPLETE,
)
from eval_runner.schemas import TrialSpec


TRIAL_META_FILENAME = 'trial_meta.yaml'

# write side 허용 bag_status — 'unknown' 은 read side 측 legacy 분류 전용이라 제외.
# 'fault_not_applicable' = 제3 범주 (ADR-0037 amend, bag_integrity 모듈 docstring).
_WRITABLE_BAG_STATUSES = (
    BAG_STATUS_COMPLETE,
    BAG_STATUS_INCOMPLETE,
    BAG_STATUS_FAULT_NOT_APPLICABLE,
)


def trial_meta_yaml_path(bag_dir: Union[str, Path]) -> Path:
    """bag 디렉토리 측 trial_meta.yaml 경로 잠금.

    ADR-0025 D4 측 "메타 YAML (trial_meta.yaml) bag 옆" — bag_dir 측 *sibling*
    아닌 *내부* (bag 디렉토리 측 단일 trial 측 모든 산출물 한 묶음). runner.py
    측 본 helper 측 path 잠금 후 trial_meta_yaml_dict / write_trial_meta_yaml
    측 사용.

    Args:
        bag_dir: rosbag2 record 측 output 디렉토리. TrialSpec.trial_id 측 default.

    Returns:
        ``<bag_dir>/trial_meta.yaml`` 경로.
    """
    return Path(bag_dir) / TRIAL_META_FILENAME


def trial_meta_yaml_dict(
    trial: TrialSpec,
    wall_clock_s: float,
    bag_status: str,
) -> Dict[str, Any]:
    """TrialSpec + wall_clock_s + bag_status → trial_meta.yaml YAML dict.

    `eval_metrics.trial_meta.load_trial_metadata` 측 read 정합 — 본 dict 측
    `yaml.safe_dump` 측 직접 YAML 파일 작성 후 load 측 roundtrip 보장.

    Args:
        trial: TrialSpec — scenario_id / baseline_config / fault_scenario / seed.
        wall_clock_s: trial 측 실 측정 episode 길이 [s] — runner.py 측 rosbag2
            start/stop wallclock delta. 양의 실수 필수 (load 측 TrialMetadata
            invariant).
        bag_status: 'complete' | 'incomplete' | 'fault_not_applicable' —
            `bag_integrity.check_bag_integrity` 판정 결과. caller(runner.run_trial)
            측 판정 책임 — 본 함수 측 default 없음 (silent 'complete' 기록 금지).

    Returns:
        YAML dict — keys = (scenario · baseline · fault_class · fault_variant ·
        seed · wall_clock_s · bag_status).

    Raises:
        ValueError: wall_clock_s <= 0 (TrialMetadata invariant 정합) 또는
            bag_status 측 _WRITABLE_BAG_STATUSES 외.
    """
    if wall_clock_s <= 0.0:
        raise ValueError(
            f'wall_clock_s 양의 실수 필수 — got {wall_clock_s}. '
            f'runner.py 측 rosbag2 start/stop wallclock delta 측 측정 의무.'
        )
    if bag_status not in _WRITABLE_BAG_STATUSES:
        raise ValueError(
            f'bag_status 측 {_WRITABLE_BAG_STATUSES} — got {bag_status!r}. '
            f"'unknown' 은 read side 측 legacy 분류 전용 (write 금지)."
        )
    return {
        'scenario': trial.scenario_id,
        # baseline value 측 'b0' → 'B0' uppercase 변환 — TrialMetadata._ALLOWED_BASELINES
        # 정합. TrialSpec.trial_id 측 lowercase 유지 (grep-friendly 의도).
        'baseline': trial.baseline_config.mode.value.upper(),
        'fault_class': trial.fault_scenario.channel.value,
        # fault_variant 측 None → 'none' 정규화. TrialMetadata 측 fault_class='none'
        # 측 fault_variant ∈ ('', 'none') 허용 (None 측 거부) — 'none' 측 default.
        'fault_variant': trial.fault_scenario.variant or 'none',
        'seed': trial.seed,
        'wall_clock_s': float(wall_clock_s),
        'bag_status': bag_status,
    }


def write_trial_meta_yaml(
    trial: TrialSpec,
    wall_clock_s: float,
    path: Union[str, Path],
    bag_status: str,
) -> None:
    """trial_meta_yaml_dict 결과 측 YAML 파일 작성.

    `yaml.safe_dump` 측 사용 — flow style 아닌 block style (paper §C 후속
    inspect 측 가독성). UTF-8 encoding 잠금.

    Args:
        trial: TrialSpec.
        wall_clock_s: trial episode 길이 [s].
        path: YAML 파일 경로. 부모 디렉토리 존재 의무 (runner.py 측 bag_dir
            생성 후 호출 패턴).
        bag_status: _WRITABLE_BAG_STATUSES 중 하나 — trial_meta_yaml_dict 정합.

    Raises:
        ValueError: wall_clock_s / bag_status 측 trial_meta_yaml_dict invariant 위반.
        FileNotFoundError: path 측 부모 디렉토리 부재.
    """
    payload = trial_meta_yaml_dict(trial, wall_clock_s, bag_status)
    target = Path(path)
    if not target.parent.exists():
        raise FileNotFoundError(
            f'trial_meta.yaml 측 부모 디렉토리 부재 — {target.parent}. '
            f'runner.py 측 bag_dir 생성 후 본 함수 호출 의무.'
        )
    with open(target, 'w', encoding='utf-8') as f:
        yaml.safe_dump(payload, f, default_flow_style=False, sort_keys=True)
