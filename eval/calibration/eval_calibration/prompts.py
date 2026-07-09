"""Scenario YAML loader + ScenarioSpec dataclass 채움.

paper §C 시뮬 indoor 4 시나리오 (S5/S6/S7/S8) 의 정상 prompt 로드.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

from eval_calibration.schemas import ScenarioSpec, TypedAction


def _parse_typed_action(raw: Optional[dict]) -> Optional[TypedAction]:
    if not raw:
        return None
    sigma = raw.get('sigma')
    theta = raw.get('theta', {})
    if sigma is None:
        return None
    return TypedAction(sigma=str(sigma), theta=dict(theta))


def load_scenario(path: Path) -> ScenarioSpec:
    """YAML → ScenarioSpec.

    PR #86 review R1 amendment: `expected_action.theta.position` (또는 `target_id`)
    가 *source of truth*. 최상위 `expected_position` / `expected_target_id` 는
    *derived field* — YAML 에서 생략 시 theta 측에서 자동 fill, 둘 다 명시 시
    값 일치를 강제 (silent inconsistency 방지). analyze.py 의 deltas 계산이
    derived field 측을 쓰는데 theta 측이 정답이라 두 값이 어긋나면 측정 silent
    오류 발생.

    Args:
        path: scenarios/{S5,S6,S7,S8}_*.yaml 경로 (절대 or 상대).

    Raises:
        ValueError: 필수 키 누락 또는 derived field 측 정합성 위반
        FileNotFoundError: 파일 없음
    """
    if not path.exists():
        raise FileNotFoundError(f'시나리오 YAML 없음: {path}')
    with path.open('r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    for required in ('scenario_id', 'description', 'user_prompt'):
        if required not in data:
            raise ValueError(f'필수 키 누락 — {required} ({path})')

    expected_action = _parse_typed_action(data.get('expected_action'))

    expected_position = _resolve_expected_position(
        explicit=data.get('expected_position'),
        action=expected_action,
        path=path,
    )
    expected_target_id = _resolve_expected_target_id(
        explicit=data.get('expected_target_id'),
        action=expected_action,
        path=path,
    )

    return ScenarioSpec(
        scenario_id=str(data['scenario_id']),
        description=str(data['description']),
        user_prompt=str(data['user_prompt']),
        expected_action=expected_action,
        expected_position=expected_position,
        expected_target_id=expected_target_id,
        known_objects=list(data.get('known_objects', [])),
        known_object_positions=_parse_known_object_positions(
            data.get('known_object_positions'), path
        ),
        move_to_probes=_parse_move_to_probes(data.get('move_to_probes'), path),
    )


def _parse_move_to_probes(raw, path: Path) -> list:
    """move_to_probes [{prompt, expected_object}] 파싱 (ADR-0025 amend 13).

    None/부재 시 빈 list. 각 probe 는 prompt + expected_object 필수.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f'move_to_probes 는 list — got {type(raw).__name__} ({path})'
        )
    probes = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict) or 'prompt' not in p or 'expected_object' not in p:
            raise ValueError(
                f'move_to_probes[{i}] 는 {{prompt, expected_object}} 필수 — '
                f'got {p!r} ({path})'
            )
        probes.append({'prompt': str(p['prompt']),
                       'expected_object': str(p['expected_object'])})
    return probes


def _parse_known_object_positions(raw, path: Path) -> Dict[str, tuple]:
    """known_object_positions {name: [x,y,z]} → {name: (x,y,z)} (ADR-0025 amend 12).

    None/부재 시 빈 dict (context-absent calibration만 가능). 각 좌표는 3-tuple 강제.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f'known_object_positions 는 dict — got {type(raw).__name__} ({path})'
        )
    return {str(name): _normalize_position(pos, path) for name, pos in raw.items()}


def _normalize_position(value, path: Path) -> tuple:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(
            f'position 은 3-tuple 이어야 — got {value} ({path})'
        )
    return tuple(float(v) for v in value)


_SIGMAS_NO_DERIVED = {'ask_user', 'return_to_dock', 'emergency_land'}
"""theta 측에 position·target_id 모두 *부재* 인 sigma 들 — explicit 명시 시 거부."""


def _resolve_expected_position(explicit, action, path: Path):
    """expected_position derived field resolver.

    PR #87 review R-2nd-A amendment: sigma 와 expected_position 호환성 검사.

    - sigma=move_to → theta.position 이 source of truth. explicit 생략 시
      auto-fill, 명시 시 일치 강제.
    - sigma=inspect → expected_position 허용 (S8 의 *dual purpose* 패턴 정합 —
      inspect 의 viewpoint 좌표가 SDF 측 도출값일 수 있음). theta 측 검증 없음
      (analyze.py 의 position_xyz_cm 가 inspect 출력에는 N/A 라 metric 오류
      위험 없음).
    - sigma=ask_user/return_to_dock/emergency_land → expected_position 무관,
      explicit 명시 시 ValueError (silent pass 거부).
    - action=None (expected_action 미명시) → explicit 그대로 (legacy 호환).
    """
    if action is None:
        if explicit is None:
            return None
        return _normalize_position(explicit, path)

    if action.sigma in _SIGMAS_NO_DERIVED:
        if explicit is not None:
            raise ValueError(
                f'expected_position 은 sigma={{ask_user, return_to_dock, '
                f'emergency_land}} 에서 허용 안 됨 — sigma="{action.sigma}" '
                f'인데 expected_position={explicit} 명시됨 ({path})'
            )
        return None

    if action.sigma != 'move_to':
        # sigma=inspect — explicit 허용 (dual purpose), 단 theta 측 검증은 없음
        if explicit is None:
            return None
        return _normalize_position(explicit, path)

    # sigma == 'move_to' — theta.position 이 source of truth
    theta_pos = action.theta.get('position')
    if theta_pos is not None:
        theta_pos = _normalize_position(theta_pos, path)

    if explicit is None:
        return theta_pos  # auto-fill

    explicit_norm = _normalize_position(explicit, path)
    if theta_pos is not None and explicit_norm != theta_pos:
        raise ValueError(
            f'expected_position {explicit_norm} 가 expected_action.theta.position '
            f'{theta_pos} 와 불일치 — source of truth = theta ({path})'
        )
    return explicit_norm


def _resolve_expected_target_id(explicit, action, path: Path):
    """expected_target_id derived field resolver.

    PR #87 review R-2nd-A amendment: expected_position 과 *대칭* 패턴.

    - sigma=inspect → theta.target_id 가 source of truth. explicit 생략 시
      auto-fill, 명시 시 일치 강제.
    - sigma=move_to → expected_target_id 허용 (S8 의 *dual purpose* — fault
      injection 측 reference). theta 측 검증 없음.
    - sigma=ask_user/return_to_dock/emergency_land → 명시 시 ValueError.
    - action=None → explicit 그대로 (legacy).
    """
    if action is None:
        return explicit

    if action.sigma in _SIGMAS_NO_DERIVED:
        if explicit is not None:
            raise ValueError(
                f'expected_target_id 는 sigma={{ask_user, return_to_dock, '
                f'emergency_land}} 에서 허용 안 됨 — sigma="{action.sigma}" '
                f'인데 expected_target_id="{explicit}" 명시됨 ({path})'
            )
        return None

    if action.sigma != 'inspect':
        # sigma=move_to — explicit 허용 (S8 dual purpose), theta 측 검증 없음
        return explicit

    # sigma == 'inspect' — theta.target_id 가 source of truth
    theta_tid = action.theta.get('target_id')

    if explicit is None:
        return theta_tid

    if theta_tid is not None and explicit != theta_tid:
        raise ValueError(
            f'expected_target_id "{explicit}" 가 expected_action.theta.target_id '
            f'"{theta_tid}" 와 불일치 — source of truth = theta ({path})'
        )
    return explicit


def discover_scenarios(scenarios_dir: Path) -> Dict[str, ScenarioSpec]:
    """디렉터리의 *.yaml 모두 load.

    Returns:
        {scenario_id → ScenarioSpec} dict.
    """
    if not scenarios_dir.is_dir():
        raise FileNotFoundError(f'scenarios 디렉터리 없음: {scenarios_dir}')
    result: Dict[str, ScenarioSpec] = {}
    for yaml_path in sorted(scenarios_dir.glob('*.yaml')):
        spec = load_scenario(yaml_path)
        if spec.scenario_id in result:
            raise ValueError(
                f'중복 scenario_id "{spec.scenario_id}" — {yaml_path} vs '
                f'{result[spec.scenario_id]}'
            )
        result[spec.scenario_id] = spec
    return result
