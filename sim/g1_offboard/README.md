# g1_offboard

G1 minimum offboard control 노드. PX4 SITL을 arm + OFFBOARD 모드로 진입시키고,
ENU velocity nominal 토픽을 PX4 `TrajectorySetpoint` (NED)로 변환·publish한다.

**ADR**: [ADR-0011](../../docs/handover/decisions/0011-g1-offboard-interface.md) — G1
인터페이스 4결정 (nominal 토픽 분리 · velocity 레벨 · ENU↔NED 변환 자리 · use_sim_time).

## 토픽 wiring

```
[nominal source — scripted player / teleop / 의도해석기]
  ↓
/cmd/trajectory_setpoint_nominal   (TwistStamped, ENU)
  ↓
[티어1 안전 필터 — 미구현 시 pass-through]
  ↓
/cmd/trajectory_setpoint_safe      (TwistStamped, ENU)  ← 이 노드의 입력
  ↓
[g1_offboard: ENU→NED + PX4 packing]
  ↓
/fmu/in/trajectory_setpoint        (px4_msgs/TrajectorySetpoint, NED)
```

티어1 미구현 단계에선 nominal 소스에서 `/cmd/trajectory_setpoint_safe`를
직접 publish해도 됨 (또는 `input_topic` 파라미터로 `_nominal`을 직접 지정).

## State machine

| 상태 | 동작 | 전이 조건 |
|------|------|-----------|
| INIT | offboard mode stream + hover setpoint publish | `arming_warmup_s` 경과 |
| ARMING | OFFBOARD + ARM 명령 송신, hover 유지 | `vehicle_status.arming_state == ARMED` 및 `nav_state == OFFBOARD` |
| CLIMB | climb velocity 명령 | local position z (NED) 기준 목표 고도 도달 |
| ACTIVE | nominal forward (없으면 hover) | 종료까지 |

## 파라미터

| 이름 | 기본 | 의미 |
|------|------|------|
| `input_topic` | `/cmd/trajectory_setpoint_safe` | 구독할 nominal 토픽 (ENU TwistStamped) |
| `publish_rate_hz` | `20.0` | offboard_control_mode + trajectory_setpoint publish 주파수 |
| `takeoff_altitude_m` | `1.5` | CLIMB 종료 고도 |
| `climb_velocity_mps` | `1.0` | CLIMB 단계 z (Up) 속도 |
| `altitude_tolerance_m` | `0.2` | CLIMB 종료 판정 tolerance |
| `arming_warmup_s` | `1.0` | INIT → ARMING 사이 setpoint stream warm-up 시간 |
| `nominal_timeout_s` | `0.5` | ACTIVE 단계에서 nominal silence 허용 시간 (초과 시 hover fallback) |

## 실행

T1 PX4 SITL + T2 gz GUI + T3 컨테이너 (`./docker/run.sh`로 MicroXRCEAgent + ROS 2)가
이미 가동 중이어야 한다 (F 스모크 환경).

```bash
./docker/run.sh "colcon build --packages-select g1_offboard && \
    source install/setup.bash && \
    ros2 launch g1_offboard g1_offboard.launch.py"
```

## 검증

`scripts/check_g1_smoke.sh` — INIT → ARMING → CLIMB → ACTIVE state 전이를 텔레메트리로
확인 + 외부 nominal velocity 흘려보냈을 때 위치 변화 검증.

## 참조

- [ADR-0011](../../docs/handover/decisions/0011-g1-offboard-interface.md)
- [ADR-0005](../../docs/handover/decisions/0005-paper1-framing.md) (intent-agnostic framing)
- [ADR-0010](../../docs/handover/decisions/0010-e1-uxrce-dds-bridge.md) (uXRCE-DDS 결합)
- [RESEARCH_CONTEXT §B6](../../docs/RESEARCH_CONTEXT.md) (1차 논문 정형 골격)
