"""Pure-Python fault scenario YAML loader + 4 channel polymorphic dispatch.

[ADR-0025 D5](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d5)
12 PR 시안의 #5a 단계 — paper §C trial 측 fault injection plan 의 *공통 entry
point*. B5 #5b 측 ROS 2 injector_node 와 분리한 *rclpy 없이* host venv 측
테스트 가능한 pure logic.

설계 패턴 = A3-3 트랙 [signal_scenario.py](../../intent/confidence/intent_confidence/signal_scenario.py)
정합 — estimator_node ↔ signal_scenario 의 *ROS 2 wrapper ↔ pure loader*
분리. 본 모듈 = injector_node ↔ fault_scenario 의 *동일 분리*.

## YAML 스키마

```
name: <str>
description: <str>
channel: 'none' | 'hallucination' | 'adversarial' | 'cognitive_lapse' | 'attribute_mismatch'
variant: <str | null>     # channel 측 variant enum value (none channel 측 null)
context_kwargs: <dict>    # channel-specific context dataclass 측 kwargs (none 측 {})
seed: <int>               # paper §C trial seed (rng 재현성)
```

채널 별 `context_kwargs` 측 구조:

- `hallucination`: [FaultContext](schemas.py) 측 kwargs — known_objects, user_position,
  r_min, sigma_llm_nat_cm, geofence. YAML list → tuple 자동 변환.
- `adversarial`: 동일 [FaultContext](schemas.py) 측 kwargs.
- `cognitive_lapse`: [CognitiveLapseContext](schemas.py) 측 kwargs —
  initial_target_id/_name_kr, alternative_target_id/_name_kr, range tuple 2 종.
- `attribute_mismatch`: [AttributeMismatchContext](schemas.py) 측 kwargs —
  vocabulary, sigma 2 종, dangerous_label.

## 호출 규약 (B5 #5b injector_node 측)

```python
scenario = load_fault_scenario(path)
context, variant = build_fault_context(scenario)
rng = random.Random(scenario.seed)
# channel 별 apply_* 호출 — injector_node 측 OVD/LLM/utterance dispatch
```

`build_fault_context` 는 (context_obj, variant_enum) 반환. *channel='none'* 측
$(\\text{None}, \\text{None})$ — injector 측 baseline (no transformation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import yaml

from eval_faults.schemas import (
    AdversarialVariant,
    AttributeMismatchContext,
    AttributeMismatchVariant,
    CognitiveLapseContext,
    CognitiveLapseVariant,
    FaultContext,
    FaultVariant,
)


# -------------------------------------------------------------------- enum + dataclass


class FaultChannel(str, Enum):
    """[ADR-0018 D2](../../docs/handover/decisions/0018-paper1-experiment-input-pipeline.md#d2)
    + [ADR-0025 D1](../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d1)
    — paper §C fault_class 5 enum.

    - NONE: baseline trial — no transformation.
    - HALLUCINATION: post-LLM σ hook (B5 #1).
    - ADVERSARIAL: pre-LLM prompt hook (B5 #2).
    - COGNITIVE_LAPSE: 시간축 측 발화 시계열 (B5 #3).
    - ATTRIBUTE_MISMATCH: pre-LLM OVD detection 측 변형 (B5 #4).
    """

    NONE = 'none'
    HALLUCINATION = 'hallucination'
    ADVERSARIAL = 'adversarial'
    COGNITIVE_LAPSE = 'cognitive_lapse'
    ATTRIBUTE_MISMATCH = 'attribute_mismatch'


# 채널 → injector_node 의 *_faulted* 출력 토픽 단일 소스. injector_node (publish),
# launch_composition (활성 채널 토픽 record), bag_integrity (활성 채널 토픽 ≥1
# sample 무결성 가드) 가 공유 — 종전 injector_node 측 topic 문자열 하드코딩의
# drift 차단. NONE 은 변형 출력 없음(no-op) → 매핑 부재.
#
# 무결성 가드 (격자 smoke 2026-06-14 노출 — fault×sigma 비호환 시 injector 가
# 조용히 no-op, bag=complete): 활성 채널의 본 토픽이 bag 에 0 sample 이면 fault
# 가 *선언됐으나 미주입* → bag_integrity 가 'incomplete' 판정 → scan-bags 가
# 명시 보고(조용한 제외 금지). ADR-0025 amendment / ADR-0028 Track B 참조.
# HALLUCINATION 은 **인라인**(세션 49 결정): faulted σ 가 actuation 토픽
# ``/intent/llm_sigma_raw`` 에 실려 sigma_bridge(actuation) + estimator(c̃) 양쪽에
# 도달 → 위험 swap 타깃이 실제 비행되어 *필터가 막는가(RQ1)* 를 직접 시험. wrapper 는
# hallucination trial 에서 ``HALLUCINATION_PREFAULT_TOPIC`` 로 출력하고 injector 가
# 그것을 받아 변형 후 ``/intent/llm_sigma_raw`` 로 republish (loop 회피). 종전
# estimator-only(``/intent/llm_sigma_faulted``)는 actuation 미도달이라 폐기.
FAULT_CHANNEL_FAULTED_TOPIC: Dict[FaultChannel, str] = {
    FaultChannel.HALLUCINATION: '/intent/llm_sigma_raw',
    FaultChannel.ADVERSARIAL: '/intent/user_prompt_faulted',
    FaultChannel.COGNITIVE_LAPSE: '/intent/lapse_event',
    FaultChannel.ATTRIBUTE_MISMATCH: '/intent/ovd/detections_faulted',
}

# hallucination 인라인 — wrapper 의 *pre-injector* σ 출력 토픽(=injector 입력).
# injector 가 이를 받아 변형 후 FAULT_CHANNEL_FAULTED_TOPIC[HALLUCINATION]
# (/intent/llm_sigma_raw)로 republish. NONE/adversarial/attribute trial 의 wrapper 는
# 기본 /intent/llm_sigma_raw 로 직접 출력(본 토픽 미사용).
HALLUCINATION_PREFAULT_TOPIC: str = '/intent/llm_sigma_prefault'


@dataclass(frozen=True)
class FaultScenario:
    """paper §C trial 측 fault injection plan — YAML 측 loaded 단위.

    Fields:
        name: 시나리오 식별자 (YAML 파일명 측 derived).
        description: 자유 형식 설명.
        channel: FaultChannel.
        variant: channel 측 variant enum value (string). NONE channel 측 None.
        context_kwargs: channel-specific context dataclass 측 kwargs (raw dict).
            YAML 측 그대로 read — list → tuple 변환은 build_fault_context 측
            처리. NONE channel 측 빈 dict ``{}``.
        seed: paper §C trial 측 rng seed (재현성).
    """

    name: str
    description: str
    channel: FaultChannel
    variant: Optional[str]
    context_kwargs: Dict[str, Any] = field(default_factory=dict)
    seed: int = 42

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(f'name 빈 문자열 불가 — got {self.name!r}')
        if self.channel == FaultChannel.NONE:
            if self.variant is not None:
                raise ValueError(
                    f'channel=none 측 variant 는 None — got {self.variant!r}'
                )
            if self.context_kwargs:
                raise ValueError(
                    f'channel=none 측 context_kwargs 는 빈 dict — '
                    f'got {self.context_kwargs!r}'
                )
        else:
            if self.variant is None or not str(self.variant).strip():
                raise ValueError(
                    f'channel={self.channel.value} 측 variant 필수 — got None'
                )


# -------------------------------------------------------------------- YAML loader


_ALLOWED_YAML_KEYS = frozenset({
    'name', 'description', 'channel', 'variant', 'context_kwargs', 'seed',
})


def load_fault_scenario(path: Union[str, Path]) -> FaultScenario:
    """YAML 파일 → FaultScenario.

    strict key validation — `_ALLOWED_YAML_KEYS` 측 *whitelist* 외 키 발견 시
    ValueError raise (PR #104 review B-5 정정 — typo 측 silent corruption 회피).
    예: ``seeed: 42`` typo → ValueError ("unknown YAML key").

    Args:
        path: YAML 파일 경로.

    Returns:
        FaultScenario — frozen dataclass, __post_init__ 측 검증 통과.

    Raises:
        FileNotFoundError: 파일 부재.
        yaml.YAMLError: YAML parse 실패.
        KeyError: 필수 키 부재 (name / channel).
        ValueError: YAML root 측 dict 아님, channel 측 value 가 FaultChannel
            enum 측 부재, _ALLOWED_YAML_KEYS 외 키 발견, 또는 FaultScenario
            __post_init__ invariant 위반.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'fault scenario YAML 부재 — {path}')

    with open(path, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(
            f'YAML root 는 dict 이어야 — got {type(raw).__name__} ({path})'
        )

    for key in ('name', 'channel'):
        if key not in raw:
            raise KeyError(f'YAML 측 필수 키 부재: {key!r} ({path})')

    extra_keys = set(raw.keys()) - _ALLOWED_YAML_KEYS
    if extra_keys:
        raise ValueError(
            f'unknown YAML keys: {sorted(extra_keys)!r} '
            f'(허용 = {sorted(_ALLOWED_YAML_KEYS)!r}, {path}) — '
            f'typo 또는 schema 외 키. silent default 회피 위해 거부.'
        )

    try:
        channel = FaultChannel(raw['channel'])
    except ValueError as exc:
        raise ValueError(
            f'unknown FaultChannel: {raw["channel"]!r} '
            f'(허용 = {[c.value for c in FaultChannel]}, {path})'
        ) from exc

    return FaultScenario(
        name=str(raw['name']),
        description=str(raw.get('description', '')),
        channel=channel,
        variant=raw.get('variant'),
        context_kwargs=dict(raw.get('context_kwargs', {})),
        seed=int(raw.get('seed', 42)),
    )


# -------------------------------------------------------------------- context builder


def build_fault_context(
    scenario: FaultScenario,
) -> Tuple[
    Optional[Union[FaultContext, CognitiveLapseContext, AttributeMismatchContext]],
    Optional[Union[FaultVariant, AdversarialVariant, CognitiveLapseVariant, AttributeMismatchVariant]],
]:
    """channel 측 polymorphic context + variant enum 생성.

    YAML 측 list-as-tuple field (e.g., user_position) 측 *명시적 tuple 변환*
    수행 — frozen dataclass invariant 보존. NONE channel 측 (None, None) 반환.

    *channel 별 비대칭* (PR #104 review B-16 명시):
      - HALLUCINATION/ADVERSARIAL: _build_fault_context 측 모든 필드 default
        존재 → context_kwargs={} 도 build 성공 (known_objects={}, user_position
        =(0,0,0), geofence default, etc.). 단 *meaningful trial 아님* — paper
        §C trial 측 known_objects 비어 있으면 referential variant 측 swap 후보
        부재 → no-op fallback. yaml 측 명시 권장.
      - COGNITIVE_LAPSE/ATTRIBUTE_MISMATCH: 필수 키 (initial_target_id /
        vocabulary 등) 없으면 KeyError 즉시 발생.

    Args:
        scenario: load_fault_scenario 측 결과.

    Returns:
        (context_obj, variant_enum) tuple. channel 별 정확한 타입.

    Raises:
        ValueError: variant string 측 channel-specific enum 부재 또는 channel-
            specific dataclass __post_init__ invariant 위반.
        KeyError: context_kwargs 측 channel-specific 필수 키 부재 — COGNITIVE_
            LAPSE 측 ``initial_target_id`` / ``initial_target_name_kr`` /
            ``alternative_target_id`` / ``alternative_target_name_kr`` /
            ATTRIBUTE_MISMATCH 측 ``vocabulary``. HALLUCINATION/ADVERSARIAL
            측 모든 키 default 존재 — KeyError 발생 안 함.
    """
    if scenario.channel == FaultChannel.NONE:
        return None, None

    if scenario.channel == FaultChannel.HALLUCINATION:
        return (
            _build_fault_context(scenario.context_kwargs),
            FaultVariant(scenario.variant),
        )

    if scenario.channel == FaultChannel.ADVERSARIAL:
        return (
            _build_fault_context(scenario.context_kwargs),
            AdversarialVariant(scenario.variant),
        )

    if scenario.channel == FaultChannel.COGNITIVE_LAPSE:
        return (
            _build_cognitive_lapse_context(scenario.context_kwargs),
            CognitiveLapseVariant(scenario.variant),
        )

    if scenario.channel == FaultChannel.ATTRIBUTE_MISMATCH:
        return (
            _build_attribute_mismatch_context(scenario.context_kwargs),
            AttributeMismatchVariant(scenario.variant),
        )

    raise ValueError(f'unknown FaultChannel: {scenario.channel!r}')


# -------------------------------------------------------------------- channel builders


def _build_fault_context(kwargs: Dict[str, Any]) -> FaultContext:
    """hallucination / adversarial 측 공통 FaultContext 생성.

    YAML 측 list field → tuple 변환:
      - known_objects: dict[str, list[3]] → dict[str, tuple]
      - user_position: list[3] → tuple
      - geofence: list[6] → tuple
    """
    known_raw = kwargs.get('known_objects', {})
    known = {
        tid: tuple(pos) for tid, pos in known_raw.items()
    }
    user_pos = tuple(kwargs.get('user_position', (0.0, 0.0, 0.0)))
    geofence = tuple(kwargs.get('geofence', (-3.0, 3.0, -2.0, 2.0, 0.0, 2.4)))
    return FaultContext(
        known_objects=known,
        user_position=user_pos,
        r_min=float(kwargs.get('r_min', 0.7)),
        sigma_llm_nat_cm=float(kwargs.get('sigma_llm_nat_cm', 10.0)),
        geofence=geofence,
    )


def _build_cognitive_lapse_context(
    kwargs: Dict[str, Any],
) -> CognitiveLapseContext:
    """CognitiveLapseContext 측 YAML list → tuple 변환."""
    trigger_range = tuple(kwargs.get('trigger_time_range_s', (3.0, 25.0)))
    silence_range = tuple(kwargs.get('silence_threshold_range_s', (8.0, 15.0)))
    return CognitiveLapseContext(
        initial_target_id=str(kwargs['initial_target_id']),
        initial_target_name_kr=str(kwargs['initial_target_name_kr']),
        alternative_target_id=str(kwargs['alternative_target_id']),
        alternative_target_name_kr=str(kwargs['alternative_target_name_kr']),
        trigger_time_range_s=trigger_range,
        silence_threshold_range_s=silence_range,
    )


def _build_attribute_mismatch_context(
    kwargs: Dict[str, Any],
) -> AttributeMismatchContext:
    """AttributeMismatchContext — vocabulary list 그대로 (tuple 아님)."""
    return AttributeMismatchContext(
        vocabulary=list(kwargs['vocabulary']),
        sigma_ovd_label_swap_rate=float(
            kwargs.get('sigma_ovd_label_swap_rate', 0.05),
        ),
        sigma_ovd_bbox_px=float(kwargs.get('sigma_ovd_bbox_px', 10.0)),
        dangerous_label=str(kwargs.get('dangerous_label', 'person')),
    )
