"""injector_node 측 pure JSON ser/de helpers (rclpy 무관).

A3-3 트랙 [signal_scenario.py](../../intent/confidence/intent_confidence/signal_scenario.py)
정합 패턴 — pure logic + ROS 2 wrapper 분리. 본 모듈 측 host venv 측 단위
테스트 가능.

ROS 2 측 정의 측 dataclass(TypedAction / LapseEvent / prompt str) 와
*std_msgs/String* 측 JSON 직렬화 변환. (Detection 채널은 ADR-0029 D-A5 로
vision_msgs/Detection2DArray 직접 통신으로 전환 — detection_bridge 참조.)

호출 측 ([injector_node.py](injector_node.py)) 측 ros msg ↔ dataclass round-trip.
"""

from __future__ import annotations

import json

from eval_calibration.schemas import TypedAction

from eval_faults.schemas import (
    CognitiveLapseVariant,
    LapseEvent,
)


# -------------------------------------------------------------------- TypedAction


def typed_action_to_json(action: TypedAction) -> str:
    """TypedAction → JSON string (std_msgs/String 측 payload)."""
    return json.dumps({'sigma': action.sigma, 'theta': action.theta})


def typed_action_from_json(s: str) -> TypedAction:
    """JSON string → TypedAction.

    Raises:
        ValueError: JSON parse 실패 또는 sigma 측 ADR-0013 D2 카탈로그 외.
        KeyError: ``sigma`` 또는 ``theta`` 키 부재.
    """
    data = json.loads(s)
    if not isinstance(data, dict):
        raise ValueError(f'JSON root 는 dict — got {type(data).__name__}')
    return TypedAction(sigma=data['sigma'], theta=data['theta'])


# Detection JSON ser/de 는 폐기 (ADR-0029 D-A5) — attribute_mismatch 가 String JSON
# 대신 vision_msgs/Detection2DArray 로 실 OVD 파이프라인과 직접 통신. 메시지↔내부
# Detection 변환은 detection_bridge 로 이동.


# -------------------------------------------------------------------- LapseEvent


def lapse_event_to_json(event: LapseEvent) -> str:
    """LapseEvent → JSON string. variant enum 측 ``.value`` 직렬화."""
    return json.dumps({
        'variant': event.variant.value,
        'trigger_time_s': event.trigger_time_s,
        'initial_utterance': event.initial_utterance,
        'follow_up_utterance': event.follow_up_utterance,
        'silence_threshold_s': event.silence_threshold_s,
        'raw_c_after_event': event.raw_c_after_event,
    })


def lapse_event_from_json(s: str) -> LapseEvent:
    """JSON string → LapseEvent.

    Raises:
        ValueError: variant string 측 CognitiveLapseVariant 부재 또는
            LapseEvent invariant 위반.
        KeyError: 필수 키 부재.
    """
    data = json.loads(s)
    if not isinstance(data, dict):
        raise ValueError(f'JSON root 는 dict — got {type(data).__name__}')
    return LapseEvent(
        variant=CognitiveLapseVariant(data['variant']),
        trigger_time_s=data['trigger_time_s'],
        initial_utterance=data['initial_utterance'],
        follow_up_utterance=data.get('follow_up_utterance'),
        silence_threshold_s=data.get('silence_threshold_s'),
        raw_c_after_event=data.get('raw_c_after_event'),
    )
