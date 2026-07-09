"""experiment control panel — 격자 구성·미리보기·export·실행 매핑 순수 로직.

웹 패널 ([scripts/experiment_panel.py](../../../scripts/experiment_panel.py)) 의
백엔드. *순수 함수* 만 — HTTP·I/O 부재 → host venv pytest 로 완전 cover.

paper §C 격자 (ADR-0025 D3 + ADR-0039 D2) 는 4 차원 — scenario(거실 S5/S6) ×
baseline(B0·B1a·B1b·B2·B3·B4) × fault(5종) × episode. 본 모듈은 사용자 선택 →
`eval_runner.grid.generate_trial_grid`
호출 → 미리보기·export·단일 trial 실행 env 매핑을 담당.

## 책임 분리

- 격자 enumeration·seed 도출: `eval_runner.grid` 재사용 (본 모듈 미복제).
- 무인 배치 실행: runner.py (미구현, B7 #12 분할 2/N) — 본 모듈 *밖*.
- 단일 trial sim 환경 기동: `up_sh_env_for_trial` 측 scripts/up.sh env 매핑만
  산출 (실 subprocess 기동은 패널 서버 측 책임). up.sh 는 *sim 환경* (장소 +
  tier1 mode) 만 구성 — fault/intent layer/Tier 2/rosbag 은 runner.py 필요.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from eval_baselines.schemas import BaselineMode

from eval_runner.grid import (
    BASELINE_HELPERS,
    default_fault_scenario_paths,
    generate_trial_grid,
)
from eval_runner.launch_composition import DEFAULT_BACKBONE
from eval_runner.schemas import VALID_SCENARIO_IDS, TrialSpec

try:
    from eval_faults.fault_scenario import load_fault_scenario
except ImportError:  # pragma: no cover - conftest 측 경로 주입 정합
    load_fault_scenario = None  # type: ignore


# scenario_id → 장소(location) 매핑 — 단일 진실 소스 = scenario_params.params
# (sim/scenario_params/). S5/S6 = 거실(livingroom); yard 는 paper-2 보존(ADR-0039 D2). 데이터를
# 직접 import 해 중복 정의 회피 (C31 single-source 정합).
from scenario_params.params import SCENARIO_LOCATION  # noqa: E402

# 완전성 guard — scenario_params 매핑이 eval_runner 격자 차원과 정합.
assert set(SCENARIO_LOCATION) == set(VALID_SCENARIO_IDS), (
    'SCENARIO_LOCATION (scenario_params) 측 VALID_SCENARIO_IDS 와 불일치 — '
    f'{sorted(SCENARIO_LOCATION)} ≠ {sorted(VALID_SCENARIO_IDS)}'
)


def scenario_location(scenario_id: str) -> str:
    """scenario_id → 장소(location) 'livingroom' | 'yard' (panel 측 ValueError 계약)."""
    if scenario_id not in SCENARIO_LOCATION:
        raise ValueError(
            f'scenario_id={scenario_id!r} 무효 — {sorted(SCENARIO_LOCATION)} 중 하나'
        )
    return SCENARIO_LOCATION[scenario_id]


def _fault_registry() -> Dict[str, Path]:
    """fault YAML name → path 매핑 (default 5종)."""
    if load_fault_scenario is None:
        raise RuntimeError('eval_faults import 불가 — PYTHONPATH 측 eval/faults 확인')
    registry: Dict[str, Path] = {}
    for path in default_fault_scenario_paths():
        fs = load_fault_scenario(path)
        registry[fs.name] = path
    return registry


def _backbone_identifiers() -> List[str]:
    """등록된 intent_llm backbone 식별자 — registry 단일 소스.

    lazy import (registry → cloud_llm → openai 측 무거운 의존 회피, options 호출
    시점에만 로드). intent_llm import 불가 시 RuntimeError.
    """
    try:
        from intent_llm.registry import list_registered  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - 경로 미주입 시
        raise RuntimeError(
            'intent_llm import 불가 — PYTHONPATH 측 intent/llm 확인'
        ) from exc
    return list(list_registered())


def build_options() -> Dict[str, Any]:
    """패널 UI 측 선택지 — scenario·baseline·fault·backbone·default 메타데이터."""
    scenarios = [
        {'id': sid, 'location': SCENARIO_LOCATION[sid]}
        for sid in VALID_SCENARIO_IDS
    ]

    baselines: List[Dict[str, Any]] = []
    for mode in BaselineMode:
        if mode not in BASELINE_HELPERS:
            continue
        cfg = BASELINE_HELPERS[mode]()
        baselines.append({
            'mode': mode.value,
            'tier1_mode': cfg.tier1_mode,
            'context_aug': cfg.context_aug,
            'tier2_enabled': cfg.tier2_enabled,
        })

    faults: List[Dict[str, Any]] = []
    for path in default_fault_scenario_paths():
        fs = load_fault_scenario(path)  # type: ignore[misc]
        faults.append({
            'name': fs.name,
            'channel': fs.channel.value,
            'variant': fs.variant,
        })

    backbones = [{'id': bid} for bid in _backbone_identifiers()]

    return {
        'scenarios': scenarios,
        'baselines': baselines,
        'faults': faults,
        'backbones': backbones,
        'defaults': {'n_episodes': 10, 'backbone': DEFAULT_BACKBONE},
    }


def _resolve_baseline_modes(values: Sequence[str]) -> List[BaselineMode]:
    out: List[BaselineMode] = []
    valid = {m.value: m for m in BaselineMode}
    for v in values:
        if v not in valid:
            raise ValueError(
                f'baseline={v!r} 무효 — {sorted(valid)} 중 하나'
            )
        out.append(valid[v])
    return out


def _resolve_fault_paths(names: Sequence[str]) -> List[Path]:
    registry = _fault_registry()
    out: List[Path] = []
    for name in names:
        if name not in registry:
            raise ValueError(
                f'fault={name!r} 무효 — {sorted(registry)} 중 하나'
            )
        out.append(registry[name])
    return out


def trial_to_record(trial: TrialSpec) -> Dict[str, Any]:
    """TrialSpec → JSON 직렬화 가능 dict (미리보기·export 행)."""
    return {
        'trial_id': trial.trial_id,
        'scenario_id': trial.scenario_id,
        'location': SCENARIO_LOCATION[trial.scenario_id],
        'baseline': trial.baseline_config.mode.value,
        'tier1_mode': trial.baseline_config.tier1_mode,
        'context_aug': trial.baseline_config.context_aug,
        'tier2_enabled': trial.baseline_config.tier2_enabled,
        'fault_channel': trial.fault_scenario.channel.value,
        'fault_variant': trial.fault_scenario.variant,
        'episode_id': trial.episode_id,
        'seed': trial.seed,
    }


def build_grid_preview(
    scenarios: Sequence[str],
    baselines: Sequence[str],
    faults: Sequence[str],
    n_episodes: int,
    sample_n: int = 12,
) -> Dict[str, Any]:
    """사용자 선택 → 격자 미리보기 (총 trial 수 + 차원 분해 + 샘플 행).

    Args:
        scenarios: scenario_id 값 list (S5/S6 부분집합).
        baselines: baseline mode 값 list ('b0'-'b4').
        faults: fault YAML name list.
        n_episodes: 각 cell 반복 수.
        sample_n: 반환할 샘플 행 수 (앞에서부터).

    Returns:
        dict — total, breakdown(차원별 수), locations(선택된 장소 집합), sample 행.
    """
    modes = _resolve_baseline_modes(baselines)
    fault_paths = _resolve_fault_paths(faults)
    grid = generate_trial_grid(scenarios, modes, fault_paths, n_episodes)

    locations = sorted({SCENARIO_LOCATION[s] for s in scenarios})
    return {
        'total': len(grid),
        'breakdown': {
            'scenarios': len(scenarios),
            'baselines': len(modes),
            'faults': len(fault_paths),
            'episodes': n_episodes,
        },
        'locations': locations,
        'sample': [trial_to_record(t) for t in grid[:sample_n]],
    }


def export_grid_json(
    scenarios: Sequence[str],
    baselines: Sequence[str],
    faults: Sequence[str],
    n_episodes: int,
    output_path: Union[str, Path],
) -> Dict[str, Any]:
    """격자 전체를 JSON 파일로 export. 반환 = {path, total}."""
    modes = _resolve_baseline_modes(baselines)
    fault_paths = _resolve_fault_paths(faults)
    grid = generate_trial_grid(scenarios, modes, fault_paths, n_episodes)

    records = [trial_to_record(t) for t in grid]
    payload = {
        'meta': {
            'scenarios': list(scenarios),
            'baselines': list(baselines),
            'faults': list(faults),
            'n_episodes': n_episodes,
            'total': len(grid),
        },
        'trials': records,
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'path': str(out), 'total': len(grid)}


def up_sh_env_for_trial(
    scenario_id: str,
    baseline: str,
    g2_scenario: Optional[str] = None,
) -> Dict[str, str]:
    """단일 trial → scripts/up.sh env 매핑 (sim 환경 기동용).

    up.sh 는 *sim 환경* (장소 + tier1 mode + 선택적 g2 waypoint) 만 구성 —
    fault injection / intent layer / Tier 2 게이트 / trial rosbag 은 runner.py
    (미구현) 필요. 따라서 본 매핑은 *대화형 단일 trial sim 점검* 용도.

    Args:
        scenario_id: 'S5'/'S6' → SCENARIO (장소) 도출.
        baseline: 'b0'·'b1a'·'b1b'·'b2'-'b4' → tier1_mode 도출 (B1b 측 'b1_max',
            B3/B4 측 tier1_mode='b2').
        g2_scenario: g2_waypoint_player 시나리오 name. None/'' 측 g2 미기동
            (teleop 측 conflict 회피 — NEXT_SESSION 정합).

    Returns:
        dict[str, str] — SCENARIO, TIER1_MODE, G2_SCENARIO env.
    """
    modes = _resolve_baseline_modes([baseline])
    cfg = BASELINE_HELPERS[modes[0]]()
    return {
        'SCENARIO': scenario_location(scenario_id),
        'TIER1_MODE': cfg.tier1_mode,
        'G2_SCENARIO': g2_scenario or '',
    }


def runner_command(
    scenarios: Sequence[str],
    baselines: Sequence[str],
    faults: Sequence[str],
    n_episodes: int,
    backbones: Sequence[str],
    output_root: str = 'results/trials',
) -> str:
    """선택 격자 + backbone(들) → eval-runner 무인 배치 실행 명령.

    backbone 은 run-level (격자 차원 아님) — 선택 backbone 별 *별 run*. 1개 측
    단일 명령, 2개 이상 측 bash for-loop (backbone 별 1000 trial 반복).

    Args:
        scenarios/baselines/faults/n_episodes: 5-dim 격자 선택.
        backbones: registry 식별자 list (1개 이상).
        output_root: trial 출력 루트 (`<output_root>/<backbone>/<trial_id>`).

    Returns:
        실행 명령 문자열 (bash). eval-runner 콘솔 스크립트(colcon build 후) 기준.

    Raises:
        ValueError: backbones 빈 list 또는 미등록 식별자.
    """
    if not backbones:
        raise ValueError('backbones 빈 list 불가 — 최소 1개 backbone 필요')
    registered = set(_backbone_identifiers())
    for bb in backbones:
        if bb not in registered:
            raise ValueError(
                f'backbone={bb!r} 무효 — {sorted(registered)} 중 하나'
            )

    args = (
        f"--scenarios {' '.join(scenarios)} "
        f"--baselines {' '.join(baselines)} "
        f"--faults {' '.join(faults)} "
        f"--n-episodes {n_episodes} "
        f"--output-root {output_root}"
    )
    if len(backbones) == 1:
        return f'eval-runner {args} --backbone {backbones[0]}'
    bb_list = ' '.join(backbones)
    return (
        f'for bb in {bb_list}; do\n'
        f'  eval-runner {args} --backbone "$bb"\n'
        f'done'
    )
