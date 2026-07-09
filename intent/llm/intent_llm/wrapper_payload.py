"""wrapper_node 측 pure JSON ser/de + IntentInput 빌더 (rclpy 무관).

[wrapper_node.py](wrapper_node.py) 측 ROS 2 노드 측 사용하는 *순수 로직* —
host venv pytest 측 완전 cover (estimator_node / injector_helpers 패턴 정합).

## 출력 payload 계약 (fault/safety-side)

wrapper 의 IntentResult 를 std_msgs/String JSON 으로 직렬화. 키 계약은
*fault/safety 계층* 의 기존 contract 와 정합:

```
{"sigma": <skill value>, "theta": <args dict>, "c": <confidence_raw>, "signals": {...}}
```

- ``sigma`` / ``theta`` — [eval_faults.injector_helpers.typed_action_from_json](../../../eval/faults/eval_faults/injector_helpers.py)
  (hallucination hook 입력) + Tier 2 gate ``/intent/command`` ({sigma, theta, c})
  와 동일 키. (intent_llm 측 TypedAction 은 skill/args 필드지만, 토픽 계약은
  sigma/theta 로 직렬화 — eval_calibration.schemas.TypedAction 정합.)
- ``c`` — confidence_raw. gate 가 소비. injector typed_action_from_json 은 무시.
- ``signals`` — s1/s2/s3 원시 신호. estimator 측 live 신호 wiring (C11-C14) 용.
  injector + gate 모두 무시 (관용적 파싱).

> ⚠️ 다운스트림 주의: hallucination injector 는 sigma/theta 만 재발행
> (typed_action_to_json) → c/signals 는 injector 통과 시 소실. 이는 fault hook
> 설계(action 교란)이며 wrapper 책임 밖. confidence/signal 보존 경로는 별 트랙.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from intent_llm.interface import IntentInput, IntentResult, TypedAction


def build_intent_input(
    utterance: str,
    scenario_id: str,
    context_graph: Optional[Mapping[str, Any]] = None,
) -> IntentInput:
    """utterance + scenario + (선택) context_graph → IntentInput.

    context_graph=None 측 direct mode, dict 측 fusion mode (IntentInput 정합).
    """
    return IntentInput(
        utterance=utterance,
        scenario_id=scenario_id,
        context_graph=context_graph,
    )


def typed_action_payload(action: TypedAction) -> Dict[str, Any]:
    """TypedAction → {"sigma", "theta"} (fault/safety-side 계약 키)."""
    return {'sigma': action.skill.value, 'theta': dict(action.args)}


def result_payload(result: IntentResult) -> Dict[str, Any]:
    """IntentResult → 출력 payload dict ({sigma, theta, c, signals})."""
    payload = typed_action_payload(result.typed_action)
    payload['c'] = float(result.confidence_raw)
    payload['signals'] = dict(result.signals)
    return payload


def serialize_result(result: IntentResult) -> str:
    """IntentResult → JSON string (std_msgs/String payload)."""
    return json.dumps(result_payload(result), ensure_ascii=False)


def referent_class_for(
    target_id: Optional[str],
    context_graph: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """target_id(인스턴스 id) → OVD 클래스 라벨 (context_graph 객체 lookup).

    [ADR-0029](../../../docs/handover/decisions/0029-trial-integration-live-path.md)
    블로커 1 — estimator $s_1$ 지시 대상 매칭은 *클래스* 기준이라(인스턴스 id 는
    검출기 출력 클래스와 입도가 달라 완전일치 불가) wrapper 가 σ 에 클래스를 실어
    보낸다. context_graph 의 ``objects`` 각 항목 ``{'name', 'position', 'ovd_class'}``
    ([scene.scene_objects_for_location](../../../sim/scenario_params/scenario_params/scene.py))
    에서 ``name == target_id`` 객체의 ``ovd_class`` 를 반환.

    Returns:
        OVD 클래스 라벨. context_graph/target_id 부재·객체 미발견·``ovd_class`` 가
        None(검출 어휘 밖)이면 None (→ caller 가 target_id 폴백).
    """
    if not target_id or context_graph is None:
        return None
    objects = context_graph.get('objects')
    if not isinstance(objects, list):
        return None
    for obj in objects:
        if isinstance(obj, Mapping) and obj.get('name') == target_id:
            cls = obj.get('ovd_class')
            return cls if isinstance(cls, str) and cls.strip() else None
    return None


def serialize_result_with_context(
    result: IntentResult,
    context_graph: Optional[Mapping[str, Any]],
) -> str:
    """IntentResult → JSON string, σ.theta 에 ``target_class`` 주입 (ADR-0029 블로커 1).

    ``theta.target_id`` 가 context_graph 에서 OVD 클래스로 해소되면 ``theta``
    (복사본)에 ``target_class`` 를 더해 직렬화한다. 해소 실패 시 ``serialize_result``
    와 동일(추가 키 없음). 기존 sigma/theta/c/signals 키는 불변 — 다운스트림
    (injector·gate·estimator) 의 관용적 파싱과 호환(estimator 만 target_class 소비).
    """
    payload = result_payload(result)
    theta = payload.get('theta')
    if isinstance(theta, dict):
        cls = referent_class_for(theta.get('target_id'), context_graph)
        if cls is not None:
            theta['target_class'] = cls
    return json.dumps(payload, ensure_ascii=False)


def parse_context_graph(json_str: Optional[str]) -> Optional[Dict[str, Any]]:
    """context_graph 토픽 JSON string → dict (빈 문자열/None 측 None).

    Raises:
        ValueError: JSON root 측 dict 아님.
        json.JSONDecodeError: JSON parse 실패.
    """
    if json_str is None:
        return None
    text = json_str.strip()
    if not text:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f'context_graph JSON root 는 dict — got {type(data).__name__}')
    return data
