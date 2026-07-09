# teleop_keyboard

Manual keyboard teleop — WASD ENU velocity → `/cmd/trajectory_setpoint_nominal`
(TwistStamped). g2_waypoint_player 측 **대신** 측 *수동 조종* 측면. ADR-0005
D3 측 intent-agnostic nominal source 정합 — tier1_filter 측 *publisher 무관*
측 동일 인터페이스 측 통과.

> **⚠️ Docker exec 측 `-it` flag 측 필수** — 본 노드 측 termios raw stdin 측
> 사용 측 *interactive PTY* 측 의무. `docker exec` 측 `-it` 없이 시작 측
> `termios.error: Inappropriate ioctl for device` 측 graceful raise + 사용자
> 안내 (PR sim-teleop-keyboard tty fix 정합).

## 키맵

| 키 | 동작 | velocity (default) |
|---|---|---|
| **W / S** | forward / backward | ±0.5 m/s (+x / -x, ENU East) |
| **A / D** | left / right | ±0.5 m/s (+y / -y, ENU North) |
| **R / F** | up / down | ±0.5 m/s (+z / -z) |
| **Q / E** | yaw left / right | ±0.5 rad/s (omega_z) |
| **space** | stop | hover (zero velocity) |
| **Ctrl-C** | quit | — |

키 누른 *마지막 시각* 측 `key_timeout_s` (default 0.5 s) 초과 측 자동 zero
velocity (안전 측 키 떼는 즉시 hover).

## 사용

### 전제

up.sh 측 *PX4 SITL + Gazebo + tier1_filter + g1_offboard* 측 가동 중 (그러나
`g2_waypoint_player` 측 *대신* 측 본 teleop 측 nominal source). 즉 `G2_SCENARIO=`
측 **빈 string** 측 g2 측 자동 시작 *안 함* 측 setup.

**up.sh v6 이상** 측 colcon build 측 `teleop_keyboard` 측 자동 포함 (PR
sim-teleop-keyboard amendment). 이전 빌드 측 *teleop_keyboard 측 install 측
누락* 측 `Package 'teleop_keyboard' not found` 측 error — `git pull origin main`
후 *재 up.sh* 또는 *수동 colcon build* 측 필요:

```bash
# 1. 수동 build (one-liner, -it 불필요 — build 측 stdin 측 무관):
docker exec llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && colcon build --packages-select teleop_keyboard"

# 2. 별 명령 측 ros2 run (-it **필수** — termios raw stdin):
docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 run teleop_keyboard teleop_keyboard_node"
```

**중요**: build 측 *-it 없이* OK이지만 ros2 run 측 *-it 필수* — *한 chain 측
묶지 말 것* (한 chain 측 stdin 측 build 측 line buffer + ros2 run 측 termios
측 *conflict*).

표준 진입:

```bash
# 1. up.sh 측 G2_SCENARIO="" 측 빈 string 측 시작 (g2 측 안 시작):
G2_SCENARIO="" ./scripts/up.sh

# 2. 별 Terminal 측 docker exec -it (interactive tty) 측 teleop 시작:
docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 launch teleop_keyboard teleop_keyboard.launch.py"
```

### 직접 `ros2 run` (launch 측 prefer)

```bash
docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 run teleop_keyboard teleop_keyboard_node"
```

### parameter override 예시

```bash
# tier1 u_max 측 더 큰 측 *위반* 가능성 측 검증 (B1/B2 측 CBF brake 측 확인):
docker exec -it llmdrone-sim /usr/local/bin/entrypoint.sh bash -c \
    "cd /workspace && source install/setup.bash && \
     ros2 launch teleop_keyboard teleop_keyboard.launch.py linear_speed:=1.0"
```

## ENU frame 정합 주의

본 노드 측 키맵 측 *world ENU frame* 측 직접 publish — `frame_id='world'`,
linear_x = +x (East), linear_y = +y (North), linear_z = +z (Up).

드론 측 *yaw 측 회전* 시 측 사용자 측 *직관* (W = "forward") 측 *world frame +x
방향* (East) 측 mismatch 발생 — 드론 측 yaw 측 East 측 향함 시 측 직관 정합,
다른 yaw 측 측면 측 mismatch. 드론 측 yaw 측 East 측 reset 측 G1/PX4 측 책임
(또는 사용자 측 자동 yaw correction 측 *body frame teleop* 측 후속 PR 후보).

## up.sh 통합 *안 함* — 이유

`docker exec -d` (detached) 측 *non-interactive* — termios raw stdin 측 사용
*불가*. up.sh 측 §7 측 g2_waypoint_player 측 자동 시작 측 동일 패턴 측 *interactive
tty* 측 부재 측 측 teleop 측 호출 측 stdin 측 hang. 따라서 *별 명령 측 사용자
직접 시작* 패턴 (PatientWheelChair Fuel mesh 측 동일 — 외부 트랙).

후속 PR 측 *up.sh 측 MANUAL=1 env override* 측 *g2 측 skip + 사용자 측 별 명령
prompt* 측 통합 가능 (ROADMAP backlog 후보).

## 토픽 흐름

```
teleop_keyboard_node
    ↓ /cmd/trajectory_setpoint_nominal (TwistStamped)
tier1_filter (mode=b0/b1/b2)
    ↓ /cmd/trajectory_setpoint_safe
g1_offboard
    ↓ PX4 SITL → drone
```

ADR-0011 D1 정합 — g2_waypoint_player 측 *동일* 토픽 인터페이스. 즉 teleop
↔ g2 측 *swappable* (사용자 측 별 명령 측 시작 측 결정).

## paper-1 scope 영향

**없음** — 본 패키지 측 *수동 조종* 측 *별 트랙* (sim 인프라 점검 / 데모 / 시각
검증 측면). paper §C 본실험 측 *nominal source* 측 g2_waypoint_player 또는
intent/llm wrapper 측 사용 — teleop 측 *제외*.
