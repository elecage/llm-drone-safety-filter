"""Calibration sampling loop — 한 (백본, 시나리오) 의 N=50 측정.

CLI:
    # (기본) user_prompt 거동 분포 → SigmaLlmNat
    python -m eval_calibration.measure --backbone gpt-4o --scenario S5 --n 50
    python -m eval_calibration.measure --backbone gpt-5.5 --scenario S5 --n 50 \\
        --mode natural
    # (--probe) move_to_probes 두 조건(provided/absent) positional σ 축별 대조
    python -m eval_calibration.measure --backbone gpt-4o --scenario S5 --probe --n 10

실 OpenAI API 호출이라 OPENAI_API_KEY 필수. 결과 YAML → results/.
`--probe` 는 ProbeCalibrationResult, 기본 모드는 CalibrationResult 로 저장
(ADR-0025 amend 12/13/14).

**PR #82 review C1·C3 amendment**:
- 기본 mode = NATURAL (자연 거동 측정 — fail-gracefully 포함)
- LLM 이 function call 회피 시 (action=None) `is_no_call=True` flag 별 보고
- `is_unrelated` 의미 보수화 — expected_action 이 *명시* 된 시나리오에서만
  의미 있음. ambiguous (expected_action.sigma == 'ask_user') 시나리오는
  *모든 응답이 graceful* 가능성 인정 → NaN.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List

import yaml

from eval_calibration.analyze import (
    compute_axis_sigma,
    compute_sigma_llm_nat,
    position_delta_cm,
)
from eval_calibration.llm_client import LlmResponse, PromptMode, call_llm
from eval_calibration.prompts import discover_scenarios
from eval_calibration.schemas import (
    Backbone,
    CalibrationResult,
    ProbeCalibrationResult,
    ProbeConditionMeasurement,
    ProbeMeasurement,
    SampleOutput,
    ScenarioSpec,
    TypedAction,
)


_AMBIGUOUS_EXPECTED_SIGMAS = {'ask_user'}  # 모호 referent 시나리오의 graceful 표시


def _sample_to_output(
    prompt: str,
    response: LlmResponse,
    scenario: ScenarioSpec,
) -> SampleOutput:
    """LlmResponse + ScenarioSpec → SampleOutput.

    PR #82 review C1·C3 amendment:
    - response.action is None → function call 회피, is_no_call=True
    - is_unrelated 는 *expected_action 이 ask_user 가 아닌* 시나리오만 측정
      (ambiguous 시나리오는 graceful inference 가능성 인정)
    """
    actual = response.action
    expected_action = scenario.expected_action

    is_no_call = actual is None

    # position_xyz_cm — actual 이 move_to 이고 expected_position 명시 시
    actual_position = None
    if actual is not None and actual.sigma == 'move_to' and 'position' in actual.theta:
        ap = actual.theta['position']
        if isinstance(ap, (list, tuple)) and len(ap) == 3:
            try:
                actual_position = tuple(float(v) for v in ap)
            except (TypeError, ValueError):
                actual_position = None  # LLM theta type 이상 (C8 측 robust)
    position_cm = position_delta_cm(actual_position, scenario.expected_position)

    # is_swap — actual 이 inspect 이고 expected_target_id 명시 시
    actual_target = (
        actual.theta.get('target_id')
        if actual is not None and actual.sigma == 'inspect'
        else None
    )
    expected_target = scenario.expected_target_id
    is_swap = bool(
        expected_target is not None
        and actual_target is not None
        and actual_target != expected_target
    )

    # is_unrelated — C3 amendment: ambiguous (expected=ask_user) 시나리오는 NaN.
    # expected 가 *명확한 sigma* (move_to/inspect/return_to_dock/...) 일 때만 측정.
    if (
        expected_action is None
        or expected_action.sigma in _AMBIGUOUS_EXPECTED_SIGMAS
        or actual is None
    ):
        is_unrelated = None  # NaN-like (ambiguous 시나리오)
    else:
        is_unrelated = bool(actual.sigma != expected_action.sigma)

    deltas = {
        'position_xyz_cm': position_cm,
        'is_swap': is_swap,
        'is_unrelated': is_unrelated,
        'is_no_call': is_no_call,
    }

    # action=None 일 때 SampleOutput.sigma 는 placeholder ('no_call' 같은 식별자
    # 는 catalog 위반이라 TypedAction 거부 → 직접 ask_user/empty 사용)
    if actual is None:
        sigma_for_output = TypedAction(sigma='ask_user', theta={'question': '(no function call)'})
    else:
        sigma_for_output = actual

    return SampleOutput(
        prompt=prompt,
        sigma=sigma_for_output,
        expected_action=expected_action,
        deltas=deltas,
    )


def run_calibration(
    backbone: Backbone,
    scenario: ScenarioSpec,
    n_samples: int,
    temperature: float = 0.7,
    mode: PromptMode = PromptMode.NATURAL,
    client_factory=None,
    verbose: bool = True,
    context_provided: bool = False,
) -> CalibrationResult:
    """N=50 sampling 실행.

    Args:
        backbone: GPT_4O 또는 GPT_5_5
        scenario: ScenarioSpec
        n_samples: 50 (ADR-0025 D1.b 잠금)
        temperature: 0.7 (ADR-0025 D1.b 잠금)
        mode: PromptMode.NATURAL (calibration default) 또는 STRICT (paper §C 본실험)
        client_factory: 테스트용 mock injection
        verbose: 진행 로그 출력
        context_provided: ADR-0025 amend 12 (D1.e) — True 면 known_object_positions
            좌표를 LLM 에 제공(본실험 fusion mode 정합, σ≈0 예상). False 면 이름만
            (context-absent 대조, LLM 좌표 추측 σ 측정). scenario 에 좌표 없으면 무효.

    Returns:
        CalibrationResult — paper §C 부록 보고 YAML 직렬화 대상
    """
    obj_positions = scenario.known_object_positions if context_provided else None
    if context_provided and not obj_positions:
        raise ValueError(
            f'context_provided=True 인데 scenario {scenario.scenario_id} 에 '
            f'known_object_positions 없음 — YAML 에 좌표 추가 필요 (ADR-0025 amend 12)'
        )
    samples: List[SampleOutput] = []
    failures: List[str] = []
    for i in range(n_samples):
        try:
            response = call_llm(
                backbone=backbone,
                scenario_prompt=scenario.user_prompt,
                known_objects=scenario.known_objects,
                temperature=temperature,
                client_factory=client_factory,
                mode=mode,
                object_positions=obj_positions,
            )
            sample = _sample_to_output(scenario.user_prompt, response, scenario)
            samples.append(sample)
            if verbose:
                no_call = sample.deltas['is_no_call']
                sigma_str = '(no call)' if no_call else sample.sigma.sigma
                pos_str = (
                    f'Δ pos {sample.deltas["position_xyz_cm"]:.1f}cm'
                    if sample.deltas['position_xyz_cm'] == sample.deltas['position_xyz_cm']  # not NaN
                    else 'pos N/A'
                )
                print(
                    f'  [{i+1}/{n_samples}] {sigma_str} ({pos_str},'
                    f' swap={sample.deltas["is_swap"]},'
                    f' unrelated={sample.deltas["is_unrelated"]},'
                    f' no_call={no_call})'
                )
        except Exception as e:  # C7 fix — RateLimitError 등 모두 잡음
            failures.append(f'{type(e).__name__}: {e}')
            if verbose:
                print(
                    f'  [{i+1}/{n_samples}] FAIL — {type(e).__name__}: {e}',
                    file=sys.stderr,
                )

    sigma_llm_nat = compute_sigma_llm_nat(samples)

    if verbose and failures:
        print(
            f'\n  [요약] {len(failures)}/{n_samples} 호출 실패 — paper §C 부록 보고',
            file=sys.stderr,
        )

    return CalibrationResult(
        backbone=backbone.value,
        scenario=scenario.scenario_id,
        n_samples=n_samples,
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        sigma_llm_nat=sigma_llm_nat,
        samples=samples,
    )


def save_result(result: CalibrationResult, output_dir: Path) -> Path:
    """YAML 저장 → results/{backbone}_{scenario}_n{N}_{ts}.yaml."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = result.timestamp.replace(':', '').replace('-', '')[:15]
    backbone_safe = result.backbone.replace('-', '_').replace('.', '_')
    path = output_dir / f'{backbone_safe}_{result.scenario}_n{result.n_samples}_{ts_compact}.yaml'
    with path.open('w', encoding='utf-8') as f:
        yaml.safe_dump(result.to_dict(), f, allow_unicode=True, sort_keys=False)
    return path


# ─── probe 기반 positional σ 두 조건 측정 (ADR-0025 amend 12/13) ──────────────
# 시나리오 정상 user_prompt 가 inspect/ask_user 를 유발할 때 positional σ_LLM,nat
# 측정이 불가하므로 (S6 "보여줘"→inspect), move_to-natural probe 발화로 측정한다.
# context-provided / context-absent 두 조건을 자동 대조 → 기둥①(context
# augmentation)이 referent 좌표 환각을 제거하는지 축별로 본다.


def _collect_probe_moves(
    backbone: Backbone,
    prompt: str,
    known_objects: List[str],
    object_positions,
    n_samples: int,
    temperature: float,
    mode: PromptMode,
    client_factory,
):
    """N 회 호출 → (move_to position 리스트, skill 분포 dict).

    LLM theta 의 position type 이상은 skip (C8 robust). move_to 외 sigma 와
    function-call 회피('(no_call)') 는 skill_distribution 에만 집계.

    주의 — skill_distribution 은 *LLM 거동 분포* (sigma 별 count, 합=n_samples),
    moves 는 *σ 유효 표본* (position 이 3-tuple 로 파싱된 move_to 만). move_to 를
    냈으나 position type 이 깨지면 skill 엔 집계되나 moves 엔 제외되므로
    `len(moves) ≤ skill_distribution.get('move_to', 0)` 가 성립할 수 있다 (즉
    ProbeConditionMeasurement.n_move_to 는 move_to 출력 수가 아니라 σ 표본 수).
    """
    moves: List[tuple] = []
    skills: dict = {}
    for _ in range(n_samples):
        r = call_llm(
            backbone,
            prompt,
            known_objects,
            temperature=temperature,
            mode=mode,
            client_factory=client_factory,
            object_positions=object_positions,
        )
        key = r.action.sigma if r.action else '(no_call)'
        skills[key] = skills.get(key, 0) + 1
        if r.action and r.action.sigma == 'move_to':
            p = r.action.theta.get('position')
            if isinstance(p, (list, tuple)) and len(p) == 3:
                try:
                    moves.append(tuple(float(v) for v in p))
                except (TypeError, ValueError):
                    pass  # LLM theta type 이상 — σ 표본에서 제외
    return moves, skills


def _measure_probe_condition(
    backbone: Backbone,
    prompt: str,
    scenario: ScenarioSpec,
    context_provided: bool,
    n_samples: int,
    temperature: float,
    mode: PromptMode,
    client_factory,
) -> ProbeConditionMeasurement:
    """단일 (probe, 조건) 측정 → ProbeConditionMeasurement."""
    object_positions = scenario.known_object_positions if context_provided else None
    moves, skills = _collect_probe_moves(
        backbone,
        prompt,
        scenario.known_objects,
        object_positions,
        n_samples,
        temperature,
        mode,
        client_factory,
    )
    axis = compute_axis_sigma(moves)
    return ProbeConditionMeasurement(
        context_provided=context_provided,
        n_samples=n_samples,
        n_move_to=len(moves),
        skill_distribution=skills,
        axis_sigma_cm={ax: axis[ax]['sigma_cm'] for ax in 'xyz'},
        axis_mean_m={ax: axis[ax]['mean_m'] for ax in 'xyz'},
    )


def _report_probe_condition(
    label: str, cond: ProbeConditionMeasurement, expected_xy
) -> None:
    """두 조건 per-axis σ 콘솔 보고 (구 verify_context_effect.py 형식)."""
    print(
        f'--- {label}: move_to {cond.n_move_to}/{cond.n_samples}, '
        f'skills={cond.skill_distribution}'
    )
    if cond.n_move_to >= 2:
        for i, ax in enumerate('xyz'):
            exp = ''
            if expected_xy is not None and i < 2:
                exp = f'  (referent {"xy"[i]}={expected_xy[i]:.2f})'
            print(
                f'    {ax}: σ={cond.axis_sigma_cm[ax]:6.1f}cm  '
                f'mean={cond.axis_mean_m[ax]:.2f}{exp}'
            )
    elif cond.n_move_to == 1:
        means = tuple(cond.axis_mean_m[ax] for ax in 'xyz')
        print(f'    유일 move_to: {means}')


def run_probe_calibration(
    backbone: Backbone,
    scenario: ScenarioSpec,
    n_samples: int,
    temperature: float = 0.7,
    mode: PromptMode = PromptMode.NATURAL,
    client_factory=None,
    verbose: bool = True,
) -> ProbeCalibrationResult:
    """시나리오의 move_to_probes 를 두 조건(provided/absent)으로 측정.

    Args:
        backbone: GPT_4O 또는 GPT_5_5
        scenario: move_to_probes + known_object_positions 보유 ScenarioSpec
        n_samples: 조건당 호출 수
        temperature: 0.7 (ADR-0025 D1.b 잠금)
        mode: PromptMode.NATURAL (default)
        client_factory: 테스트용 mock injection
        verbose: 두 조건 per-axis σ 콘솔 보고

    Returns:
        ProbeCalibrationResult — paper §C 부록 보고 YAML 직렬화 대상

    Raises:
        ValueError: move_to_probes 또는 known_object_positions 부재
    """
    if not scenario.move_to_probes:
        raise ValueError(
            f'scenario {scenario.scenario_id} 에 move_to_probes 없음 — '
            f'positional σ probe 측정 불가 (ADR-0025 amend 13)'
        )
    if not scenario.known_object_positions:
        raise ValueError(
            f'scenario {scenario.scenario_id} 에 known_object_positions 없음 — '
            f'context-provided 조건 불가 (ADR-0025 amend 12)'
        )

    probes: List[ProbeMeasurement] = []
    for probe in scenario.move_to_probes:
        prompt = probe['prompt']
        expected_object = probe['expected_object']
        expected_pos = scenario.known_object_positions.get(expected_object)
        expected_xy = (
            (expected_pos[0], expected_pos[1]) if expected_pos is not None else None
        )
        if verbose:
            print(f'\n발화: "{prompt}"')
        conds = {}
        for label, ctx in [('context-provided', True), ('context-absent', False)]:
            cond = _measure_probe_condition(
                backbone, prompt, scenario, ctx, n_samples,
                temperature, mode, client_factory,
            )
            conds[ctx] = cond
            if verbose:
                _report_probe_condition(label, cond, expected_xy)
        probes.append(
            ProbeMeasurement(
                prompt=prompt,
                expected_object=expected_object,
                expected_xy=expected_xy,
                provided=conds[True],
                absent=conds[False],
            )
        )

    return ProbeCalibrationResult(
        backbone=backbone.value,
        scenario=scenario.scenario_id,
        n_samples=n_samples,
        timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        probes=probes,
    )


def save_probe_result(result: ProbeCalibrationResult, output_dir: Path) -> Path:
    """YAML 저장 → results/{backbone}_{scenario}_probe_n{N}_{ts}.yaml."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts_compact = result.timestamp.replace(':', '').replace('-', '')[:15]
    backbone_safe = result.backbone.replace('-', '_').replace('.', '_')
    path = (
        output_dir
        / f'{backbone_safe}_{result.scenario}_probe_n{result.n_samples}_{ts_compact}.yaml'
    )
    with path.open('w', encoding='utf-8') as f:
        yaml.safe_dump(result.to_dict(), f, allow_unicode=True, sort_keys=False)
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='paper §C calibration sampling loop (ADR-0025 D1.b)'
    )
    parser.add_argument(
        '--backbone',
        type=str,
        required=True,
        choices=['gpt-4o', 'gpt-5.5'],
        help='ADR-0025 D1.b amendment 8 대상 백본',
    )
    parser.add_argument(
        '--scenario',
        type=str,
        required=True,
        help='시나리오 ID (S5, S6, S7, S8)',
    )
    parser.add_argument(
        '--n',
        type=int,
        default=50,
        help='샘플 수 (ADR-0025 D1.b 잠금 기본 = 50)',
    )
    parser.add_argument(
        '--mode',
        type=str,
        choices=['natural', 'strict'],
        default='natural',
        help='PromptMode — natural (자연 거동 측정, default) / strict (paper §C 본실험)',
    )
    parser.add_argument(
        '--scenarios-dir',
        type=Path,
        default=Path(__file__).resolve().parent.parent / 'scenarios',
        help='시나리오 YAML 디렉터리',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(__file__).resolve().parent.parent / 'results',
        help='결과 YAML 저장 디렉터리',
    )
    parser.add_argument('--temperature', type=float, default=0.7)
    parser.add_argument(
        '--context-provided',
        action='store_true',
        help='ADR-0025 amend 12 — known_object_positions 좌표를 LLM 에 제공 '
             '(본실험 fusion 정합, σ≈0 예상). 미지정 시 이름만(대조). '
             '--probe 와 함께 쓰면 무시(probe 는 두 조건 자동).',
    )
    parser.add_argument(
        '--probe',
        action='store_true',
        help='ADR-0025 amend 12/13 — move_to_probes 발화를 context-provided / '
             'context-absent 두 조건으로 측정 (positional σ 축별 대조). '
             'user_prompt 거동 분포(기본 모드) 대신 positional σ 측정.',
    )
    return parser


def _resolve_backbone(name: str) -> Backbone:
    mapping = {'gpt-4o': Backbone.GPT_4O, 'gpt-5.5': Backbone.GPT_5_5}
    if name not in mapping:
        raise ValueError(f'알 수 없는 backbone "{name}" — choices: {list(mapping)}')
    return mapping[name]


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    backbone = _resolve_backbone(args.backbone)
    mode = PromptMode(args.mode)
    scenarios = discover_scenarios(args.scenarios_dir)
    if args.scenario not in scenarios:
        print(
            f'ERROR: 시나리오 "{args.scenario}" 없음 — available: {sorted(scenarios)}',
            file=sys.stderr,
        )
        return 2

    if args.probe:
        if args.context_provided:
            print(
                '[warn] --probe 는 두 조건 자동 측정 — --context-provided 무시',
                file=sys.stderr,
            )
        print(
            f'[calibration] backbone={backbone.value} scenario={args.scenario}'
            f' N={args.n} T={args.temperature} mode={mode.value} probe (두 조건)'
        )
        try:
            probe_result = run_probe_calibration(
                backbone=backbone,
                scenario=scenarios[args.scenario],
                n_samples=args.n,
                temperature=args.temperature,
                mode=mode,
            )
        except ValueError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            return 2
        probe_path = save_probe_result(probe_result, args.output_dir)
        print(f'\n[calibration] probe result saved → {probe_path}')
        return 0

    ctx_str = 'context-provided' if args.context_provided else 'context-absent'
    print(
        f'[calibration] backbone={backbone.value} scenario={args.scenario}'
        f' N={args.n} T={args.temperature} mode={mode.value} {ctx_str}'
    )
    result = run_calibration(
        backbone=backbone,
        scenario=scenarios[args.scenario],
        n_samples=args.n,
        temperature=args.temperature,
        mode=mode,
        context_provided=args.context_provided,
    )
    path = save_result(result, args.output_dir)
    print(f'\n[calibration] result saved → {path}')
    print(
        f'  sigma_llm_nat = position {result.sigma_llm_nat.position_xyz_cm:.2f} cm,'
        f' swap {result.sigma_llm_nat.target_swap_rate * 100:.1f}%,'
        f' unrelated {result.sigma_llm_nat.unrelated_sigma_rate * 100:.1f}%,'
        f' no_call {result.sigma_llm_nat.no_call_rate * 100:.1f}%'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
