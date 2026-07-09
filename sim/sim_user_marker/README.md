# sim_user_marker

사용자 위치 TF + 회피 영역 RViz 마커 공급 노드.

## 의도

S6/S5 시뮬레이션에서 paper-1 §3·§5의 사용자 회피 영역을 시각·논리적으로 대표하는
최소 컴포넌트. [CLAUDE.md §A4](../../CLAUDE.md) "사용자 아바타 = 좌표 프레임만
(TF + RViz 마커) only" 결정의 구현.

## 빌드

```
colcon build --packages-select sim_user_marker
source install/setup.bash
```

## 실행

```
ros2 run sim_user_marker user_marker_node
```

기본 파라미터 (v3 layout, 사용자 머리 (-2.6, 1.5, 1.1), r_min = 0.9 m)는 코드 안에
defaults로 박혀 있다. r_min=0.9는 cmsm-proof §7.1 P1 (2026-05-25 갱신): r_drone (0.142) +
d_brake (0.025) + b_human (0.75, Duncan & Murphy 2013 passing upper bound) ≈ 0.917 → 0.9 round.
파라미터로 덮어쓸 수 있다:

```
ros2 run sim_user_marker user_marker_node --ros-args \
  -p user_x:=-2.6 -p user_y:=1.5 -p user_z:=1.1 -p r_min:=0.9
```

## 발행

| 토픽 | 타입 | 비고 |
|---|---|---|
| `/tf_static` | `tf2_msgs/TFMessage` | `world → user` 정적 변환. 1회 발행 (TRANSIENT_LOCAL). |
| `/user_avoidance_zone` | `visualization_msgs/Marker` | `user` frame 중심 sphere, 지름 = 2 · r_min. 주기 = `marker_period_s` (기본 0.5 s). |

## 헤드리스 확인

```
ros2 run sim_user_marker user_marker_node &
ros2 topic echo --once /tf_static
ros2 topic echo --once /user_avoidance_zone
```

## RViz 확인 (XQuartz 등 X 서버 필요)

`Fixed Frame = world` 설정 후 TF · Marker (`/user_avoidance_zone`) 디스플레이 추가.

## 한계 (1차 의도된 범위)

- 사용자 *움직임*은 모델링하지 않음. S6/S5는 휠체어 고정 가정.
- 마커는 *시각화* 용. 실제 안전 거리 계산은 별도 티어 1 노드(작업 #5)가
  `world → user` TF와 드론 위치를 받아 수행.
- `r_min` 외 `r(c)`·`r_max`도 따로 시각화하려면 마커 노드 확장이 필요. 1차엔
  단조성-하한 (r_min) 한 개만.
