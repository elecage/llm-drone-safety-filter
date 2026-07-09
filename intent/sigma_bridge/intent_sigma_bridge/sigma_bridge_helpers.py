"""sigma_bridge_node 보조 함수 — ROS 2 의존성 격리.

본 모듈은 pure 수학 함수만 포함하므로 host venv pytest 로 단위 검증 가능
(rclpy import 불요).

## 우회 waypoint inject (paper §C ADR-0028 sigma_bridge 책임 확장)

`_publish_pose_guarded` 의 기존 동작 = *target 자체* 가 사용자 회피 영역 안
일 때 *drone↔user 직선의 drone 쪽 r_guard 외곽점* 으로 projection (radial).

본 모듈 신규 = *target 은 회피 영역 밖이지만 drone→target 직선 segment 가
회피 영역과 교차* 하는 경우 → drone 이 사용자 정면 saddle 에 멈추는
*CBF local minimum* 문제 회피용 *수평 우회 waypoint inject*.

수직 차이 dz ≈ 0.4 m (사용자 z=1.1, drone hover z=1.5) < r_guard=1.5 라
3D segment-sphere 교차 검사 + *수평 plane* 우회 waypoint 가 자연.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from scenario_params.scene import expand_ovd_synonyms

Vec3 = Tuple[float, float, float]


def _segment_closest_distance_to_point(
    seg_a: Vec3, seg_b: Vec3, point: Vec3
) -> Tuple[float, Vec3]:
    """3D segment [seg_a, seg_b] 와 point 의 최단 거리 + closest point 반환.

    t∈[0,1] 로 clamp 한 parametric closest. zero-length segment 는 seg_a 반환.
    """
    dx = seg_b[0] - seg_a[0]
    dy = seg_b[1] - seg_a[1]
    dz = seg_b[2] - seg_a[2]
    seg_len_sq = dx * dx + dy * dy + dz * dz
    if seg_len_sq < 1e-12:
        # zero-length — seg_a 와 point 거리
        d = math.sqrt(
            (point[0] - seg_a[0]) ** 2
            + (point[1] - seg_a[1]) ** 2
            + (point[2] - seg_a[2]) ** 2
        )
        return d, seg_a
    t = (
        (point[0] - seg_a[0]) * dx
        + (point[1] - seg_a[1]) * dy
        + (point[2] - seg_a[2]) * dz
    ) / seg_len_sq
    t = max(0.0, min(1.0, t))
    cx = seg_a[0] + t * dx
    cy = seg_a[1] + t * dy
    cz = seg_a[2] + t * dz
    d = math.sqrt(
        (point[0] - cx) ** 2 + (point[1] - cy) ** 2 + (point[2] - cz) ** 2
    )
    return d, (cx, cy, cz)


_DETOUR_MIN_CLEARANCE_RATIO = 1.2  # drone/goal 이 user 회피 영역에서 이만큼 떨어져야 단일 waypoint 우회 가능
_DETOUR_R_MAX_RATIO = 3.0  # 우회 반경 최대 (= r_guard × ratio)
_DETOUR_R_STEP_RATIO = 0.15  # iterative 증가 step


def compute_detour_waypoint(
    drone: Vec3,
    goal: Vec3,
    user: Vec3,
    r_guard: float,
) -> Optional[Vec3]:
    """drone→goal 직선 segment 가 user 회피 영역 (반경 r_guard) 과 교차하면
    *수평 우회 waypoint* 반환, 아니면 None.

    Args:
        drone: 현재 드론 ENU local 위치 (x, y, z) [m].
        goal: 목표 setpoint ENU local 위치 (x, y, z) [m].
        user: 사용자 ENU local 위치 (x, y, z) [m].
        r_guard: 사용자 회피 영역 가드 반경 [m] (sigma_bridge user_guard_radius_m
            정합 — paper §C ADR-0028 데모 운용 가드. r_min=0.9 < r_guard=1.5 라
            tier1 CBF 활성 전 sigma_bridge 가 setpoint 단에서 차단).

    Returns:
        우회 waypoint (wx, wy, drone_z) — 수평 우회, 고도는 drone 현재 유지.
        다음의 경우 None (호출측 fallback 동작):
        - 직선이 회피 영역과 안 가르면 (이미 안전)
        - drone≈goal (이동 없음)
        - drone 또는 goal 이 user 와 너무 가까움 (xy 거리 <
          ``_DETOUR_MIN_CLEARANCE_RATIO`` × r_guard) — 단일 waypoint 우회의
          기하학적 한계. 호출측은 기존 projection 또는 hover 로 fallback.
        - iterative 증가가 ``_DETOUR_R_MAX_RATIO`` × r_guard 까지 두 leg
          모두 안전한 r 을 못 찾음.

    알고리즘:
        1. 3D segment-sphere 교차 검사 → 안전이면 None.
        2. drone→goal 의 xy 단위 수직 벡터 n̂. user 의 *반대편* 부호 선택
           (segment 가 user 를 어느 옆으로 비껴가야 하는지).
        3. drone-user 와 goal-user 의 xy 거리가 둘 다 r_guard 의 ratio 배
           이상이어야 단일 waypoint 우회가 기하학적으로 가능. 못 만족하면 None.
        4. r = r_guard 부터 시작해 step 만큼 증가시키며 candidate w = user
           + sign·r·n̂. drone→w 와 w→goal 두 leg 의 user closest 거리가
           모두 r_guard 이상인 첫 r 채택.
        5. 최대 r 까지 못 찾으면 None.

    수치 한계 (단일 waypoint 우회):
        drone 과 user 사이 거리 D, segment 수직 user-옆 거리 R 일 때
        segment-user closest = R·D / sqrt(D² + R²). 이 값이 r_guard 이상이
        되려면 R ≥ r_guard·D / sqrt(D² − r_guard²). D 가 r_guard 에 가까우면
        R 가 발산 → 단일 waypoint 로 우회 불가능. ratio 1.2 는 D ≥ 1.2·r_guard
        보장 시 R ≤ 1.81·r_guard 로 한정 (max ratio 3.0 안에 수렴).
    """
    if r_guard <= 0.0:
        return None

    d_closest, _ = _segment_closest_distance_to_point(drone, goal, user)
    if d_closest >= r_guard:
        return None

    dx = goal[0] - drone[0]
    dy = goal[1] - drone[1]
    seg_xy_len_sq = dx * dx + dy * dy
    if seg_xy_len_sq < 1e-12:
        # xy 동일 — 순수 수직 segment. 수평 우회 의미 없음.
        return None

    seg_xy_len = math.sqrt(seg_xy_len_sq)
    nx = -dy / seg_xy_len
    ny = dx / seg_xy_len

    # user 가 segment 의 어느 옆?  (offset_n 부호) → 반대편으로 우회.
    offset_n = (user[0] - drone[0]) * nx + (user[1] - drone[1]) * ny
    if abs(offset_n) < 1e-9:
        # user 가 segment 위 (정렬) — sign 임의 (+1) 로 결정성.
        sign_avoid = 1.0
    else:
        sign_avoid = -1.0 if offset_n > 0.0 else 1.0

    # 단일 waypoint 우회 가능성 검사 — drone/goal 이 user 와 너무 가까우면
    # 기하학적으로 불가능 (segment 의 일부가 항상 r_guard 안).
    d_drone_user_xy = math.sqrt(
        (user[0] - drone[0]) ** 2 + (user[1] - drone[1]) ** 2
    )
    d_goal_user_xy = math.sqrt(
        (user[0] - goal[0]) ** 2 + (user[1] - goal[1]) ** 2
    )
    min_clearance = _DETOUR_MIN_CLEARANCE_RATIO * r_guard
    if d_drone_user_xy < min_clearance or d_goal_user_xy < min_clearance:
        return None

    # iterative r 증가: 첫 안전 r 채택.
    eps = 1e-6
    attempt_r = r_guard
    max_r = _DETOUR_R_MAX_RATIO * r_guard
    step = _DETOUR_R_STEP_RATIO * r_guard

    while attempt_r <= max_r + eps:
        wx = user[0] + sign_avoid * attempt_r * nx
        wy = user[1] + sign_avoid * attempt_r * ny
        candidate: Vec3 = (wx, wy, drone[2])
        d1, _ = _segment_closest_distance_to_point(drone, candidate, user)
        d2, _ = _segment_closest_distance_to_point(candidate, goal, user)
        if d1 >= r_guard - eps and d2 >= r_guard - eps:
            return candidate
        attempt_r += step

    return None


def compute_radial_escape(
    drone: Vec3,
    user: Vec3,
    r_guard: float,
    target_clearance: float,
) -> Optional[Vec3]:
    """drone 을 user 에서 *xy 직선으로 멀어지는 방향* 으로 밀어내 user 와의
    xy 거리가 ``target_clearance`` 가 되는 *수평 탈출 waypoint* 반환.

    용도: drone 이 회피 영역 경계(예: "사용자에게 와" 직후 radial projection
    으로 d_drone_user = r_guard)에 있어 단일 우회 waypoint 가 기하학적으로
    불가능할 때(``compute_detour_waypoint`` 가 None), *먼저* 사용자에게서
    멀어져 클리어런스를 확보하는 선행 leg. 이 leg(drone→escape)는 user 로부터
    반경 방향 바깥이라 segment 의 user 최근접점이 drone 현재 거리(= r_guard)
    이상으로 단조 증가 → **항상 회피 영역 밖**(추가 침범 없음).

    Args:
        drone: 현재 드론 ENU local 위치 (x, y, z) [m].
        user: 사용자 ENU local 위치 (x, y, z) [m].
        r_guard: 사용자 회피 영역 가드 반경 [m]. 0 이하면 None(가드 비활성).
        target_clearance: 탈출 후 user 와의 목표 xy 거리 [m]. 후속
            ``compute_detour_waypoint`` 가 성공하려면 그 함수의 최소
            클리어런스(``_DETOUR_MIN_CLEARANCE_RATIO`` × r_guard)보다 커야
            하므로 호출측이 약간의 여유를 둬 전달한다.

    Returns:
        탈출 waypoint (ex, ey, drone_z) — 고도는 drone 현재 유지.
        다음의 경우 None:
        - r_guard ≤ 0 (가드 비활성).
        - drone 의 user 대비 xy 거리가 이미 target_clearance 이상(탈출 불요).
        - drone 이 user 와 xy 상 거의 일치(방향 미정의) — 호출측 hover fallback.
    """
    if r_guard <= 0.0:
        return None
    dx = drone[0] - user[0]
    dy = drone[1] - user[1]
    d_xy = math.sqrt(dx * dx + dy * dy)
    if d_xy >= target_clearance:
        return None
    if d_xy < 1e-6:
        # drone 이 user 바로 위 — 반경 방향 미정의.
        return None
    scale = target_clearance / d_xy
    ex = user[0] + dx * scale
    ey = user[1] + dy * scale
    return (ex, ey, drone[2])


def distance_3d(a: Vec3, b: Vec3) -> float:
    """3D 유클리드 거리."""
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


# move_to 결정론 해석 (ADR-0027 amendment — LLM 좌표 직접 출력 폐기).
# LLM 은 *의미 선택* (target_id 객체명 / direction 토큰)만 출력하고, 좌표 산출은
# 본 결정론 함수가 담당 → 작은 모델(gemma 등)의 좌표 환각(Y 부호 뒤집힘·옆 객체
# 좌표) 을 구조적으로 제거. world ENU 규약: +X=East, +Y=North, +Z=Up.
VALID_DIRECTIONS: Tuple[str, ...] = (
    'forward', 'back', 'left', 'right', 'up', 'down',
)
_DIRECTION_OFFSETS: dict = {
    'forward': (0.0, 2.0, 0.0),   # +Y (North)
    'back': (0.0, -2.0, 0.0),     # −Y
    'left': (-2.0, 0.0, 0.0),     # −X
    'right': (2.0, 0.0, 0.0),     # +X
    'up': (0.0, 0.0, 1.0),        # +Z
    'down': (0.0, 0.0, -1.0),     # −Z
}


def direction_offset(token: Optional[str]) -> Optional[Vec3]:
    """방향 토큰 → 상대 오프셋 (수평 2 m, 수직 1 m). 무효 토큰은 None.

    대소문자·앞뒤 공백 무시. 호출측은 drone 현재 위치에 더해 setpoint 산출.
    """
    if not token:
        return None
    return _DIRECTION_OFFSETS.get(str(token).strip().lower())


def lookup_object_position(
    target: Optional[str], scene_objects: list
) -> Optional[Vec3]:
    """객체 이름 → world 좌표 (대소문자·공백 무시 정확 일치). 미발견 None.

    Args:
        target: 객체 이름 (예: 'sofa'). LLM 이 Context.objects 에서 복사한 이름.
        scene_objects: ``[{'name': str, 'position': [x, y, z]}, ...]``
            (scenario_params.scene.scene_objects_for_location 산출).

    Returns:
        (x, y, z) world ENU [m] 또는 None.
    """
    if not target:
        return None
    key = str(target).strip().lower()
    for obj in scene_objects:
        if str(obj.get('name', '')).strip().lower() == key:
            pos = obj.get('position')
            if pos and len(pos) >= 3:
                return (float(pos[0]), float(pos[1]), float(pos[2]))
    return None


def has_arrived(
    drone: Vec3, target: Vec3, threshold_m: float
) -> bool:
    """drone 이 target 에 도달했는지 — 3D sphere 거리 < threshold_m."""
    return distance_3d(drone, target) < threshold_m


def is_segment_intersecting_sphere(
    seg_a: Vec3, seg_b: Vec3, sphere_center: Vec3, sphere_radius: float,
) -> bool:
    """3D segment 가 sphere 와 교차하는지 (segment-point closest < radius).

    sigma_bridge 분기 (2)/(3) 명시 분리용. compute_detour_waypoint 의 첫
    검사와 동일 로직이지만 *segment 안전 자체* 만 단독 질의 가능 →
    호출측에서 '안전 → (3) publish' vs '위반 + detour 불가 → (2-fallback)
    hover' 를 명확히 구분.

    Args:
        seg_a, seg_b: segment 양 끝점.
        sphere_center: sphere 중심.
        sphere_radius: sphere 반경 [m]. 0 이하면 항상 False (가드 비활성).

    Returns:
        segment 의 어느 점이 sphere 안에 있으면 True (교차), 아니면 False.
    """
    if sphere_radius <= 0.0:
        return False
    d, _ = _segment_closest_distance_to_point(seg_a, seg_b, sphere_center)
    return d < sphere_radius


def apply_vertical_floor(z: float, floor_m: float) -> float:
    """setpoint z 에 수직 floor 강제 (가구·바닥 충돌 회피 가드).

    ADR-0028 D5 amendment — sigma_bridge 의 *수직* 안전 가드. LLM 이 가구
    center z (예: dining_table 0.375 m, sofa 0.4 m) 를 setpoint 로 그대로
    보내는 경우 standoff 3D 거리 적용 후에도 *가구 표면 위 마진* 이
    PX4 추종 잔여 (≈ 0.45 m) 보다 작아 충돌·추락 위험. floor_m 강제로
    카메라 only 보조 드론 가정 (ADR-0026 D6) 정합.

    Args:
        z: 원 setpoint z [m].
        floor_m: 강제 하한 [m] (sigma_bridge_node 의 takeoff_altitude_m).
            0 이하면 사실상 비활성.

    Returns:
        max(z, floor_m).
    """
    if floor_m <= 0.0:
        return z
    return max(z, floor_m)


# ---------------------------------------------------------------------------
# inspect vantage 자동화 (ADR-0031)
# ---------------------------------------------------------------------------
# `inspect` 가 지시 클래스 후보를 카메라 프레임에 담는 vantage pose 로 비행하게
# 하는 pure 기하. 전방 +15° 하향 고정 카메라(짐벌 없음)이므로 드론이 후보 쪽으로
# 위치·yaw 를 잡지 않으면 객체가 FOV 에 안 들어와 OVD s1≈0 으로 고착된다
# (세션 47 sweep 진단). vantage 좌표는 알려진 지도(scene.py)로 *계산* 하되 검출·
# 그라운딩은 live OVD 가 수행한다 (ADR-0031 D1·D2).


def inspect_referent_keys(theta: dict, scene_objects: list) -> set:
    """inspect σ.theta → ovd_class 매칭용 referent key 집합.

    백본별 σ 형식 차이를 흡수해 vantage 후보 필터(``obj['ovd_class'] in keys``)에
    쓰일 키 집합을 만든다:
      - ``target_class``: fusion wrapper 가 주입한 OVD 클래스 (ADR-0029 블로커 1).
      - ``target_id``: 클래스명('chair') · 인스턴스 id('chair_left') · direct mode
        합성 라벨('mug_cup') 모두 가능.
      - target_id 토큰: ``'_'``·``'-'``·공백 분해('mug_cup' → 'mug','cup') —
        context_graph 없는 direct mode 가 OVD 어휘 밖 합성 라벨을 낼 때 ovd_class
        ('cup') 매칭을 복원 (grounding ``_expand_label_tokens`` 와 동일 정책,
        ADR-0029 블로커 1 의 direct-mode 연장). 단일 토큰('chair')은 자기 자신만.
      - 인스턴스 name → ovd_class: target_id 가 scene 인스턴스 name 이면 그 ovd_class.
      - 동의어 → 정본 ovd_class ('mug' → 'cup'): LLM 이 OVD 어휘의 동의어를 내면
        토큰 분해로도 복원 불가 → 후보 0 → +0.5m 상승 fallback (세션 62 llama S5
        10/10 실측). ``scenario_params.scene.OVD_CLASS_SYNONYMS`` 단일 소스
        (grounding ``_expand_label_tokens`` 와 공유).

    Args:
        theta: σ.theta dict (``target_class`` / ``target_id``).
        scene_objects: ``[{'name', 'position', 'ovd_class'}, ...]``.

    Returns:
        소문자 정규화된 referent key 집합 (빈 dict 시 빈 집합).
    """
    keys: set = set()
    tc = theta.get('target_class')
    if tc:
        keys.add(str(tc).strip().lower())
    tid_raw = theta.get('target_id')
    if tid_raw:
        tid = str(tid_raw).strip().lower()
        keys.add(tid)  # (a) 클래스명 직접
        # (a') direct mode 합성 라벨 토큰 흡수 ('mug_cup' → 'mug','cup').
        for tok in tid.replace('-', '_').replace(' ', '_').split('_'):
            if tok:
                keys.add(tok)
        # (b) 인스턴스 name → ovd_class.
        for obj in scene_objects:
            if str(obj.get('name', '')).strip().lower() == tid:
                oc = obj.get('ovd_class')
                if oc:
                    keys.add(str(oc).strip().lower())
                break
    # (c) OVD 어휘 동의어 정규화 ('mug' → 'cup') — 빈 집합이면 그대로.
    return expand_ovd_synonyms(keys) if keys else keys


def candidate_cluster_center(positions: list) -> Optional[Vec3]:
    """후보 객체 world 좌표 리스트의 산술 평균 중심. 빈 리스트면 None.

    지시 *클래스* 후보 전체(예 의자 2개)의 중심을 잡아 vantage 가 동일 클래스
    후보를 한 프레임에 담도록 한다 — 모호성 보존(C2). 단일 최근접만 잡으면
    모호성을 거짓 해소해 신뢰도 변조 메커니즘이 손상된다 (ADR-0031 D2).

    Args:
        positions: 후보 world 좌표 ``[(x, y, z), ...]`` 또는 ``[[x, y, z], ...]``.

    Returns:
        ``(cx, cy, cz)`` 산술 중심 또는 빈 입력 시 None.
    """
    if not positions:
        return None
    n = len(positions)
    sx = sum(float(p[0]) for p in positions)
    sy = sum(float(p[1]) for p in positions)
    sz = sum(float(p[2]) for p in positions)
    return (sx / n, sy / n, sz / n)


def compute_vantage_pose(
    center: Vec3,
    drone: Vec3,
    standoff_m: float,
    altitude_m: float,
) -> Tuple[Vec3, float]:
    r"""클러스터 중심 ``center`` 를 standoff·altitude 에서 바라보는 vantage pose.

    드론 현재 위치 쪽에서 수평으로 ``standoff_m`` 만큼 중심에서 물러난 점을
    vantage 로 잡고(이동 거리 최소·기존 접근 방향 유지), 고도는 ``altitude_m``
    고정, yaw 는 vantage→center 수평 방향(전방 고정 카메라가 중심을 담도록).

    하향각 $\theta = \arctan((altitude - h_\text{obj}) / standoff)$ 가 FOV
    여유 구간(대략 20°–45°)에 들도록 standoff·altitude 를 호출측이 고른다
    (ADR-0031 기하 근거: 의자 $h_\text{obj}{\approx}0.43$, altitude 1.5 m 에서
    standoff 1.5–2.0 m).

    Args:
        center: 후보 클러스터 중심 world ENU (x, y, z) [m].
        drone: 드론 현재 world ENU (x, y, z) [m].
        standoff_m: 중심으로부터 수평 standoff [m] (양수).
        altitude_m: vantage 고도 [m].

    Returns:
        ``((vx, vy, vz), yaw_enu)`` — vantage world ENU 좌표 + yaw [rad]
        (ENU East 기준 CCW 양수, 카메라 전방이 향할 heading). 드론이 중심과
        수평으로 거의 일치(xy 거리 < 1e-3)하면 +X(East)로 standoff 를 잡아
        결정성을 보장한다.
    """
    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    dxy = math.hypot(drone[0] - cx, drone[1] - cy)
    if dxy < 1e-3:
        # 드론이 중심 바로 위 — 접근 방향 미정의. +X(East)로 standoff.
        ux, uy = 1.0, 0.0
    else:
        ux, uy = (drone[0] - cx) / dxy, (drone[1] - cy) / dxy
    vx = cx + standoff_m * ux
    vy = cy + standoff_m * uy
    vz = altitude_m
    # yaw: vantage → center 수평 방향 (전방 카메라가 중심을 향함).
    yaw = math.atan2(cy - vy, cx - vx)
    return (vx, vy, vz), yaw


def wrap_angle(a: float) -> float:
    """각도 [rad]를 ``[-π, π]`` 로 정규화 (yaw 오차 계산용).

    ``atan2(sin, cos)`` 항등식으로 분기 없이 정규화. yaw 도달 판정에서 현재 yaw 와
    목표 yaw 의 차이를 최단 회전 오차로 환산할 때 사용.
    """
    return math.atan2(math.sin(a), math.cos(a))


def yaw_to_quaternion_zw(yaw: float) -> Tuple[float, float]:
    """yaw [rad] (ENU East 기준 CCW) → 평면 회전 quaternion 의 (z, w) 성분.

    PoseStamped.orientation 에 yaw 만 인코딩 (x=y=0). 소비측(waypoint_follower·
    g1_offboard)은 ``yaw = 2·atan2(z, w)`` 로 복원 (g1 frame_conversions 규약).
    all-zero quaternion(yaw 의도 없음)과 구분하기 위해 yaw=0 도 w=1.0 을 채운다.
    """
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def quaternion_zw_to_yaw(z: float, w: float) -> Optional[float]:
    """평면 quaternion (z, w) → yaw [rad] (ENU East 기준 CCW). all-zero 면 None.

    ``yaw_to_quaternion_zw`` 의 역. x/y 성분은 무시(평면 회전 가정).
    all-zero(x=y=z=w=0)는 "yaw 의도 없음"(현 yaw 유지)이라 None 반환 —
    g1_offboard 의 NaN-yaw 규약과 정합.
    """
    if z == 0.0 and w == 0.0:
        return None
    return 2.0 * math.atan2(z, w)
