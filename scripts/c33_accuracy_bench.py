#!/usr/bin/env python3
"""C33 sub-task (b) — EdgeLLMWrapper 정확도 측정.

ADR-0014 D8 측 typed action 일치율 측정 스크립트.
Mac mini M4 Ollama 환경에서 실행.

## 측정 항목

- skill 일치율 (predicted_skill == expected_skill)
- ρ (self-consistency, M=3 내부 다수결 비율)
- H (정규화 엔트로피, 낮을수록 확신)
- 호출당 지연 (s)
- 스킬별 세분화 결과

## 사용법

    # 단일 모델 (Mac mini 에서)
    PYTHONPATH=intent/llm python3 scripts/c33_accuracy_bench.py \\
        --model gemma-4-e4b \\
        --utterances scripts/c33_bench_utterances.yaml \\
        --output results/c33_gemma4.yaml

    # 3 모델 전체 순차 실행
    PYTHONPATH=intent/llm python3 scripts/c33_accuracy_bench.py \\
        --all-models \\
        --utterances scripts/c33_bench_utterances.yaml \\
        --output-dir results/

## 환경변수

    OLLAMA_BASE_URL  — Ollama API URL (기본값 http://localhost:11434)
    TRIAL_LOG_DIR    — 설정 시 edge_llm JSONL 로그 저장

## 주의

    - 실 Ollama 연결 필요. Ollama 미기동 → ASK_USER fallback 집계 (Ollama 오류 아님).
    - 모델 미pull 시 Ollama 가 첫 호출에서 pull 시도 → 오래 걸릴 수 있음.
    - requests 패키지 필요: pip install requests pyyaml
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


# intent_llm 패키지 경로 — 루트에서 PYTHONPATH=intent/llm 설정 필요.
try:
    from intent_llm.edge_llm import EdgeLLMWrapper, _OLLAMA_MODEL_MAP
    from intent_llm.interface import (
        SIGNAL_SELF_CONSISTENCY,
        IntentInput,
    )
    from intent_llm.skill_catalog import SkillName
except ImportError as e:
    sys.exit(
        f'[c33_accuracy_bench] import 오류: {e}\n'
        'PYTHONPATH=intent/llm 설정 후 재실행:\n'
        '  PYTHONPATH=intent/llm python3 scripts/c33_accuracy_bench.py ...'
    )

# ADR-0014 D1 local 백본 3종 — EdgeLLMWrapper 식별자.
ALL_MODEL_IDS: List[str] = sorted(_OLLAMA_MODEL_MAP.keys())


# ----------------------------------------------------------------- 데이터 구조


@dataclass
class UtteranceResult:
    utterance: str
    expected_skill: str
    predicted_skill: str
    match: bool
    rho: float
    latency_s: float
    error: str = ''  # Ollama 오류 메시지 (있을 때만)


@dataclass
class ModelBenchResult:
    model_id: str
    n_total: int = 0
    n_correct: int = 0
    n_error: int = 0
    per_skill: Dict[str, Dict] = field(default_factory=dict)
    mean_rho: float = 0.0
    mean_latency_s: float = 0.0
    utterance_results: List[UtteranceResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.n_correct / self.n_total if self.n_total > 0 else 0.0


# ----------------------------------------------------------------- 유틸


def load_utterances(path: str) -> tuple[str, List[dict], List[str]]:
    """YAML 파일에서 scenario_id, utterances, known_objects 로드."""
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    scenario_id = data.get('scenario_id', 'S3')
    known_objects = data.get('known_objects', [])
    utterances = data.get('utterances', [])
    return scenario_id, utterances, known_objects


def _skill_name_from_str(s: str) -> Optional[SkillName]:
    try:
        return SkillName(s.lower().strip())
    except ValueError:
        return None


# ----------------------------------------------------------------- 측정 코어


def bench_model(
    model_id: str,
    scenario_id: str,
    utterances: List[dict],
    known_objects: List[str],
    verbose: bool = True,
) -> ModelBenchResult:
    """단일 EdgeLLMWrapper 모델 측 N 발화 정확도 측정."""
    wrapper = EdgeLLMWrapper(model_id)
    result = ModelBenchResult(model_id=model_id)

    # 스킬별 집계 초기화.
    for sk in SkillName:
        result.per_skill[sk.value] = {'correct': 0, 'total': 0}

    rho_list: List[float] = []
    latency_list: List[float] = []

    for idx, item in enumerate(utterances):
        utterance = item['utterance']
        expected_str = item.get('expected_skill', '')
        expected_skill = _skill_name_from_str(expected_str)

        context_graph: dict = {'known_objects': known_objects} if known_objects else {}

        intent_input = IntentInput(
            utterance=utterance,
            scenario_id=scenario_id,
            context_graph=context_graph or None,
        )

        t0 = time.monotonic()
        try:
            intent_result = wrapper.process(intent_input)
            latency_s = time.monotonic() - t0
            error_str = ''
        except Exception as exc:
            latency_s = time.monotonic() - t0
            error_str = str(exc)
            # process() 는 항상 IntentResult 반환 (interface.py 계약) 이므로
            # Exception 은 프로그래밍 오류. fallback ASK_USER 처리.
            import dataclasses
            from intent_llm.interface import IntentResult, TypedAction
            intent_result = IntentResult(
                typed_action=TypedAction(
                    skill=SkillName.ASK_USER,
                    args={'question': 'error'},
                ),
                confidence_raw=0.0,
                signals={},
            )

        predicted_skill = intent_result.typed_action.skill
        rho = intent_result.signals.get(SIGNAL_SELF_CONSISTENCY, 0.0)
        # s1(접지 엔트로피 H)은 OVD 노드 전용 (정본 §2.1) — wrapper signals 미포함.
        # skill 분포 H 진단은 wrapper trial log 참조.

        match = (expected_skill is not None) and (predicted_skill == expected_skill)

        ur = UtteranceResult(
            utterance=utterance,
            expected_skill=expected_str,
            predicted_skill=predicted_skill.value,
            match=match,
            rho=rho,
            latency_s=latency_s,
            error=error_str,
        )
        result.utterance_results.append(ur)

        result.n_total += 1
        if match:
            result.n_correct += 1
        if error_str:
            result.n_error += 1

        if expected_skill is not None and expected_skill.value in result.per_skill:
            result.per_skill[expected_skill.value]['total'] += 1
            if match:
                result.per_skill[expected_skill.value]['correct'] += 1

        rho_list.append(rho)
        latency_list.append(latency_s)

        if verbose:
            mark = '✓' if match else '✗'
            print(
                f'  [{idx+1:2d}/{len(utterances)}] {mark} '
                f'{predicted_skill.value:<20} (expected: {expected_str})'
                f'  ρ={rho:.2f} {latency_s:.1f}s'
            )

    if rho_list:
        result.mean_rho = sum(rho_list) / len(rho_list)
        result.mean_latency_s = sum(latency_list) / len(latency_list)

    # 스킬별 accuracy 계산.
    for sk_val, counts in result.per_skill.items():
        t = counts['total']
        c = counts['correct']
        counts['accuracy'] = c / t if t > 0 else None

    return result


# ----------------------------------------------------------------- 출력


def _to_yaml_dict(r: ModelBenchResult) -> dict:
    return {
        'model': r.model_id,
        'n_total': r.n_total,
        'n_correct': r.n_correct,
        'n_error': r.n_error,
        'accuracy': round(r.accuracy, 4),
        'mean_rho': round(r.mean_rho, 4),
        'mean_latency_s': round(r.mean_latency_s, 3),
        'per_skill': {
            sk: {
                'correct': v['correct'],
                'total': v['total'],
                'accuracy': round(v['accuracy'], 4) if v['accuracy'] is not None else None,
            }
            for sk, v in r.per_skill.items()
        },
        'utterances': [
            {
                'utterance': u.utterance,
                'expected': u.expected_skill,
                'predicted': u.predicted_skill,
                'match': u.match,
                'rho': round(u.rho, 4),
                'latency_s': round(u.latency_s, 3),
                **(({'error': u.error}) if u.error else {}),
            }
            for u in r.utterance_results
        ],
    }


def print_summary(r: ModelBenchResult) -> None:
    print(f'\n{"─"*55}')
    print(f' 모델: {r.model_id}')
    print(f' 정확도: {r.n_correct}/{r.n_total} = {r.accuracy:.1%}')
    print(f' ρ 평균: {r.mean_rho:.3f}  지연: {r.mean_latency_s:.1f}s/회')
    print(' 스킬별:')
    for sk, v in r.per_skill.items():
        if v['total'] > 0:
            acc = v['accuracy']
            acc_str = f'{acc:.1%}' if acc is not None else 'N/A'
            print(f'   {sk:<22} {v["correct"]}/{v["total"]}  ({acc_str})')
    if r.n_error > 0:
        print(f' ⚠️  오류 발화 수: {r.n_error} (Ollama 연결 오류 또는 타임아웃)')
    print(f'{"─"*55}')


# ----------------------------------------------------------------- 메인


def main() -> None:
    parser = argparse.ArgumentParser(
        description='C33 sub-task (b) — EdgeLLMWrapper 정확도 측정'
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--model',
        choices=ALL_MODEL_IDS,
        help=f'단일 모델 식별자 ({", ".join(ALL_MODEL_IDS)})',
    )
    group.add_argument(
        '--all-models',
        action='store_true',
        help='3 모델 전체 순차 실행',
    )
    parser.add_argument(
        '--utterances',
        default='scripts/c33_bench_utterances.yaml',
        help='발화 YAML 파일 경로 (기본값: scripts/c33_bench_utterances.yaml)',
    )
    parser.add_argument(
        '--output',
        help='단일 모델 결과 저장 YAML 경로 (--model 사용 시)',
    )
    parser.add_argument(
        '--output-dir',
        default='results',
        help='--all-models 사용 시 결과 저장 디렉토리 (기본값: results/)',
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='발화별 진행 출력 억제',
    )
    args = parser.parse_args()

    utterances_path = args.utterances
    if not Path(utterances_path).exists():
        sys.exit(f'[c33_accuracy_bench] 발화 파일 없음: {utterances_path}')

    scenario_id, utterances, known_objects = load_utterances(utterances_path)

    model_ids = ALL_MODEL_IDS if args.all_models else [args.model]

    all_results: List[ModelBenchResult] = []

    for model_id in model_ids:
        print(f'\n[c33-bench] 모델: {model_id} ({len(utterances)} 발화)')
        result = bench_model(
            model_id,
            scenario_id,
            utterances,
            known_objects,
            verbose=not args.quiet,
        )
        all_results.append(result)
        print_summary(result)

        # 결과 저장.
        if args.all_models:
            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f'c33_{model_id.replace("-", "_")}.yaml'
        elif args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_path = None

        if out_path:
            with open(out_path, 'w', encoding='utf-8') as f:
                yaml.dump(
                    _to_yaml_dict(result),
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
            print(f'[c33-bench] 결과 저장: {out_path}')

    # --all-models 비교 요약.
    if args.all_models and len(all_results) > 1:
        print(f'\n{"═"*55}')
        print(' [C33 전체 비교 요약]')
        print(f' {"모델":<28} {"정확도":>7}  {"ρ":>6}  {"지연":>8}')
        print(f' {"─"*28} {"─"*7}  {"─"*6}  {"─"*8}')
        for r in all_results:
            print(
                f' {r.model_id:<28} {r.accuracy:>6.1%}  '
                f'{r.mean_rho:>6.3f}  {r.mean_latency_s:>7.1f}s'
            )
        print(f'{"═"*55}')


if __name__ == '__main__':
    main()
