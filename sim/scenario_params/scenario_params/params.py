"""시나리오별 사용자 좌표 단일 진실 소스.

## 데이터 출처

- world: SDF 파일의 사용자 위치 (월드 좌표계)
- spawn: PX4 SITL 드론 spawn 위치 (scripts/run_native_sitl_*.sh 기본값)
- local: world − spawn (드론 spawn 상대 ENU, tier1 CBF 계산용)
    부동소수점 정합을 위해 명시적으로 저장 (1.1 - 0.15 ≠ 0.95 in Python IEEE 754)

## 파생 파라미터

- user_marker_params(scenario)  → user_x/y/z (월드 좌표) + r_min
- tier1_local_params(scenario)  → user_local_x/y/z (local ENU) + r_min

## 좌표 검증표

| scenario   | world xyz           | spawn xyz           | local xyz            |
|------------|---------------------|---------------------|----------------------|
| livingroom | ( 0.0,  1.5,  1.1)  | ( 0.5, −0.5,  0.15) | (−0.5,  2.0,  0.95) |
| yard       | ( 0.0, −3.0,  1.1)  | ( 0.0, −2.0,  0.15) | ( 0.0, −1.0,  0.95) |

v4.1 layout (2026-05-30): livingroom 사용자가 소파(-1.8, 1.5) 동쪽 옆자리
(0, 1.5)로 이동. sofa 자체는 원위치 유지. 기존 v3 (-2.6, 1.5)는 sofa 박스
footprint 안에 들어가 시각·물리적으로 겹쳤음. 중간 시안 v4 (0, 0) 거실
정중앙은 drone dock(0.5, -0.5)과 3D 거리 1.18m로 회피 영역 r=0.9 마진이 빠듯해
이륙·hover 불안정 → v4.1에서 소파 옆자리로 이동, dock 3D 거리 ≈ 2.29m 안전.
"""

from __future__ import annotations

from typing import Dict, List


_SCENARIOS: Dict[str, Dict] = {
    'livingroom': {
        'world': ( 0.0,  1.5,  1.1),
        'spawn': ( 0.5, -0.5,  0.15),
        'local': (-0.5,  2.0,  0.95),  # world − spawn (명시적 저장, v4.1 layout 2026-05-30)
        'r_min': 0.9,
    },
    'yard': {
        'world': ( 0.0, -3.0,  1.1),
        'spawn': ( 0.0, -2.0,  0.15),
        'local': ( 0.0, -1.0,  0.95),  # world − spawn (명시적 저장, S8 layout)
        'r_min': 0.9,
    },
}

VALID_SCENARIOS: frozenset = frozenset(_SCENARIOS.keys())


# paper §C scenario_id → 물리 장소(location) 매핑 — ADR-0006 + ADR-0039 D2 정합.
# S5/S6 = 거실(livingroom). 본 매핑이 scenario_id ↔ location 의 *단일 진실 소스*
# — eval(panel) + intent(context graph) 측 공통 참조. **S7 폐기·S8 paper-2 이관**
# (ADR-0039: 시나리오 축 = C2 신뢰도 스펙트럼 전용 → 거실 S5 모호 + S6 단일 2점).
# yard scene·SDF·build_yard_people 는 paper-2 인프라로 *보존*(본실험 격자에서만 제외).
SCENARIO_LOCATION: Dict[str, str] = {
    'S5': 'livingroom',
    'S6': 'livingroom',
}


def scenario_location(scenario_id: str) -> str:
    """scenario_id → 물리 장소 'livingroom' | 'yard'.

    Raises:
        RuntimeError: scenario_id 측 SCENARIO_LOCATION 외.
    """
    if scenario_id not in SCENARIO_LOCATION:
        raise RuntimeError(
            f'scenario_id={scenario_id!r} 측 unknown — '
            f'허용 = {sorted(SCENARIO_LOCATION)!r}'
        )
    return SCENARIO_LOCATION[scenario_id]


# paper §C 본실험 trial 측 시나리오별 사용자 발화 — 자동 격자에서 trial 시작 시
# 1회 발행(eval_runner.launch_composition 의 per-trial 발화 publisher, ADR-0030 F5).
# 종전 수동 `ros2 topic pub /intent/user_prompt_raw` 를 trial 합성으로 흡수해 nominal
# 사슬(발화→wrapper σ→sigma_bridge→follower→tier1→setpoint_safe)을 자동 구동.
# 발화 referent 는 scene 객체(scene.py) 중 시나리오 의도 대상 — grounding 백본
# (gpt-4o·gemma fusion)이 target 위치를 lookup, keyword 백본은 skill 만(args 공백).
_SCENARIO_UTTERANCE: Dict[str, str] = {
    # S5 = 모호 referent (ADR-0035/ADR-0006): 식탁 위 외형 동일 머그컵 3개라
    # 발화만으로 1개를 지시할 수 없다 → 명료화 루프 유발(C2 의도 기둥①).
    'S5': '내 머그컵 어디 있는지 보여줘',
    'S6': '소파 보여줘',
}


def scenario_utterance(scenario_id: str) -> str:
    """scenario_id → 본실험 trial 사용자 발화 (한국어).

    Raises:
        RuntimeError: scenario_id 측 _SCENARIO_UTTERANCE 외.
    """
    if scenario_id not in _SCENARIO_UTTERANCE:
        raise RuntimeError(
            f'scenario_id={scenario_id!r} 측 unknown — '
            f'허용 = {sorted(_SCENARIO_UTTERANCE)!r}'
        )
    return _SCENARIO_UTTERANCE[scenario_id]


# scenario_id → 발화 지시 대상의 OVD 클래스 라벨 (scene.py ``ovd_class`` 정합).
# _SCENARIO_UTTERANCE 의 한국어 지시 대상과 대응: "머그컵"→cup, "소파"→sofa.
# post-hoc 작업 성공(SR) 평가기(ADR-0032 D2)가 기대 vantage 의 후보 클러스터를 이
# 클래스로 거른다 — 단일 진실 소스. sigma_bridge 의 live 후보 해소
# (σ.theta.target_class, ADR-0029 블로커 1)와 동일 클래스 입도.
_SCENARIO_TARGET_CLASS: Dict[str, str] = {
    'S5': 'cup',
    'S6': 'sofa',
}


def scenario_target_class(scenario_id: str) -> str:
    """scenario_id → 발화 지시 대상의 OVD 클래스 라벨 (scene.py ``ovd_class``).

    Args:
        scenario_id: 'S5' | 'S6'.

    Returns:
        OVD 클래스 라벨 ('cup' | 'sofa') — scene_objects_for_location
        의 ``ovd_class`` 필드와 매칭하는 데 사용.

    Raises:
        RuntimeError: scenario_id 측 _SCENARIO_TARGET_CLASS 외.
    """
    if scenario_id not in _SCENARIO_TARGET_CLASS:
        raise RuntimeError(
            f'scenario_id={scenario_id!r} 측 unknown — '
            f'허용 = {sorted(_SCENARIO_TARGET_CLASS)!r}'
        )
    return _SCENARIO_TARGET_CLASS[scenario_id]


def scenario_ovd_vocab(scenario_id: str) -> List[str]:
    """scenario_id → 해당 장소의 OVD 정적 어휘 (scene.py 단일 소스 파생).

    발화 referent 의 OVD 클래스(``scenario_target_class``)가 *반드시* 포함된다
    (scene ``ovd_class`` 에서 파생). 수동 e2e(start_intent_stack.sh, 시나리오 단위)
    용. 본실험 격자 영속 OVD 는 전 시나리오 합집합(``scene.ovd_vocabulary_all``)을
    쓴다 (detector 단일 인스턴스가 거실+마당을 모두 서빙).

    Args:
        scenario_id: 'S5' | 'S6'.

    Returns:
        정렬된 OVD 클래스 라벨 list — ``scenario_target_class(scenario_id)`` 포함.

    Raises:
        RuntimeError: scenario_id unknown (scenario_location 위임).
    """
    from scenario_params.scene import ovd_vocabulary_for_location
    return ovd_vocabulary_for_location(scenario_location(scenario_id))


# ── CBF spec 상수 (cmsm-proof §7.1, 시나리오 무관) ───────────────────────────
# P3 u_max = 0.5 m/s (EASA C2 conservative scaling), P4 gamma = 4.0 /s
# (= 1/τ_ctrl, PX4 OFFBOARD velocity tracking). r_min 은 시나리오별 (_SCENARIOS).
U_MAX: float = 0.5
GAMMA: float = 4.0


# scenario_id (S5/S6) → r_max [m]. ADR-0023 잠금 — task feasibility ∩ dock
# clearance derive (v4.1 배포 기하). r_min = 0.9 균일. dot_c_max 는 파생
# (tier1_cbf_params). location (livingroom) 키가 아니라 scenario_id 키 —
# S5/S6 모두 livingroom·r_max 1.80 (소파 viewpoint task binding).
_R_MAX_BY_SCENARIO: Dict[str, float] = {
    # ADR-0023 amendment (세션 49): S5/S6 을 2.00(도크 clearance 바인딩, 사용자–도크
    # 2.325)에서 1.80 으로 인하. 종전 2.00 은 *소파 작업 viewpoint(사용자에서 1.93 m)*
    # 를 r_max 회피 영역 *안* 에 넣어 정적 r_max(B1B) 가 소파 도달 불가(SR=0)이고
    # B2 가 결함으로 c↓→r→r_max 시 영역 확장으로 infeasibility(V>0) 발생(세션 49 격자
    # 진단). 1.80 < 1.93(소파) < 2.325(도크) → 소파 도달 가능 + 도크 clearance 유지.
    'S5': 1.80,  # task binding (소파 viewpoint 1.93, was 2.00 dock-bound)
    'S6': 1.80,  # task binding (소파 viewpoint 1.93, was 2.00 dock-bound)
}

# scenario_id 단일 집합 — SCENARIO_LOCATION 키와 동일 (단일 진실 소스).
VALID_SCENARIO_IDS: frozenset = frozenset(SCENARIO_LOCATION.keys())


def cbf_availability_margin(
    r_min: float, r_max: float, u_max: float, dot_c_max: float
) -> float:
    """cmsm-proof §6 가용성 여유 = u_max − (r_max − r_min)·dot_c_max.

    음이 아니면 변화율 제한된 신뢰도 거동이 입력 제약 안에서 실현 가능 (T2-4).
    dot_c_max = u_max/(r_max − r_min) derive 시 정확히 0 (등호, 가장 빡빡).
    """
    return u_max - (r_max - r_min) * dot_c_max


def is_cbf_available(
    r_min: float, r_max: float, u_max: float, dot_c_max: float, tol: float = 1e-9
) -> bool:
    """가용성 조건 (r_max − r_min)·dot_c_max ≤ u_max 만족 여부."""
    return cbf_availability_margin(r_min, r_max, u_max, dot_c_max) >= -tol


def tier1_cbf_params(scenario_id: str) -> Dict[str, float]:
    """scenario_id (S5/S6) → tier1_filter CBF 파라미터 일괄 (단일 진실 소스).

    user_local 좌표 + r_min 은 location lookup (tier1_local_params), r_max 는
    ADR-0023 시나리오별 잠금, dot_c_max 는 cmsm-proof §6 가용성에서 파생
    (dot_c_max = u_max/(r_max − r_min), C11 해소).

    Args:
        scenario_id: 'S5' | 'S6'.

    Returns:
        dict — user_local_x/y/z + r_min + r_max + gamma + u_max + dot_c_max
        (8 keys). 매 호출마다 새 dict.

    Raises:
        RuntimeError: scenario_id 측 VALID_SCENARIO_IDS 외.
    """
    if scenario_id not in _R_MAX_BY_SCENARIO:
        raise RuntimeError(
            f'scenario_id={scenario_id!r} 측 unknown — '
            f'허용 = {sorted(_R_MAX_BY_SCENARIO)!r}'
        )
    location = SCENARIO_LOCATION[scenario_id]
    params = tier1_local_params(location)  # user_local_x/y/z + r_min
    r_max = _R_MAX_BY_SCENARIO[scenario_id]
    r_min = params['r_min']
    params.update({
        'r_max': r_max,
        'gamma': GAMMA,
        'u_max': U_MAX,
        'dot_c_max': U_MAX / (r_max - r_min),  # 파생 (가용성 등호)
    })
    return params


def _get(scenario: str) -> Dict:
    if scenario not in _SCENARIOS:
        raise RuntimeError(
            f"scenario={scenario!r} 측 unknown — "
            f"허용 = {sorted(_SCENARIOS.keys())!r}"
        )
    return _SCENARIOS[scenario]


def user_marker_params(scenario: str) -> Dict[str, float]:
    """sim_user_marker 노드용 파라미터 반환.

    Args:
        scenario: 'livingroom' (default) | 'yard'.

    Returns:
        dict — user_x/y/z (월드 좌표) + r_min (4 keys). 매 호출마다 새 dict.

    Raises:
        RuntimeError: scenario 측 unknown.
    """
    s = _get(scenario)
    wx, wy, wz = s['world']
    return {'user_x': wx, 'user_y': wy, 'user_z': wz, 'r_min': s['r_min']}


def spawn_params(scenario: str) -> Dict[str, float]:
    """드론 spawn 위치 (world ENU) 반환 — sigma_bridge world→local 변환용.

    world 좌표(context_graph 객체)를 PX4 local frame(spawn 기준) setpoint로
    바꿀 때 spawn offset 을 빼는 데 사용 (local = world − spawn).

    Args:
        scenario: 'livingroom' (default) | 'yard'.

    Returns:
        dict — spawn_x/y/z (world ENU). 매 호출마다 새 dict.

    Raises:
        RuntimeError: scenario 측 unknown.
    """
    s = _get(scenario)
    sx, sy, sz = s['spawn']
    return {'spawn_x': sx, 'spawn_y': sy, 'spawn_z': sz}


def tier1_local_params(scenario: str) -> Dict[str, float]:
    """tier1_filter용 파라미터 반환.

    Args:
        scenario: 'livingroom' (default) | 'yard'.

    Returns:
        dict — user_local_x/y/z (드론 spawn 상대 ENU) + r_min (4 keys). 매 호출마다 새 dict.

    Raises:
        RuntimeError: scenario 측 unknown.
    """
    s = _get(scenario)
    lx, ly, lz = s['local']
    return {
        'user_local_x': lx,
        'user_local_y': ly,
        'user_local_z': lz,
        'r_min': s['r_min'],
    }
