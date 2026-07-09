# intent_confidence — Mock *의도해석기* (confidence channel only)

Phase 2b B2 검증용. YAML segment (constant / ramp / step) 기반으로
`/intent/grounding_confidence` (`std_msgs/Float32`, $c \in [0, 1]$) publish.

ADR-0005 D3 (intent-agnostic) 정합 — 본 mock 노드는 *의도해석기*-class
인터페이스 reference. 향후 실제 LLM/VLA 백본으로 swap 시 동일 토픽·QoS·메시지
형식 유지하면 tier1_filter (B2)는 변경 없음.

## 시나리오 (`scenarios/`)

- `c_constant_1.yaml` — 상수 $c = 1.0$ → $r = r_\text{min} = 0.9$ m (B1 regression).
- `c_constant_0.yaml` — 상수 $c = 0.0$ → $r = r_\text{max} = 1.5$ m (최대 brake).
- `c_step_down.yaml` — $t=5$ s에서 $1 \to 0$ 즉시 step. tier1_filter 변화율 제한기
  ($\dot{\tilde c}_\text{max} = 0.833$/s)가 발동 → $\tilde c$는 $\sim 1.2$ s에 걸쳐 감속.
- `c_ramp_down.yaml` — 10 s 동안 $1 \to 0$ 선형 ramp ($|\dot c| = 0.1$/s, rate limiter
  미발동 경계). $\tilde c$가 raw $c$를 그대로 추종.

## 실행

```bash
# 단독 실행
ros2 launch intent_confidence confidence_player.launch.py scenario:=c_step_down

# tier1 B2와 함께 (G2 c2 시나리오 + 변조)
TIER1_MODE=b2 ./scripts/up.sh
# (별 터미널에서)
ros2 launch intent_confidence confidence_player.launch.py scenario:=c_step_down
```

## 토픽

| 방향 | 토픽 | 타입 | 주파수 |
|---|---|---|---|
| publish | `/intent/grounding_confidence` | `std_msgs/Float32` | 시나리오의 `publish_rate_hz` (기본 10) |

raw $c$를 그대로 publish. 변화율 제한기는 [tier1_filter](../../safety/tier1/) (B2 모드)
내부에서 작동.

## YAML 스키마

```yaml
name: <str>
description: <str>
publish_rate_hz: <float>      # 기본 10
finish_hover_s: <float>       # 기본 2 (마지막 값 hold)
segments:
  - duration_s: <float>
    type: constant
    value: <float>            # [0, 1]
  - duration_s: <float>
    type: ramp
    from: <float>
    to: <float>
  - duration_s: <float>
    type: step
    value: <float>            # 즉시 점프 후 hold
    note: <str>               # 선택
```
