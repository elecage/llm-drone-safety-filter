"""task_success_geom — SR post-hoc 기하 평가기 단위 테스트 (ADR-0032 D2).

host venv 순수 테스트 — rosbag2 불요. 기대 vantage 좌표·도달 판정·scenario 분기.
"""

from __future__ import annotations

import math

import pytest

from eval_runner.task_success_geom import (
    DEFAULT_DELTA_M,
    expected_vantage_local,
    reached_vantage,
    trial_task_success,
)


# scene.py (참값) + params.py (spawn·target_class) 로부터 손계산한 기대 vantage.
# S6: sofa 단일 world (-1.8,1.5,0.4), spawn (0.5,-0.5,0.15)
#   → 후보 local (-2.3,2.0,0.25) → center (-2.3,2.0,0.25). 원점 접근, standoff 1.5.
def test_expected_vantage_s6_matches_hand_computation():
    vx, vy, vz = expected_vantage_local('S6')
    assert vz == pytest.approx(1.5)  # altitude 고정
    # 중심으로부터 수평 거리 = standoff 1.5.
    cx, cy = -2.3, 2.0
    assert math.hypot(vx - cx, vy - cy) == pytest.approx(1.5, abs=1e-6)


def test_expected_vantage_all_scenarios_altitude_and_standoff():
    # S5 cup(3 후보)·S6 sofa(단일) — 거실 2종 전부 vantage 정의됨 (ADR-0039 D2).
    for s in ('S5', 'S6'):
        vx, vy, vz = expected_vantage_local(s)
        assert vz == pytest.approx(1.5)
        assert math.isfinite(vx) and math.isfinite(vy)


def test_expected_vantage_standoff_altitude_override():
    base = expected_vantage_local('S6')
    higher = expected_vantage_local('S6', altitude_m=2.0)
    assert higher[2] == pytest.approx(2.0)
    assert base[2] == pytest.approx(1.5)
    # standoff 키우면 중심에서 더 멀어짐 (수평).
    near = expected_vantage_local('S6', standoff_m=1.0)
    far = expected_vantage_local('S6', standoff_m=2.5)
    cx, cy = -2.3, 2.0
    assert math.hypot(near[0] - cx, near[1] - cy) == pytest.approx(1.0, abs=1e-6)
    assert math.hypot(far[0] - cx, far[1] - cy) == pytest.approx(2.5, abs=1e-6)


def test_expected_vantage_unknown_scenario_raises():
    with pytest.raises(RuntimeError):
        expected_vantage_local('S99')


def test_reached_vantage_hit_and_miss():
    v = expected_vantage_local('S6')
    # 정확히 vantage 통과 → 도달.
    traj_hit = [(0.0, (0.0, 0.0, 0.0)), (1.0, v)]
    assert reached_vantage(traj_hit, v, DEFAULT_DELTA_M) is True
    # 멀리 떨어진 궤적 → 미도달.
    traj_miss = [(0.0, (10.0, 10.0, 10.0)), (1.0, (9.0, 9.0, 9.0))]
    assert reached_vantage(traj_miss, v, DEFAULT_DELTA_M) is False


def test_reached_vantage_within_delta_boundary():
    v = (0.0, 0.0, 1.5)
    # delta 경계 내부(0.4 < 0.5) → 도달.
    assert reached_vantage([(0.0, (0.4, 0.0, 1.5))], v, 0.5) is True
    # delta 경계 밖(0.6 > 0.5) → 미도달.
    assert reached_vantage([(0.0, (0.6, 0.0, 1.5))], v, 0.5) is False
    # 정확히 경계(0.5 == 0.5) → 도달(<=).
    assert reached_vantage([(0.0, (0.5, 0.0, 1.5))], v, 0.5) is True


def test_reached_vantage_empty_trajectory_false():
    assert reached_vantage([], (0.0, 0.0, 1.5), 0.5) is False


def test_reached_vantage_invalid_delta_raises():
    with pytest.raises(ValueError):
        reached_vantage([(0.0, (0, 0, 0))], (0, 0, 0), 0.0)
    with pytest.raises(ValueError):
        reached_vantage([(0.0, (0, 0, 0))], (0, 0, 0), -1.0)


def test_trial_task_success_composition():
    v = expected_vantage_local('S6')
    assert trial_task_success('S6', [(0.0, v)]) is True
    assert trial_task_success('S6', [(0.0, (99.0, 99.0, 99.0))]) is False
