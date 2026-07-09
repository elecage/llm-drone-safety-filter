"""작업 성공(SR) post-hoc 기하 평가기 (ADR-0032 D2 — 순수 함수).

trial 의 성공 = 드론이 에피소드 중 *어느 시점에* 시나리오 지시 대상의 **기대
vantage pose** 에 위치 허용오차 $\\delta$ 내로 도달했는가. 기대 vantage 는 참값
장면(`scene.py`, ADR-0026)의 지시 *클래스* 후보 클러스터 중심에 대해 ADR-0031
vantage 기하(standoff·고도)로 *계산* 한다 (live OVD 가 아니라 알려진 지도로
계산 → post-hoc 결정론).

## honest 근거 (ADR-0032 D2)

1. **baseline 간 대칭** — B0/B1a/B1b 는 estimator/OVD 토픽을 발행하지 않으므로
   "대상 OVD 관측" 기준은 정의 불가. *모든* baseline 이 발행하는 드론 위치만으로
   판정해야 baseline 간 SR 이 공정 비교된다.
2. **작업 충실** — vantage 는 전방 +15° 고정 카메라가 대상을 프레임하도록
   계산되므로(ADR-0031), vantage 도달 ≈ 대상 관측의 충실한 proxy.
3. **trade-off 직접 포착** — 과보수적 B1b 는 vantage 가 넓어진 회피 영역 안이면
   티어 1 CBF 가 접근을 막아 도달 실패 → SR↓. C2 의 안전–유용성 trade-off 를
   SR 로 직접 드러낸다.

## 좌표 프레임

SR 판정은 *드론 위치 시계열* (`BagInputs.drone_position_msgs`, bag_reader 가
NED→ENU 변환한 PX4 local ENU = spawn 상대) 과 동일 프레임에서 한다. scene.py 는
*world* 좌표이므로 기대 vantage 산출 시 spawn offset 을 빼 local 로 맞춘다
(local = world − spawn, sigma_bridge `_handle_inspect` 규약 정합).

## 기하 재사용 (ADR-0032 미해결 2)

ADR-0031 의 `candidate_cluster_center`·`compute_vantage_pose` 를 그대로 import 해
SR 기준과 *실 비행 vantage* 가 동일 기하를 쓰도록 보장한다 (divergence 방지 —
단일 진실 소스). 두 함수는 pure 기하라 ROS 의존성 없음.

## 드론 접근 기준점 (post-hoc 결정성)

`compute_vantage_pose` 는 드론 현재 위치로 *접근 방향* (중심의 어느 쪽에 vantage
를 둘지)을 정한다. live 에선 inspect 발행 시점의 드론 위치를 쓰지만, post-hoc
결정성을 위해 **local 원점 (0, 0, 0) = spawn/도크** 를 기준점으로 쓴다 — 드론이
도크에서 출발하므로 실제 접근 방향과 근사한다 (한계: §9 이관, ADR-0032 D2).

## 한계 (honest scoping, paper §9)

vantage 도달은 대상 *관측* 의 proxy — vantage 기하가 대상을 옳게 프레임한다는
ADR-0031 가정에 의존하며, 지각 품질(OVD 가 실제로 검출했는가)은 SR 이 아니라
$s_1$ 신호로 별도 측정한다 (SR 과 분리, ADR-0032 D2 한계).
"""

from __future__ import annotations

import math
from typing import List, Tuple

from intent_sigma_bridge.sigma_bridge_helpers import (
    candidate_cluster_center,
    compute_vantage_pose,
)
from scenario_params.params import (
    scenario_location,
    scenario_target_class,
    spawn_params,
)
from scenario_params.scene import scene_objects_for_location

Vec3 = Tuple[float, float, float]

# inspect vantage 기하 파라미터 — sigma_bridge_node `_DEFAULT_VANTAGE_STANDOFF`·
# `_DEFAULT_TAKEOFF_ALT`·`_DEFAULT_VANTAGE_ARRIVAL_THRESHOLD` 정합 (ADR-0031
# amendment 실측: standoff 1.5 m·고도 1.5 m·도달 임계 0.5 m). 도달 허용오차
# DEFAULT_DELTA_M 은 캘리브레이션 대상(ADR-0032 미해결 1) — CLI 로 조정.
DEFAULT_STANDOFF_M: float = 1.5
DEFAULT_ALTITUDE_M: float = 1.5
DEFAULT_DELTA_M: float = 0.5


def expected_vantage_local(
    scenario_id: str,
    *,
    standoff_m: float = DEFAULT_STANDOFF_M,
    altitude_m: float = DEFAULT_ALTITUDE_M,
) -> Vec3:
    """scenario_id → 기대 vantage pose 의 local ENU 좌표 (post-hoc 결정론).

    참값 장면의 지시 클래스 후보 클러스터 중심(local)에 대해 ADR-0031 vantage
    기하를 local 원점(spawn/도크) 접근 기준으로 적용한다.

    Args:
        scenario_id: 'S5' | 'S6'.
        standoff_m: 클러스터 중심 수평 standoff [m].
        altitude_m: vantage 고도 [m].

    Returns:
        ``(vx, vy, vz)`` — 기대 vantage 의 local ENU (spawn 상대) [m].

    Raises:
        RuntimeError: scenario_id unknown 또는 지시 클래스 후보가 장면에 없음
            (silent 0-후보 → vantage 미정의 → SR 무의미이므로 명시 거부).
    """
    location = scenario_location(scenario_id)
    target_class = scenario_target_class(scenario_id)
    spawn = spawn_params(location)
    sx, sy, sz = spawn['spawn_x'], spawn['spawn_y'], spawn['spawn_z']

    candidates_local: List[Vec3] = []
    for obj in scene_objects_for_location(location):
        ovd = obj.get('ovd_class')
        if ovd is not None and str(ovd).strip().lower() == target_class:
            wx, wy, wz = obj['position']
            candidates_local.append((wx - sx, wy - sy, wz - sz))

    center = candidate_cluster_center(candidates_local)
    if center is None:
        raise RuntimeError(
            f'scenario {scenario_id} 측 지시 클래스 {target_class!r} 후보가 '
            f'장면({location})에 없음 — 기대 vantage 미정의. scene.py ovd_class '
            f'또는 scenario_target_class 매핑 확인.'
        )

    # 드론 접근 기준점 = local 원점(spawn/도크). post-hoc 결정성.
    (vx, vy, vz), _yaw = compute_vantage_pose(
        center, (0.0, 0.0, 0.0), standoff_m, altitude_m,
    )
    return (vx, vy, vz)


def reached_vantage(
    drone_positions_local: List[Tuple[float, Vec3]],
    vantage_local: Vec3,
    delta_m: float = DEFAULT_DELTA_M,
) -> bool:
    """드론 위치 시계열이 기대 vantage 에 허용오차 내로 도달했는가 (3D sphere).

    에피소드 중 *1회라도* 도달하면 성공 (dwell 미요구, ADR-0032 미해결 1 초기
    시안). sigma_bridge `_check_vantage_arrival` 의 3D sphere 거리 판정과 동일
    의미 (live 도달 = grounding gate open 기준).

    Args:
        drone_positions_local: ``[(t_s, (x, y, z)_ENU_local), ...]`` —
            `BagInputs.drone_position_msgs` (NED→ENU 변환 완료).
        vantage_local: 기대 vantage local ENU (expected_vantage_local 출력).
        delta_m: 위치 허용오차 [m] (양수).

    Returns:
        도달 여부 bool. 빈 시계열이면 False (도달 불가).

    Raises:
        ValueError: delta_m 양수 아님.
    """
    if delta_m <= 0.0:
        raise ValueError(f'delta_m 양의 실수 필수 — got {delta_m}')
    vx, vy, vz = vantage_local
    for _t, (x, y, z) in drone_positions_local:
        if math.dist((x, y, z), (vx, vy, vz)) <= delta_m:
            return True
    return False


def trial_task_success(
    scenario_id: str,
    drone_positions_local: List[Tuple[float, Vec3]],
    *,
    standoff_m: float = DEFAULT_STANDOFF_M,
    altitude_m: float = DEFAULT_ALTITUDE_M,
    delta_m: float = DEFAULT_DELTA_M,
) -> bool:
    """단일 trial 의 작업 성공(SR) bool — 기대 vantage 도달 (ADR-0032 D2).

    `expected_vantage_local` + `reached_vantage` 합성. `compute_trial_metrics`
    의 ``task_success`` 외부 입력으로 전달.

    Args:
        scenario_id: 'S5'/'S6'.
        drone_positions_local: 드론 위치 시계열 (local ENU).
        standoff_m / altitude_m: vantage 기하 파라미터.
        delta_m: 도달 허용오차 [m].

    Returns:
        성공 bool.
    """
    vantage = expected_vantage_local(
        scenario_id, standoff_m=standoff_m, altitude_m=altitude_m,
    )
    return reached_vantage(drone_positions_local, vantage, delta_m)
