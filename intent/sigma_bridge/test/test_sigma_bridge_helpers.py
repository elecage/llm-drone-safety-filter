"""sigma_bridge_helpers 단위 테스트 — 우회 waypoint inject 수학 검증."""

from __future__ import annotations

import math

import pytest

# 패키지 path 는 conftest.py 가 추가 (intent/sigma_bridge/ → intent_sigma_bridge import).
from intent_sigma_bridge.sigma_bridge_helpers import (
    _segment_closest_distance_to_point,
    apply_vertical_floor,
    candidate_cluster_center,
    compute_detour_waypoint,
    compute_radial_escape,
    compute_vantage_pose,
    direction_offset,
    distance_3d,
    has_arrived,
    inspect_referent_keys,
    is_segment_intersecting_sphere,
    lookup_object_position,
    quaternion_zw_to_yaw,
    wrap_angle,
    yaw_to_quaternion_zw,
)


# ==================================================================== segment closest


class TestSegmentClosest:
    def test_point_on_segment_zero_distance(self) -> None:
        d, c = _segment_closest_distance_to_point((0, 0, 0), (4, 0, 0), (2, 0, 0))
        assert d == pytest.approx(0.0)
        assert c == pytest.approx((2.0, 0.0, 0.0))

    def test_point_perpendicular_to_segment(self) -> None:
        d, c = _segment_closest_distance_to_point((0, 0, 0), (4, 0, 0), (2, 3, 0))
        assert d == pytest.approx(3.0)
        assert c == pytest.approx((2.0, 0.0, 0.0))

    def test_point_past_segment_end_clamped(self) -> None:
        # point 가 segment 너머 → seg_b 가 closest.
        d, c = _segment_closest_distance_to_point((0, 0, 0), (4, 0, 0), (10, 0, 0))
        assert d == pytest.approx(6.0)
        assert c == pytest.approx((4.0, 0.0, 0.0))

    def test_point_before_segment_start_clamped(self) -> None:
        d, c = _segment_closest_distance_to_point((0, 0, 0), (4, 0, 0), (-5, 0, 0))
        assert d == pytest.approx(5.0)
        assert c == pytest.approx((0.0, 0.0, 0.0))

    def test_zero_length_segment(self) -> None:
        d, c = _segment_closest_distance_to_point((1, 1, 1), (1, 1, 1), (4, 5, 1))
        assert d == pytest.approx(5.0)
        assert c == (1, 1, 1)

    def test_3d_segment_perpendicular(self) -> None:
        # segment along x-axis at z=1, point at (2, 0, 5) → closest = (2, 0, 1).
        d, c = _segment_closest_distance_to_point((0, 0, 1), (4, 0, 1), (2, 0, 5))
        assert d == pytest.approx(4.0)
        assert c == pytest.approx((2.0, 0.0, 1.0))


# ==================================================================== compute_detour_waypoint


class TestComputeDetourWaypointSafe:
    """직선이 회피 영역 밖이면 None."""

    def test_segment_far_from_user(self) -> None:
        # drone (0, 0) → goal (4, 0), user (2, 3) — 거리 3.0 > r_guard 1.5.
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(4, 0, 1.5), user=(2, 3, 1.1), r_guard=1.5,
        )
        assert result is None

    def test_user_behind_drone(self) -> None:
        # drone (0, 0) → goal (4, 0), user (-3, 0) — segment 밖 (t=-0.75 clamp).
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(4, 0, 1.5), user=(-3, 0, 1.1), r_guard=1.5,
        )
        assert result is None  # drone-user 거리 3.0 + dz 0.4 = 3.03 > 1.5

    def test_user_past_goal(self) -> None:
        # drone (0, 0) → goal (4, 0), user (10, 0) — segment 밖 (t>1 clamp).
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(4, 0, 1.5), user=(10, 0, 1.1), r_guard=1.5,
        )
        assert result is None

    def test_r_guard_zero_returns_none(self) -> None:
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(4, 0, 1.5), user=(2, 0, 1.1), r_guard=0.0,
        )
        assert result is None

    def test_drone_equals_goal_returns_none(self) -> None:
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(0, 0, 1.5), user=(0.1, 0.1, 1.1), r_guard=1.5,
        )
        assert result is None


# ==================================================================== compute_detour_waypoint — 우회 inject


class TestComputeDetourWaypointInject:
    """직선이 회피 영역 가르면 수평 우회 waypoint inject — 정확 좌표는 iterative
    탐색 결과라 deterministic 이지만 복잡 → *조건* (두 leg 안전 + z 유지 + 부호)
    위주로 검증.
    """

    @staticmethod
    def _both_legs_safe(drone, waypoint, goal, user, r_guard, eps=1e-6) -> bool:
        d1, _ = _segment_closest_distance_to_point(drone, waypoint, user)
        d2, _ = _segment_closest_distance_to_point(waypoint, goal, user)
        return d1 >= r_guard - eps and d2 >= r_guard - eps

    def test_user_on_segment_perpendicular_detour(self) -> None:
        # drone (0, 0) → goal (4, 0), user (2, 0, 1.1), r_guard 1.5.
        # user 가 segment 위 (offset_n=0) → sign_avoid=+1 → +y 쪽 우회.
        drone = (0, 0, 1.5)
        goal = (4, 0, 1.5)
        user = (2, 0, 1.1)
        result = compute_detour_waypoint(drone, goal, user, r_guard=1.5)
        assert result is not None
        wx, wy, wz = result
        assert wx == pytest.approx(2.0)  # user x 위
        assert wy > 0.0  # +y 쪽 우회
        assert wz == pytest.approx(1.5)  # drone z 유지
        assert self._both_legs_safe(drone, result, goal, user, 1.5)

    def test_asymmetric_user_avoid_opposite_side(self) -> None:
        # user 가 +y 쪽 (segment 위에서 약간) → 우회 waypoint 는 -y 쪽.
        drone = (0, 0, 1.5)
        goal = (4, 0, 1.5)
        user = (2, 0.5, 1.1)
        result = compute_detour_waypoint(drone, goal, user, r_guard=1.5)
        assert result is not None
        _, wy, _ = result
        assert wy < 0.0  # user 반대편 (-y)
        assert self._both_legs_safe(drone, result, goal, user, 1.5)

    def test_detour_waypoint_outside_user_guard(self) -> None:
        """우회 waypoint 자체가 r_guard 외곽 (xy 평면) 에 있어야."""
        drone = (0.5, -0.5, 1.5)
        goal = (-1.8, -1.5, 1.015)  # TV (v4.1 layout)
        user = (-0.5, 2.0, 0.95)  # v4.1 user local
        r_guard = 1.5
        result = compute_detour_waypoint(drone, goal, user, r_guard)
        if result is None:
            pytest.skip('본 geometry 는 우회 불요 — 별 case 가 검증함')
        wx, wy, _ = result
        d_xy = math.sqrt((wx - user[0]) ** 2 + (wy - user[1]) ** 2)
        assert d_xy >= r_guard - 1e-9

    def test_detour_waypoint_z_preserves_drone_altitude(self) -> None:
        # drone z=1.5, goal z=2.0 → 우회 waypoint z = drone z.
        drone = (0, 0, 1.5)
        result = compute_detour_waypoint(
            drone=drone, goal=(4, 0, 2.0), user=(2, 0, 1.1), r_guard=1.5,
        )
        assert result is not None
        _, _, wz = result
        assert wz == pytest.approx(1.5)

    def test_vertical_segment_no_detour(self) -> None:
        # drone (0, 0, 0) → goal (0, 0, 3) — xy 동일 (순수 수직) → 수평 우회 불가.
        result = compute_detour_waypoint(
            drone=(0, 0, 0), goal=(0, 0, 3), user=(0, 0, 1.5), r_guard=1.5,
        )
        assert result is None

    def test_drone_too_close_to_user_returns_none(self) -> None:
        """drone-user xy 거리 < 1.2·r_guard 면 단일 waypoint 우회 기하학적
        불가능 → None (호출측 fallback: 기존 projection 또는 hover)."""
        # drone (0, 0) → goal (4, 0), user (0.5, 0.5, 1.1) — drone 과 0.71m
        # < 1.2·1.5=1.8. r_guard=1.5.
        # 단 segment-user closest = sqrt(0.25+0.25+0.16) ≈ 0.81 < r_guard → 우회 필요한 case.
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(4, 0, 1.5),
            user=(0.5, 0.5, 1.1), r_guard=1.5,
        )
        assert result is None

    def test_goal_too_close_to_user_returns_none(self) -> None:
        """goal-user xy 거리 < 1.2·r_guard 면 None."""
        result = compute_detour_waypoint(
            drone=(0, 0, 1.5), goal=(4, 0, 1.5),
            user=(3.5, 0.5, 1.1), r_guard=1.5,
        )
        assert result is None

    def test_detour_realistic_v4_1_geometry(self) -> None:
        """v4.1 layout: drone dock(0,0,1.5) → TV setpoint(-2.3,-1.0,0.865) 가
        user(-0.5, 2.0, 0.95) 회피 영역과 어떻게 되는지.

        TV 가 user 와 충분히 먼 *방향* 이라 직선 안전 → None 기대.
        """
        drone = (0.0, 0.0, 1.5)
        goal = (-2.3, -1.0, 0.865)
        user = (-0.5, 2.0, 0.95)
        r_guard = 1.5
        result = compute_detour_waypoint(drone, goal, user, r_guard)
        # closest 계산:
        # segment (0,0,1.5) → (-2.3,-1.0,0.865), direction (-2.3,-1.0,-0.635)
        # user (-0.5, 2.0, 0.95)
        # closest 거리 >> 1.5 (user 가 +y, segment 는 -y) → None.
        assert result is None

    def test_detour_blocking_geometry(self) -> None:
        """drone (0.5,-0.5,1.5) → goal (-0.5, 4.5, 1.5) (user 너머 북쪽).
        user (-0.5, 2.0, 0.95) — segment 가 user 옆을 가로지름.
        """
        drone = (0.5, -0.5, 1.5)
        goal = (-0.5, 4.5, 1.5)
        user = (-0.5, 2.0, 0.95)
        r_guard = 1.5
        # 먼저 직선 위반 확인:
        d_seg, _ = _segment_closest_distance_to_point(drone, goal, user)
        assert d_seg < r_guard  # 위반 — 우회 필요
        # drone-user xy 거리 = sqrt(1+6.25) = 2.69 > 1.8 ✓
        # goal-user xy 거리 = 2.5 > 1.8 ✓
        result = compute_detour_waypoint(drone, goal, user, r_guard)
        assert result is not None
        assert TestComputeDetourWaypointInject._both_legs_safe(
            drone, result, goal, user, r_guard
        )


# ==================================================================== distance_3d / has_arrived


class TestDistanceArrived:
    def test_distance_3d(self) -> None:
        assert distance_3d((0, 0, 0), (3, 4, 0)) == pytest.approx(5.0)
        assert distance_3d((1, 1, 1), (1, 1, 1)) == pytest.approx(0.0)

    def test_has_arrived_within_threshold(self) -> None:
        assert has_arrived((0, 0, 0), (0.3, 0.0, 0.0), 0.5)

    def test_has_arrived_at_threshold_boundary_excluded(self) -> None:
        # 거리 = threshold 면 도달 *아님* (strict <).
        assert not has_arrived((0, 0, 0), (0.5, 0.0, 0.0), 0.5)

    def test_has_not_arrived_outside_threshold(self) -> None:
        assert not has_arrived((0, 0, 0), (1.0, 0.0, 0.0), 0.5)


# ==================================================================== apply_vertical_floor


class TestApplyVerticalFloor:
    def test_z_below_floor_raised_to_floor(self) -> None:
        # dining_table center z=0.225 (local) → floor 1.5 적용.
        assert apply_vertical_floor(0.225, 1.5) == pytest.approx(1.5)

    def test_z_above_floor_unchanged(self) -> None:
        # tv center z=0.865 < floor 1.5 → 1.5 강제.
        assert apply_vertical_floor(0.865, 1.5) == pytest.approx(1.5)
        # inspect +0.5 한 case (drone hover 1.5 + 0.5 = 2.0) > floor.
        assert apply_vertical_floor(2.0, 1.5) == pytest.approx(2.0)

    def test_z_at_floor_unchanged(self) -> None:
        assert apply_vertical_floor(1.5, 1.5) == pytest.approx(1.5)

    def test_floor_zero_deactivates(self) -> None:
        # floor=0 → 원 z 반환 (사실상 비활성).
        assert apply_vertical_floor(0.225, 0.0) == pytest.approx(0.225)

    def test_floor_negative_deactivates(self) -> None:
        assert apply_vertical_floor(0.5, -1.0) == pytest.approx(0.5)

    def test_negative_z_raised_to_floor(self) -> None:
        # 비현실 케이스 (LLM 환각) — floor 가 보호.
        assert apply_vertical_floor(-2.0, 1.5) == pytest.approx(1.5)


# ==================================================================== is_segment_intersecting_sphere


class TestIsSegmentIntersectingSphere:
    def test_safe_far(self) -> None:
        # segment 와 sphere 충분히 멀리.
        assert not is_segment_intersecting_sphere(
            (0, 0, 0), (4, 0, 0), sphere_center=(2, 3, 0), sphere_radius=1.0,
        )

    def test_intersects_perpendicular(self) -> None:
        # user 가 segment 정중앙 옆 0.4 m.
        assert is_segment_intersecting_sphere(
            (0, 0, 0), (4, 0, 0), sphere_center=(2, 0.4, 0), sphere_radius=1.0,
        )

    def test_boundary_exclusive(self) -> None:
        # 거리 = radius → False (strict <).
        assert not is_segment_intersecting_sphere(
            (0, 0, 0), (4, 0, 0), sphere_center=(2, 1.0, 0), sphere_radius=1.0,
        )

    def test_zero_radius_always_false(self) -> None:
        assert not is_segment_intersecting_sphere(
            (0, 0, 0), (4, 0, 0), sphere_center=(2, 0, 0), sphere_radius=0.0,
        )

    def test_observed_failure_case(self) -> None:
        """실 sigma_bridge.log 케이스 검증 — 분기 (3) 오라벨 fix 회귀 방지.

        drone (1.07, 2.29, 1.51) → target (-1.65, 2.06, 1.50) 가 user
        (-0.5, 2.0, 0.95) 회피 영역 r=1.0 을 가르는지. 수동 계산: closest
        거리 ≈ 0.573 m < 1.0 → True.
        """
        assert is_segment_intersecting_sphere(
            seg_a=(1.07, 2.29, 1.51),
            seg_b=(-1.65, 2.06, 1.50),
            sphere_center=(-0.5, 2.0, 0.95),
            sphere_radius=1.0,
        )


# ==================================================================== compute_radial_escape


class TestComputeRadialEscape:
    """drone 이 회피 경계에 주차됐을 때 사용자에게서 멀어지는 탈출 waypoint."""

    def test_escape_pushes_radially_outward(self) -> None:
        # drone 이 user 동쪽 경계 r=1.0 에. user (0,0,1.1), drone (1,0,1.5).
        # target_clearance=1.3 → escape 는 같은 방향(+x) 1.3 m.
        escape = compute_radial_escape(
            drone=(1.0, 0.0, 1.5), user=(0.0, 0.0, 1.1),
            r_guard=1.0, target_clearance=1.3,
        )
        assert escape is not None
        assert escape == pytest.approx((1.3, 0.0, 1.5))

    def test_escape_keeps_drone_altitude(self) -> None:
        escape = compute_radial_escape(
            drone=(0.0, 1.0, 1.42), user=(0.0, 0.0, 1.1),
            r_guard=1.0, target_clearance=1.3,
        )
        assert escape is not None
        assert escape[2] == pytest.approx(1.42)
        # +y 방향 유지, xy 거리 = 1.3.
        assert math.hypot(escape[0], escape[1]) == pytest.approx(1.3)

    def test_escape_leg_never_enters_sphere(self) -> None:
        # 핵심 불변식: drone→escape leg 가 회피 영역을 침범하지 않음.
        user = (0.0, 0.0, 1.1)
        drone = (1.0, 0.0, 1.5)  # d_drone_user = sqrt(1+0.16)=1.077 ≥ r=1.0
        escape = compute_radial_escape(
            drone=drone, user=user, r_guard=1.0, target_clearance=1.3,
        )
        assert escape is not None
        assert not is_segment_intersecting_sphere(
            seg_a=drone, seg_b=escape,
            sphere_center=user, sphere_radius=1.0,
        )

    def test_none_when_already_far(self) -> None:
        # drone 이 이미 target_clearance 밖 → 탈출 불요.
        assert compute_radial_escape(
            drone=(2.0, 0.0, 1.5), user=(0.0, 0.0, 1.1),
            r_guard=1.0, target_clearance=1.3,
        ) is None

    def test_none_when_drone_over_user_xy(self) -> None:
        # drone 이 user 바로 위 (xy 일치) → 방향 미정의 → None.
        assert compute_radial_escape(
            drone=(0.0, 0.0, 1.5), user=(0.0, 0.0, 1.1),
            r_guard=1.0, target_clearance=1.3,
        ) is None

    def test_none_when_guard_disabled(self) -> None:
        assert compute_radial_escape(
            drone=(1.0, 0.0, 1.5), user=(0.0, 0.0, 1.1),
            r_guard=0.0, target_clearance=1.3,
        ) is None

    def test_escape_then_detour_solves_trapped_drone(self) -> None:
        """엔드투엔드: 경계 주차 드론이 사용자 반대편 목표로 가는 경로 성립.

        실 버그 시나리오 — "사용자에게 와" 직후 drone 이 user 동쪽 경계
        (1.0, 0.0)에 주차. 반대편(서쪽) 식탁 (-3, 0)으로 명령. 직접
        compute_detour 는 d_drone_user_xy=1.0 < 1.2 라 None(갇힘). escape 후
        재계산하면 escape(1.3,0)→식탁 우회가 성립해야 함.
        """
        user = (0.0, 0.0, 1.1)
        drone = (1.0, 0.0, 1.5)
        goal = (-3.0, 0.0, 1.5)
        r_guard = 1.0

        # 직접 우회는 실패(갇힘) — 회귀 기준.
        assert compute_detour_waypoint(drone, goal, user, r_guard) is None

        # escape 후 우회는 성립.
        escape = compute_radial_escape(
            drone=drone, user=user, r_guard=r_guard,
            target_clearance=1.3 * r_guard,
        )
        assert escape is not None
        w = compute_detour_waypoint(escape, goal, user, r_guard)
        assert w is not None
        # 세 leg 모두 회피 영역 밖.
        for a, b in ((drone, escape), (escape, w), (w, goal)):
            assert not is_segment_intersecting_sphere(
                seg_a=a, seg_b=b, sphere_center=user, sphere_radius=r_guard,
            )


# ==================================================================== move_to 결정론 해석 (ADR-0027 amendment)


class TestDirectionOffset:
    """방향 토큰 → 상대 오프셋 (LLM 좌표 직접 출력 폐기)."""

    def test_forward_is_plus_y(self) -> None:
        assert direction_offset('forward') == (0.0, 2.0, 0.0)

    def test_back_is_minus_y(self) -> None:
        assert direction_offset('back') == (0.0, -2.0, 0.0)

    def test_left_right_are_x(self) -> None:
        assert direction_offset('left') == (-2.0, 0.0, 0.0)
        assert direction_offset('right') == (2.0, 0.0, 0.0)

    def test_up_down_are_z(self) -> None:
        assert direction_offset('up') == (0.0, 0.0, 1.0)
        assert direction_offset('down') == (0.0, 0.0, -1.0)

    def test_case_and_whitespace_insensitive(self) -> None:
        assert direction_offset('  FORWARD ') == (0.0, 2.0, 0.0)

    def test_invalid_token_none(self) -> None:
        assert direction_offset('northeast') is None
        assert direction_offset('') is None
        assert direction_offset(None) is None


class TestLookupObjectPosition:
    """객체 이름 → world 좌표 결정론 lookup (gemma 좌표 환각 대체)."""

    _SCENE = [
        {'name': 'sofa', 'position': [-1.8, 1.5, 0.4]},
        {'name': 'dining_table', 'position': [2.0, -1.0, 0.375]},
        {'name': 'tv', 'position': [-1.8, -1.5, 1.015]},
    ]

    def test_exact_name(self) -> None:
        assert lookup_object_position('sofa', self._SCENE) == (-1.8, 1.5, 0.4)

    def test_case_and_whitespace_insensitive(self) -> None:
        assert lookup_object_position(' Sofa ', self._SCENE) == (-1.8, 1.5, 0.4)

    def test_distinct_objects_not_confused(self) -> None:
        # 회귀: "소파"→sofa 가 dining_table 좌표로 새지 않음.
        assert lookup_object_position('sofa', self._SCENE) != \
            lookup_object_position('dining_table', self._SCENE)
        assert lookup_object_position('dining_table', self._SCENE) == (2.0, -1.0, 0.375)

    def test_unknown_name_none(self) -> None:
        assert lookup_object_position('fridge', self._SCENE) is None

    def test_empty_or_none_none(self) -> None:
        assert lookup_object_position('', self._SCENE) is None
        assert lookup_object_position(None, self._SCENE) is None

    def test_empty_scene_none(self) -> None:
        assert lookup_object_position('sofa', []) is None


# ==================================================================== vantage (ADR-0031)


class TestCandidateClusterCenter:
    def test_single_candidate(self) -> None:
        assert candidate_cluster_center([(2.0, -0.4, 0.425)]) == \
            pytest.approx((2.0, -0.4, 0.425))

    def test_two_chairs_midpoint(self) -> None:
        # 거실 의자 2개 → 수평 중점, z 평균 (모호성 보존 framing).
        c = candidate_cluster_center([(2.0, -0.4, 0.425), (2.0, -1.6, 0.425)])
        assert c == pytest.approx((2.0, -1.0, 0.425))

    def test_accepts_list_positions(self) -> None:
        c = candidate_cluster_center([[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
        assert c == pytest.approx((1.0, 1.0, 1.0))

    def test_empty_none(self) -> None:
        assert candidate_cluster_center([]) is None


class TestComputeVantagePose:
    def test_standoff_on_drone_side(self) -> None:
        # 드론이 중심의 +X 쪽 → vantage 는 중심에서 +X 로 standoff 물러난 점,
        # 고도는 altitude 고정.
        (vx, vy, vz), yaw = compute_vantage_pose(
            center=(0.0, 0.0, 0.43), drone=(5.0, 0.0, 1.5),
            standoff_m=1.5, altitude_m=1.5,
        )
        assert (vx, vy, vz) == pytest.approx((1.5, 0.0, 1.5))
        # yaw 는 vantage→center 방향 = −X(West) = ±π.
        assert abs(yaw) == pytest.approx(math.pi)

    def test_yaw_points_at_center(self) -> None:
        # 드론이 중심의 +Y 쪽 → vantage 는 중심 +Y 로 standoff, yaw 는 −Y(South)=−π/2.
        (vx, vy, vz), yaw = compute_vantage_pose(
            center=(0.0, 0.0, 0.43), drone=(0.0, 5.0, 1.5),
            standoff_m=2.0, altitude_m=1.5,
        )
        assert (vx, vy, vz) == pytest.approx((0.0, 2.0, 1.5))
        assert yaw == pytest.approx(-math.pi / 2.0)

    def test_drone_above_center_deterministic(self) -> None:
        # 드론이 중심 바로 위(xy 일치) → +X standoff 결정성.
        (vx, vy, vz), yaw = compute_vantage_pose(
            center=(1.0, 1.0, 0.43), drone=(1.0, 1.0, 1.5),
            standoff_m=1.5, altitude_m=1.5,
        )
        assert (vx, vy) == pytest.approx((2.5, 1.0))
        assert vz == pytest.approx(1.5)

    def test_downward_angle_in_fov(self) -> None:
        # ADR-0031 기하: standoff 1.5·altitude 1.5·h_obj 0.43 → 하향각 ≈ 35.5°
        # (FOV 여유 20°–45°). vantage 수평거리=standoff 확인.
        center = (0.0, 0.0, 0.43)
        (vx, vy, vz), _ = compute_vantage_pose(
            center, drone=(3.0, 0.0, 1.5), standoff_m=1.5, altitude_m=1.5,
        )
        horiz = math.hypot(vx - center[0], vy - center[1])
        theta = math.degrees(math.atan2(vz - center[2], horiz))
        assert horiz == pytest.approx(1.5)
        assert 20.0 < theta < 45.0


class TestYawQuaternion:
    def test_zero_yaw_w_one(self) -> None:
        z, w = yaw_to_quaternion_zw(0.0)
        assert (z, w) == pytest.approx((0.0, 1.0))

    def test_encode_decode_roundtrip(self) -> None:
        for deg in (-170, -90, -10, 0, 10, 90, 170):
            yaw = math.radians(deg)
            z, w = yaw_to_quaternion_zw(yaw)
            assert quaternion_zw_to_yaw(z, w) == pytest.approx(yaw, abs=1e-9)

    def test_decode_all_zero_none(self) -> None:
        assert quaternion_zw_to_yaw(0.0, 0.0) is None


# ============================================================ inspect referent keys
class TestInspectReferentKeys:
    """inspect σ.theta → ovd_class 매칭 키 집합 (direct mode 합성 라벨 토큰 흡수)."""

    SCENE = [
        {'name': 'mug_left', 'position': [1.7, -1.0, 0.8], 'ovd_class': 'cup'},
        {'name': 'mug_center', 'position': [2.0, -1.0, 0.8], 'ovd_class': 'cup'},
        {'name': 'chair_left', 'position': [2.0, -0.4, 0.4], 'ovd_class': 'chair'},
    ]

    def test_composite_label_token_recovers_ovd_class(self) -> None:
        # direct mode 합성 라벨 'mug_cup' → 토큰 'cup' 으로 ovd_class 매칭 복원
        # (S5 핵심 버그 — 완전일치였으면 'cup' 부재 → vantage fallback).
        keys = inspect_referent_keys({'target_id': 'mug_cup'}, self.SCENE)
        assert 'cup' in keys

    def test_single_token_class_name_unchanged(self) -> None:
        # 'chair' == ovd_class — 토큰 확장에도 불변 (S7 회귀 방지).
        keys = inspect_referent_keys({'target_id': 'chair'}, self.SCENE)
        assert 'chair' in keys

    def test_instance_name_lookup(self) -> None:
        # 인스턴스 name 'mug_left' → ovd_class 'cup'.
        keys = inspect_referent_keys({'target_id': 'mug_left'}, self.SCENE)
        assert 'cup' in keys

    def test_target_class_direct(self) -> None:
        # fusion wrapper 가 주입한 target_class 직접 사용.
        keys = inspect_referent_keys({'target_class': 'cup'}, self.SCENE)
        assert 'cup' in keys

    def test_case_normalized(self) -> None:
        keys = inspect_referent_keys({'target_id': 'Mug_Cup'}, self.SCENE)
        assert 'cup' in keys

    def test_synonym_normalized_to_ovd_class(self) -> None:
        # 세션 62 llama S5 회귀: target_id='mug' (합성 라벨도 인스턴스 name 도
        # 아님 — 토큰 분해·name lookup 모두 복원 불가) → 동의어 표('mug'→'cup',
        # scenario_params.scene.OVD_CLASS_SYNONYMS 단일 소스)로 vantage 후보
        # 필터 매칭 복원 (+0.5m 상승 fallback 차단).
        keys = inspect_referent_keys({'target_id': 'mug'}, self.SCENE)
        assert 'cup' in keys

    def test_synonym_via_target_class(self) -> None:
        # wrapper 가 target_class 로 동의어를 실어도 동일 정규화.
        keys = inspect_referent_keys({'target_class': 'mug'}, self.SCENE)
        assert 'cup' in keys

    def test_empty_theta_empty_set(self) -> None:
        assert inspect_referent_keys({}, self.SCENE) == set()


# ============================================================== wrap_angle (yaw)
class TestWrapAngle:
    def test_zero(self) -> None:
        assert wrap_angle(0.0) == pytest.approx(0.0)

    def test_within_range_unchanged(self) -> None:
        assert wrap_angle(1.0) == pytest.approx(1.0)

    def test_over_pi_wraps_negative(self) -> None:
        assert wrap_angle(math.pi + 0.5) == pytest.approx(-math.pi + 0.5)

    def test_under_neg_pi_wraps_positive(self) -> None:
        assert wrap_angle(-math.pi - 0.5) == pytest.approx(math.pi - 0.5)

    def test_yaw_error_shortest_rotation(self) -> None:
        # 170° 와 -170° 의 차이는 최단 20° (340° 아님) — yaw 정렬 판정 핵심.
        err = wrap_angle(math.radians(170) - math.radians(-170))
        assert abs(err) == pytest.approx(math.radians(20), abs=1e-6)
