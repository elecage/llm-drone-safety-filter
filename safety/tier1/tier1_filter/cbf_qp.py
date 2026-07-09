"""CBF-QP solvers — B1 (정적) + B2 (신뢰도 변조), 1-constraint analytical QP.

cmsm-proof.md 매핑:

  단일적분기 $\\dot p = u$, 준정적 사용자 $\\dot p_\\text{user} = 0$.

  **B1 (§5 명제 1, 정적 $c$):**
    $h(p) = \\|p - p_\\text{user}\\| - r_\\text{min}$
    $\\dot h = \\hat n^T u$,  $\\hat n = (p - p_\\text{user}) / \\|p - p_\\text{user}\\|$
    CBF 조건: $\\dot h + \\gamma h \\geq 0$  →  $\\hat n^T u \\geq -\\gamma h$

  **B2 (§6 정리 2, 시변 $\\tilde c(t)$):**
    $h(x, t) = \\|p - p_\\text{user}\\| - r(\\tilde c(t))$
    단조 비증가 $r(c) = r_\\text{min} + (1-c)(r_\\text{max} - r_\\text{min})$ 미분:
      $\\dot r = -(r_\\text{max} - r_\\text{min}) \\dot{\\tilde c}$.
    cmsm-proof §6 (식 변형):
      $\\dot h = \\hat n^T u + (r_\\text{max} - r_\\text{min}) \\dot{\\tilde c}$
              $= \\hat n^T u - \\dot r$  (since $(r_\\text{max}-r_\\text{min})\\dot c = -\\dot r$).
    CBF 조건 $\\dot h + \\gamma h \\geq 0$:
      $\\hat n^T u - \\dot r + \\gamma h \\geq 0$
      $\\Rightarrow$ $\\hat n^T u \\geq -\\gamma h + \\dot r$.
    부호 직관: $\\dot{\\tilde c} < 0$ (신뢰도 급락) → $\\dot r > 0$ (영역 팽창) → rhs 증가
    → 제약 강해짐 (drone이 더 빨리 후퇴해야).

두 모드 모두 단일 inequality + 3D variable이라 KKT closed-form. 공통 솔버
``_cbf_qp_solve``가 $\\hat n$·rhs·$u_\\text{max}$만 받아 푸는 구조.

Pose nominal projection (corner anchor 등 안전 영역 내부 target 처리)도 같은
원칙으로 ``project_pose_to_safe_static`` / ``..._modulated`` 분리. 시그니처
모양은 동일하나 호출부에서 모드를 명시적으로 드러내기 위함.
"""

from __future__ import annotations

import numpy as np


# ----------------------------------------------------------------------
# 내부 helper — mode-agnostic KKT closed-form 솔버
# ----------------------------------------------------------------------
def _cbf_qp_solve(
    u_nom: np.ndarray,
    n_hat: np.ndarray,
    rhs: float,
    u_max: float | None,
) -> tuple[np.ndarray, dict]:
    """1-constraint QP $\\min \\|u - u_\\text{nom}\\|^2$ s.t. $\\hat n^T u \\ge \\text{rhs}$ 의 closed-form 해.

    KKT 분석:
      - $\\hat n^T u_\\text{nom} \\ge \\text{rhs}$ 이면 (제약 비활성, $\\lambda = 0$):
          $u^* = u_\\text{nom}$
      - 아니면 (제약 활성, $\\lambda > 0$):
          $\\lambda = \\text{rhs} - \\hat n^T u_\\text{nom}$
          $u^* = u_\\text{nom} + \\lambda \\hat n$
          검증: $\\hat n^T u^* = \\hat n^T u_\\text{nom} + \\lambda = \\text{rhs}$ ✓

    옵션 입력 saturation ($u_\\text{max}$): 결과 norm이 초과하면 비례 축소.
    """
    cbf_lhs = float(n_hat @ u_nom)
    if cbf_lhs >= rhs:
        u_safe = u_nom.copy()
        info: dict = {'constraint_active': False, 'lambda': 0.0, 'saturated': False}
    else:
        lam = rhs - cbf_lhs  # > 0
        u_safe = u_nom + lam * n_hat
        info = {'constraint_active': True, 'lambda': lam, 'saturated': False}

    if u_max is not None:
        u_norm = float(np.linalg.norm(u_safe))
        if u_norm > u_max:
            u_safe = u_safe * (u_max / u_norm)
            info['saturated'] = True

    return u_safe, info


# ----------------------------------------------------------------------
# B1 — 정적 CBF-QP (cmsm-proof §5 명제 1)
# ----------------------------------------------------------------------
def cbf_qp_velocity_static(
    u_nom: np.ndarray,
    p_drone: np.ndarray,
    p_user: np.ndarray,
    r_min: float,
    gamma: float,
    u_max: float | None = None,
) -> tuple[np.ndarray, dict]:
    """B1 — 정적 안전 마진 $r_\\text{min}$ 고정 CBF-QP.

    cmsm-proof §5 명제 1: 정적 $c$ 하 안전집합 $\\mathcal{C}_\\text{floor} = \\{x : h \\ge 0\\}$의
    전방불변성. CBF 제약 $\\hat n^T u \\ge -\\gamma h$, $\\alpha(h) = \\gamma h$ 선형 특수화.

    Parameters
    ----------
    u_nom : (3,) ENU velocity nominal (m/s).
    p_drone : (3,) ENU drone position (local frame).
    p_user : (3,) ENU user position (local frame).
    r_min : 안전 마진 하한 [m] (B1에선 그대로 $r$).
    gamma : CBF 게인 [/s] ($\\alpha(h) = \\gamma h$).
    u_max : 옵션 입력 saturation [m/s].

    Returns
    -------
    u_safe : (3,) ENU velocity safe output.
    info : dict — h, dist, constraint_active, lambda, saturated, singularity.
    """
    delta = np.asarray(p_drone, dtype=float) - np.asarray(p_user, dtype=float)
    dist = float(np.linalg.norm(delta))
    u_nom_arr = np.asarray(u_nom, dtype=float)

    if dist < 1e-6:
        # drone이 user에 정확히 (특이점) — 임의 방향 +x로 최대 후퇴.
        u_safe = np.array([u_max if u_max else 0.5, 0.0, 0.0])
        return u_safe, {
            'h': -r_min, 'dist': dist, 'singularity': True,
            'constraint_active': True, 'lambda': float('inf'), 'saturated': True,
        }

    n_hat = delta / dist
    h = dist - r_min
    rhs = -gamma * h

    u_safe, kkt_info = _cbf_qp_solve(u_nom_arr, n_hat, rhs, u_max)
    info = {'h': h, 'dist': dist, 'singularity': False, **kkt_info}
    return u_safe, info


# ----------------------------------------------------------------------
# B2 — 신뢰도 변조 CBF-QP (cmsm-proof §6 정리 2)
# ----------------------------------------------------------------------
def cbf_qp_velocity_modulated(
    u_nom: np.ndarray,
    p_drone: np.ndarray,
    p_user: np.ndarray,
    r: float,
    r_dot: float,
    gamma: float,
    u_max: float | None = None,
) -> tuple[np.ndarray, dict]:
    """B2 — 시변 $r(\\tilde c(t))$ CBF-QP.

    cmsm-proof §6 정리 2: 시변 $\\tilde c(t)$ 하 안전집합 $\\mathcal{C}(t) = \\{x : h(x,t) \\ge 0\\}$의
    전방불변성. CBF 제약 $\\hat n^T u + (r_\\text{max} - r_\\text{min}) \\dot{\\tilde c} \\ge -\\gamma h$
    → $\\hat n^T u \\ge -\\gamma h + \\dot r$ (B1과의 차이 = $+\\dot r$ 항 하나).
    $\\dot r > 0$ (영역 팽창, $\\dot{\\tilde c} < 0$) → rhs 증가 → 제약 강해짐.

    가용성 조건 (§6): $(r_\\text{max} - r_\\text{min}) \\dot{\\tilde c}_\\text{max} \\le u_\\text{max}$
    이 성립하면 경계에서 제약을 만족하는 $u \\in \\mathcal{U}$가 항상 존재. **신뢰도 추정기**가
    변화율 제한기로 $|\\dot{\\tilde c}| \\le \\dot{\\tilde c}_\\text{max}$를 강제하므로(ADR-0020 D9)
    본 함수는 그 가정을 이미 만족한 $\\dot r$를 받는다 가정. filter_node 는 추정기 출력 $c(t)$의
    변화율을 *측정* 해 $\\dot r$로 전달하며 재차 clamp 하지 않는다 (종전 docstring 의
    "호출자(filter_node)가 강제"는 D9 로 무효화). 단 측정 시 $\\Delta t$를 수신 처리 시각으로
    잡으므로 메시지 큐잉·지연 시 측정 $\\dot r$가 일시적으로 상한을 넘을 수 있다 — ADR-0020 D9
    "타이밍 한계", P5 e2e 실측 대상.

    Parameters
    ----------
    u_nom : (3,) ENU velocity nominal (m/s).
    p_drone : (3,) ENU drone position (local frame).
    p_user : (3,) ENU user position (local frame).
    r : 현재 안전 마진 $r(\\tilde c(t))$ [m]. $r \\in [r_\\text{min}, r_\\text{max}]$.
    r_dot : 현재 $\\dot r = -(r_\\text{max} - r_\\text{min}) \\dot{\\tilde c}$ [m/s].
            ($\\tilde c$ 급락 = $\\dot{\\tilde c} < 0$ → $\\dot r > 0$ = 영역 팽창.)
    gamma : CBF 게인 [/s] ($\\alpha(h) = \\gamma h$).
    u_max : 옵션 입력 saturation [m/s].

    Returns
    -------
    u_safe : (3,) ENU velocity safe output.
    info : dict — h, dist, r, r_dot, constraint_active, lambda, saturated, singularity.

    Note
    ----
    $r_\\dot = 0$이면 결과는 ``cbf_qp_velocity_static(..., r_min=r, ...)``과 정확히 동일.
    """
    delta = np.asarray(p_drone, dtype=float) - np.asarray(p_user, dtype=float)
    dist = float(np.linalg.norm(delta))
    u_nom_arr = np.asarray(u_nom, dtype=float)

    if dist < 1e-6:
        u_safe = np.array([u_max if u_max else 0.5, 0.0, 0.0])
        return u_safe, {
            'h': -r, 'dist': dist, 'r': r, 'r_dot': r_dot, 'singularity': True,
            'constraint_active': True, 'lambda': float('inf'), 'saturated': True,
        }

    n_hat = delta / dist
    h = dist - r
    rhs = -gamma * h + r_dot

    u_safe, kkt_info = _cbf_qp_solve(u_nom_arr, n_hat, rhs, u_max)
    info = {
        'h': h, 'dist': dist, 'r': r, 'r_dot': r_dot, 'singularity': False,
        **kkt_info,
    }
    return u_safe, info


# ----------------------------------------------------------------------
# Pose target boundary projection — geometric (velocity-level CBF와 별개)
# ----------------------------------------------------------------------
def _project_pose_to_safe(
    p_target: np.ndarray,
    p_user: np.ndarray,
    r: float,
) -> tuple[np.ndarray, dict]:
    """Pose target을 반경 $r$ 표면으로 projection (내부 helper).

    pose target이 안전 영역 내부 ($\\|p_\\text{target} - p_\\text{user}\\| < r$) 면
    user → target 방향의 $r$ 표면 점으로 projection. 밖이면 target 그대로 forward.

    cmsm-proof §1·§5·§6는 velocity-level만 다룸. pose target은 PX4 position
    controller가 추적하므로 그 *target 자체*가 안전 영역 내부면 controller가
    영역 안으로 drone을 가져감 → 본 helper가 그 target을 boundary로 옮겨
    위반 방지. velocity-level CBF와 별개의 *geometric* 처리.
    """
    delta = np.asarray(p_target, dtype=float) - np.asarray(p_user, dtype=float)
    dist = float(np.linalg.norm(delta))

    if dist >= r:
        return np.asarray(p_target, dtype=float), {'dist': dist, 'r': r, 'projected': False}

    if dist < 1e-6:
        # 특이점 — target이 user에 정확히 — 임의 방향 +x로 $r$ 표면.
        return np.asarray(p_user, dtype=float) + np.array([r, 0.0, 0.0]), {
            'dist': dist, 'r': r, 'projected': True, 'singularity': True,
        }

    p_safe = np.asarray(p_user, dtype=float) + r * delta / dist
    return p_safe, {'dist': dist, 'r': r, 'projected': True, 'singularity': False}


def project_pose_to_safe_static(
    p_target: np.ndarray,
    p_user: np.ndarray,
    r_min: float,
) -> tuple[np.ndarray, dict]:
    """B1 — 정적 $r_\\text{min}$ 반경으로 pose projection. cmsm-proof §5 매핑."""
    return _project_pose_to_safe(p_target, p_user, r_min)


def project_pose_to_safe_modulated(
    p_target: np.ndarray,
    p_user: np.ndarray,
    r: float,
) -> tuple[np.ndarray, dict]:
    """B2 — 시변 $r(\\tilde c)$ 반경으로 pose projection. cmsm-proof §6 매핑.

    velocity CBF와 달리 pose projection은 $\\dot r$를 받지 않는다 — 매 timestep
    *현재* $r$로 geometric projection만 수행. 시변성은 $r$ 값이 callback마다
    갱신되는 것으로 자연 반영.
    """
    return _project_pose_to_safe(p_target, p_user, r)
