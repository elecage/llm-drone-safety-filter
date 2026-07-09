"""Unit test for cbf_qp.py — KKT 해 정확성·boundary projection·특이점 처리.

테스트 그룹:
  - **B1 (static)**: ``cbf_qp_velocity_static`` 10개 시나리오.
  - **B2 (modulated)**: ``cbf_qp_velocity_modulated`` 5개 시나리오 + B1 등가성.
  - **Pose projection**: static/modulated 6개 시나리오.
"""

from __future__ import annotations

import numpy as np
import pytest

from tier1_filter.cbf_qp import (
    cbf_qp_velocity_modulated,
    cbf_qp_velocity_static,
    project_pose_to_safe_modulated,
    project_pose_to_safe_static,
)


R_MIN = 0.9
R_MAX = 1.5
GAMMA = 4.0
U_MAX = 0.5
TOL = 1e-9


# ==================================================================
# B1 — cbf_qp_velocity_static (cmsm-proof §5)
# ==================================================================

def test_static_constraint_inactive_when_moving_away():
    """drone이 user에서 멀어지는 nominal은 그대로 통과 (constraint inactive)."""
    p_drone = np.array([2.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([1.0, 0.0, 0.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA)
    assert info['constraint_active'] is False
    np.testing.assert_allclose(u_safe, u_nom, atol=TOL)


def test_static_constraint_active_when_approaching():
    """drone이 user 근처에서 user를 향해 가는 nominal은 CBF에 의해 정정됨.

    drone (1.0, 0, 1.5), user (0, 0, 1.5) → dist=1.0, h=0.1, -γh=-0.4.
    u_nom (-1, 0, 0) → $\\hat n^T u_\\text{nom} = -1 < -0.4$ → active.
    """
    p_drone = np.array([1.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([-1.0, 0.0, 0.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA)
    assert info['constraint_active'] is True
    n_hat = (p_drone - p_user) / np.linalg.norm(p_drone - p_user)
    h = float(np.linalg.norm(p_drone - p_user) - R_MIN)
    np.testing.assert_allclose(n_hat @ u_safe, -GAMMA * h, atol=TOL)


def test_static_kkt_minimization():
    """active 시 u_safe = u_nom + lambda * n_hat, lambda > 0."""
    p_drone = np.array([1.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([-1.0, 0.0, 0.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA)
    assert info['constraint_active'] is True
    assert info['lambda'] > 0
    n_hat = (p_drone - p_user) / np.linalg.norm(p_drone - p_user)
    expected = u_nom + info['lambda'] * n_hat
    np.testing.assert_allclose(u_safe, expected, atol=TOL)


def test_static_boundary_case():
    """drone이 정확히 boundary ($h=0$)에 있고 nominal이 stationary면 u_safe = 0 (inactive)."""
    p_drone = np.array([R_MIN, 0.0, 0.0])
    p_user = np.array([0.0, 0.0, 0.0])
    u_nom = np.array([0.0, 0.0, 0.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA)
    assert info['constraint_active'] is False
    np.testing.assert_allclose(u_safe, np.zeros(3), atol=TOL)


def test_static_saturation():
    """u_safe norm > u_max 시 비례 축소.

    drone (1.0, 0, 1.5), user (0, 0, 1.5), n=(1,0,0). u_nom = (-1, 5, 0)
    (tangential 5 + normal -1). active → lambda = 0.6, u_safe = (-0.4, 5, 0),
    norm ≈ 5.02 > u_max=0.5 → saturation.
    """
    p_drone = np.array([1.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([-1.0, 5.0, 0.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA, u_max=U_MAX)
    assert info['constraint_active'] is True
    assert info['saturated'] is True
    np.testing.assert_allclose(np.linalg.norm(u_safe), U_MAX, atol=TOL)


def test_static_singularity():
    """drone == user 특이점 — 임의 후퇴 방향으로 u_max 출력."""
    p_drone = np.zeros(3)
    p_user = np.zeros(3)
    u_nom = np.array([1.0, 0.0, 0.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA, u_max=U_MAX)
    assert info.get('singularity') is True
    assert np.linalg.norm(u_safe) == pytest.approx(U_MAX, abs=TOL)


def test_static_3d_geometry():
    """z 축 포함 3D — drone이 user 위에서 user 방향(-z)으로 가는 nominal CBF active."""
    p_drone = np.array([0.0, 0.0, 2.0])
    p_user = np.array([0.0, 0.0, 1.0])
    u_nom = np.array([0.0, 0.0, -1.0])
    u_safe, info = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA)
    assert info['constraint_active'] is True
    n_hat = (p_drone - p_user) / np.linalg.norm(p_drone - p_user)
    h = float(np.linalg.norm(p_drone - p_user) - R_MIN)
    np.testing.assert_allclose(n_hat @ u_safe, -GAMMA * h, atol=TOL)


# ==================================================================
# B2 — cbf_qp_velocity_modulated (cmsm-proof §6)
# ==================================================================

def test_modulated_equals_static_when_r_dot_zero():
    """$\\dot r = 0$ + $r = r_\\text{min}$이면 static과 정확히 동일."""
    p_drone = np.array([1.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([-1.0, 0.5, 0.2])
    u_b1, info_b1 = cbf_qp_velocity_static(u_nom, p_drone, p_user, R_MIN, GAMMA, u_max=U_MAX)
    u_b2, info_b2 = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=R_MIN, r_dot=0.0, gamma=GAMMA, u_max=U_MAX,
    )
    np.testing.assert_allclose(u_b2, u_b1, atol=TOL)
    assert info_b2['constraint_active'] == info_b1['constraint_active']
    np.testing.assert_allclose(info_b2['lambda'], info_b1['lambda'], atol=TOL)


def test_modulated_larger_r_increases_constraint():
    """동일 위치·nominal에서 $r$이 클수록 ($r_\\text{max}$ 측) $h$ 작아져 제약 강해짐.

    drone (1.0, 0, 1.5), user (0, 0, 1.5), dist=1.0.
    r=R_MIN=0.9 → h=0.1, -γh=-0.4.
    r=R_MAX=1.5 → h=-0.5 (영역 침입!), -γh=2.0.
    u_nom (-1, 0, 0) → n^T u_nom = -1.
    R_MIN: -1 < -0.4 → active, lambda = 0.6.
    R_MAX: -1 < 2.0 → active, lambda = 3.0 (보정 더 큼).
    """
    p_drone = np.array([1.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([-1.0, 0.0, 0.0])
    _, info_rmin = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=R_MIN, r_dot=0.0, gamma=GAMMA,
    )
    _, info_rmax = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=R_MAX, r_dot=0.0, gamma=GAMMA,
    )
    assert info_rmin['constraint_active'] is True
    assert info_rmax['constraint_active'] is True
    assert info_rmax['lambda'] > info_rmin['lambda']


def test_modulated_positive_r_dot_strengthens_constraint():
    """$\\dot r > 0$ (영역 팽창, $\\dot{\\tilde c} < 0$) → rhs 증가 → lambda 증가.

    cmsm-proof §6 부호 도출:
      $\\dot h = \\hat n^T u + (r_\\text{max}-r_\\text{min})\\dot c = \\hat n^T u - \\dot r$
      CBF 조건 $\\dot h + \\gamma h \\ge 0$ → $\\hat n^T u \\ge -\\gamma h + \\dot r$.
      따라서 rhs = $-\\gamma h + \\dot r$.

    drone (1.0, 0, 1.5), user (0, 0, 1.5), r=0.9, h=0.1, u_nom=(-1,0,0), $\\hat n^T u_\\text{nom}=-1$.
      r_dot=0: rhs=-0.4, active, lambda=0.6.
      r_dot=0.3: rhs=-0.4+0.3=-0.1, active, lambda=0.9.
    본 테스트는 cmsm-proof §6 부호 가드 — 실패하면 cbf_qp.py 부호 수정 필요.
    """
    p_drone = np.array([1.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    u_nom = np.array([-1.0, 0.0, 0.0])
    _, info_no_dot = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=R_MIN, r_dot=0.0, gamma=GAMMA,
    )
    _, info_with_dot = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=R_MIN, r_dot=0.3, gamma=GAMMA,
    )
    assert info_with_dot['lambda'] > info_no_dot['lambda']
    np.testing.assert_allclose(info_no_dot['lambda'], 0.6, atol=TOL)
    np.testing.assert_allclose(info_with_dot['lambda'], 0.9, atol=TOL)


def test_modulated_3d_with_active_modulation():
    """3D + 시변 r + 시변 r_dot 종합 테스트.

    drone (0, 0, 2), user (0, 0, 1), dist=1.0, r=1.2, h=-0.2 (영역 침입).
    u_nom (0, 0, -0.3) (-z 더 침입). n_hat (0,0,1), n^T u_nom = -0.3.
    r_dot = 0.1.
    rhs = -γh + r_dot = -4*(-0.2) + 0.1 = 0.8 + 0.1 = 0.9.
    n^T u_nom=-0.3 < 0.9 → active, lambda = 1.2.
    u_safe = (0, 0, -0.3 + 1.2) = (0, 0, 0.9). 검증: n^T u_safe = 0.9 ✓.
    """
    p_drone = np.array([0.0, 0.0, 2.0])
    p_user = np.array([0.0, 0.0, 1.0])
    u_nom = np.array([0.0, 0.0, -0.3])
    r = 1.2
    r_dot = 0.1
    u_safe, info = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=r, r_dot=r_dot, gamma=GAMMA,
    )
    assert info['constraint_active'] is True
    n_hat = np.array([0.0, 0.0, 1.0])
    h = 1.0 - r  # -0.2
    expected_rhs = -GAMMA * h + r_dot  # 0.9
    np.testing.assert_allclose(n_hat @ u_safe, expected_rhs, atol=TOL)


def test_modulated_singularity():
    """drone == user 특이점 — modulated에서도 임의 후퇴."""
    p_drone = np.zeros(3)
    p_user = np.zeros(3)
    u_nom = np.array([1.0, 0.0, 0.0])
    u_safe, info = cbf_qp_velocity_modulated(
        u_nom, p_drone, p_user, r=R_MIN, r_dot=0.0, gamma=GAMMA, u_max=U_MAX,
    )
    assert info.get('singularity') is True
    assert np.linalg.norm(u_safe) == pytest.approx(U_MAX, abs=TOL)


# ==================================================================
# Pose projection — static / modulated
# ==================================================================

def test_pose_static_outside():
    """target이 안전 영역 밖이면 그대로 통과."""
    p_target = np.array([2.0, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    p_safe, info = project_pose_to_safe_static(p_target, p_user, R_MIN)
    assert info['projected'] is False
    np.testing.assert_allclose(p_safe, p_target, atol=TOL)


def test_pose_static_inside_projection():
    """target이 안전 영역 안이면 boundary로 projection."""
    p_target = np.array([0.3, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    p_safe, info = project_pose_to_safe_static(p_target, p_user, R_MIN)
    assert info['projected'] is True
    np.testing.assert_allclose(np.linalg.norm(p_safe - p_user), R_MIN, atol=TOL)
    target_dir = (p_target - p_user) / np.linalg.norm(p_target - p_user)
    safe_dir = (p_safe - p_user) / np.linalg.norm(p_safe - p_user)
    np.testing.assert_allclose(target_dir, safe_dir, atol=TOL)


def test_pose_static_singularity():
    """target == user 특이점 — +x r_min 표면 출력."""
    p_target = np.zeros(3)
    p_user = np.zeros(3)
    p_safe, info = project_pose_to_safe_static(p_target, p_user, R_MIN)
    assert info.get('singularity') is True
    np.testing.assert_allclose(np.linalg.norm(p_safe - p_user), R_MIN, atol=TOL)


def test_pose_modulated_matches_static_at_r_min():
    """modulated($r = r_\\text{min}$)는 static과 정확히 동일 결과."""
    p_target = np.array([0.3, 0.0, 1.5])
    p_user = np.array([0.0, 0.0, 1.5])
    p_safe_s, _ = project_pose_to_safe_static(p_target, p_user, R_MIN)
    p_safe_m, _ = project_pose_to_safe_modulated(p_target, p_user, R_MIN)
    np.testing.assert_allclose(p_safe_m, p_safe_s, atol=TOL)


def test_pose_modulated_larger_r():
    """modulated에서 $r > r_\\text{min}$이면 더 큰 반경 surface로 projection."""
    p_target = np.array([1.0, 0.0, 1.5])  # user에서 1.0m
    p_user = np.array([0.0, 0.0, 1.5])
    # r=R_MAX=1.5 → target이 안전 영역 안 (1.0 < 1.5).
    p_safe, info = project_pose_to_safe_modulated(p_target, p_user, R_MAX)
    assert info['projected'] is True
    np.testing.assert_allclose(np.linalg.norm(p_safe - p_user), R_MAX, atol=TOL)


def test_pose_modulated_outside_at_r():
    """modulated에서 target이 *현재 r* 밖이면 그대로 통과 (r=R_MIN로 projection 안 함)."""
    p_target = np.array([1.0, 0.0, 1.5])  # user에서 1.0m
    p_user = np.array([0.0, 0.0, 1.5])
    p_safe, info = project_pose_to_safe_modulated(p_target, p_user, R_MIN)  # r=0.9 < 1.0
    assert info['projected'] is False
    np.testing.assert_allclose(p_safe, p_target, atol=TOL)
