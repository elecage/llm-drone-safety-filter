"""격자 측 ablation chain invariant 자동 검증.

[eval/baselines/test/test_b4_full_loop.py](../../baselines/test/test_b4_full_loop.py)
+ PR #119 측 ``TestAblationChainInvariant`` 측 *baseline 6 종 사이 ablation chain*
invariant (B0→B1a·B1a→B1b·B1b→B2·B2→B3·B3→B4 각 step 정확히 1 축 차이) 측 *격자
차원* 측 확장 (ADR-0025 amendment 19 — B1→B1a/B1b). runner 측 격자 enumeration
측 동일 (scenario · fault · episode) cell 내 baseline 6 종 추출 측 chain invariant
자동 만족 보장.

[ADR-0025 D3](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d3)
+ paper §C 표 1 측 6 행 측 단축 차이 측 *ablation 측 효과 분리 측정* 보장 — 본
invariant 가 격자 측 정합성 *코드상 잠금*.

호출 패턴:

    grid = generate_trial_grid(...)
    check_chain_invariant(grid)  # 위반 측 AssertionError raise

본 invariant 측 paper §C 표 1 측 6 행 측 *행 사이 단일 축 변경 만으로 효과
측정 가능* 보장 — 본 invariant 위반 측 ablation 해석 무의미 (다축 변경 시
효과 혼합).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from eval_baselines.schemas import BaselineConfig, BaselineMode

from eval_runner.schemas import TrialSpec


# B0 → B1a → B1b → B2 → B3 → B4 ablation chain — paper §C 표 1 측 6 행 측 단축
# 차이 (ADR-0025 amendment 19 — B1→B1a/B1b 분리). B0→B1a→B1b→B2 는 모두
# tier1_mode 단독 차이(b0→b1→b1_max→b2), B2→B3 는 context_aug, B3→B4 는
# tier2_enabled.
ABLATION_CHAIN: Tuple[Tuple[BaselineMode, BaselineMode], ...] = (
    (BaselineMode.B0, BaselineMode.B1A),
    (BaselineMode.B1A, BaselineMode.B1B),
    (BaselineMode.B1B, BaselineMode.B2),
    (BaselineMode.B2, BaselineMode.B3),
    (BaselineMode.B3, BaselineMode.B4),
)


def _config_axes(config: BaselineConfig) -> Tuple[str, bool, bool]:
    """BaselineConfig 측 3 축 tuple — (tier1_mode, context_aug, tier2_enabled)."""
    return (config.tier1_mode, config.context_aug, config.tier2_enabled)


def _axis_diff_count(a: BaselineConfig, b: BaselineConfig) -> int:
    """두 BaselineConfig 측 3 축 차이 수."""
    aa = _config_axes(a)
    bb = _config_axes(b)
    return sum(1 for x, y in zip(aa, bb) if x != y)


def check_chain_invariant(grid: Sequence[TrialSpec]) -> None:
    """격자 측 모든 (scenario · fault_channel · fault_variant · episode) cell 측
    baseline 6 종 사이 ablation chain invariant 자동 검증.

    Args:
        grid: generate_trial_grid 측 출력 또는 호환 list[TrialSpec].

    Raises:
        AssertionError: 다음 위반 측 message 와 함께 raise —
            (1) cell 측 baseline 누락 (6 종 미만)
            (2) cell 측 baseline 중복 (동일 mode 측 2 회 이상)
            (3) ABLATION_CHAIN 측 인접 baseline 측 *정확히 1 축 차이* 위반
                (PR #118 review C-1 lesson 정합)

    Note:
        본 검증 측 *cell-level* — 동일 (scenario · fault_channel · fault_variant
        · episode) cell 측 6 baseline 모두 존재 + chain step 측 1 축 차이.
        cell 측 정의 측 episode 포함 측 *각 episode 측 독립 ablation chain* 잠금.
    """
    # cell_key → BaselineMode → TrialSpec 측 매핑.
    cells: Dict[Tuple[str, str, str, int], Dict[BaselineMode, TrialSpec]] = (
        defaultdict(dict)
    )
    for trial in grid:
        variant_key = trial.fault_scenario.variant or 'none'
        cell_key = (
            trial.scenario_id,
            trial.fault_scenario.channel.value,
            variant_key,
            trial.episode_id,
        )
        mode = trial.baseline_config.mode
        if mode in cells[cell_key]:
            raise AssertionError(
                f'ablation cell {cell_key} 측 baseline {mode.value} 중복 — '
                f'동일 cell 측 동일 mode 측 2 trial 불가'
            )
        cells[cell_key][mode] = trial

    for cell_key, by_mode in cells.items():
        missing = [m for m in BaselineMode if m not in by_mode]
        if missing:
            raise AssertionError(
                f'ablation cell {cell_key} 측 baseline 누락 — '
                f'{[m.value for m in missing]} 부재. '
                f'cell 측 6 baseline (B0·B1a·B1b·B2·B3·B4) 모두 필요 — '
                f'paper §C 표 1 행 정합.'
            )

        for prev_mode, next_mode in ABLATION_CHAIN:
            prev_cfg = by_mode[prev_mode].baseline_config
            next_cfg = by_mode[next_mode].baseline_config
            diff = _axis_diff_count(prev_cfg, next_cfg)
            if diff != 1:
                raise AssertionError(
                    f'ablation chain {prev_mode.value} → {next_mode.value} 측 '
                    f'정확히 1 축 차이 위반 — cell {cell_key} 측 {diff} 축 차이. '
                    f'prev 3 축 = {_config_axes(prev_cfg)}, '
                    f'next 3 축 = {_config_axes(next_cfg)}. '
                    f'paper §C 표 1 측 단축 ablation 해석 측 의미 없음 — '
                    f'b{{N}}_config() helper 측 점검 필요.'
                )


def cell_count(grid: Sequence[TrialSpec]) -> int:
    """격자 측 ablation cell 수 — (scenario · fault_channel · fault_variant ·
    episode) distinct 4-tuple 수.

    각 cell 측 6 baseline 측 grid len ÷ 6 측 정확히 cell 수. ADR-0025 D3
    + amendment 19 default 격자 측 4 시나리오 × 5 fault_class × 10 episode = 200 cell.

    Args:
        grid: generate_trial_grid 측 출력.

    Returns:
        distinct cell 수.
    """
    cell_keys = {
        (
            trial.scenario_id,
            trial.fault_scenario.channel.value,
            trial.fault_scenario.variant or 'none',
            trial.episode_id,
        )
        for trial in grid
    }
    return len(cell_keys)
