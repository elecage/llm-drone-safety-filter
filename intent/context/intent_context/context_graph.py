"""context graph 조립 — scenario → 장면 dict (pure logic).

[context_graph_publisher.py](context_graph_publisher.py) 측 ROS 2 노드가 사용하는
*순수 로직* — host venv pytest 완전 cover. 시나리오 식별자로부터 장면 context
graph 를 조립 (장소 + 사용자 위치 + 장면 객체 + 옵션 드론 위치), wrapper_node(fusion)
측 LLM prompt 주입용.

## 데이터 출처 (단일 진실 소스)

- scenario → location: [scenario_params.params.scenario_location](../../../sim/scenario_params/scenario_params/params.py)
- user world 위치: scenario_params.params.user_marker_params
- 장소별 장면 객체: [scenario_params.scene.scene_objects_for_location](../../../sim/scenario_params/scenario_params/scene.py)
- 드론 world 위치: PX4 vehicle_local_position(NED) → ENU + spawn offset (publisher 측 계산)

## 스키마

```
{
  "scenario": "S5",
  "location": "livingroom",
  "user_position": [x, y, z],          # world frame [m]
  "objects": [{"name": str, "position": [x, y, z]}, ...],
  "drone_position": [x, y, z],         # world frame ENU [m] — 옵션
                                       # publisher 측 PX4 위치 수신 시만 포함.
                                       # 순수 방향 명령(left/forward 등) 해석 시
                                       # LLM 이 기준점으로 사용 (_llm_prompt 참조).
}
```

[intent_llm._llm_prompt.build_messages](../../../intent/llm/intent_llm/_llm_prompt.py)
측 ``Context: {JSON}`` 으로 직렬화 → 지시 대상 해석(referent grounding) 입력.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence

from scenario_params.params import scenario_location, user_marker_params
from scenario_params.scene import scene_objects_for_location


def build_context_graph(
    scenario_id: str,
    drone_world_position: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """scenario_id → 장면 context graph dict.

    Args:
        scenario_id: 'S5' | 'S6' | 'S7' | 'S8'.
        drone_world_position: 옵션 — 드론 현재 world ENU 좌표 [x, y, z].
            제공되면 결과 dict 에 'drone_position' 필드로 포함 → LLM 이 순수
            방향 명령("left" 등) 해석 시 기준점으로 사용. None 이면 누락.

    Returns:
        dict — scenario / location / user_position / objects [+ drone_position].

    Raises:
        RuntimeError: scenario_id 측 unknown (scenario_location propagate).
        ValueError: drone_world_position 측 3원소 아님.
    """
    location = scenario_location(scenario_id)
    user = user_marker_params(location)
    graph: Dict[str, Any] = {
        'scenario': scenario_id,
        'location': location,
        'user_position': [user['user_x'], user['user_y'], user['user_z']],
        'objects': scene_objects_for_location(location),
    }
    if drone_world_position is not None:
        pos = list(drone_world_position)
        if len(pos) != 3:
            raise ValueError(
                f'drone_world_position 측 [x,y,z] 3원소 필수 — 받음: {pos!r}'
            )
        graph['drone_position'] = [float(v) for v in pos]
    return graph


def serialize_context_graph(graph: Dict[str, Any]) -> str:
    """context graph dict → JSON string (std_msgs/String payload)."""
    return json.dumps(graph, ensure_ascii=False)
