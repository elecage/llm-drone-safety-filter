#!/usr/bin/env python3
"""C37(b) 레벨 1 검증 — 실 LLM 의도 좌표 그라운딩 (sim 불필요).

실 코드 경로 그대로: build_context_graph → 의도해석기 wrapper.process (실 LLM).
context_graph(객체+좌표)를 주입했을 때 LLM이 ADR-0027 스키마
(move_to.position=[x,y,z], inspect.target_id)를 올바르게 산출하는지 검증.

## 실행 (edge LLM = Mac mini gemma Q4, SSH 터널 경유)

Mac mini Ollama 는 127.0.0.1 바인딩이므로 LAN 직접 접근 불가 → SSH 터널:

    ssh -f -N -L 11500:localhost:11434 aiot@192.168.0.51
    OLLAMA_BASE_URL=http://localhost:11500 \\
      PYTHONPATH=intent/llm:intent/context:sim/scenario_params \\
      python3 scripts/c37_grounding_verify.py --backbone gemma-4-e4b --scenario S5

## 실행 (cloud LLM)

    OPENAI_API_KEY=sk-... \\
      PYTHONPATH=intent/llm:intent/context:sim/scenario_params \\
      python3 scripts/c37_grounding_verify.py --backbone gpt-4o --scenario S5

종료 코드: 모든 케이스 PASS = 0, 하나라도 FAIL = 1.
"""
from __future__ import annotations

import argparse
import math
import sys

from intent_context.context_graph import build_context_graph
from intent_llm.interface import IntentInput
from intent_llm.registry import get_wrapper
from intent_llm.skill_catalog import SkillName

# (발화, 기대 skill, 기대 객체 이름) — 객체 이름 None = 좌표/ID 검증 없음.
_CASES_LIVINGROOM = [
    ('저 TV 쪽으로 가줘',        SkillName.MOVE_TO,        'tv'),
    ('소파로 이동해줘',          SkillName.MOVE_TO,        'sofa'),
    ('식탁 쪽으로 가줘',         SkillName.MOVE_TO,        'dining_table'),
    ('TV 좀 자세히 살펴봐줘',    SkillName.INSPECT,        'tv'),
    ('충전대로 돌아가',          SkillName.RETURN_TO_DOCK, None),
    ('어... 저기 그거 좀',       SkillName.ASK_USER,       None),
]

_GROUNDING_TOL_M = 0.5  # move_to.position 이 기대 객체 좌표와 이 거리 이내면 정합


def _dist(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def main() -> int:
    ap = argparse.ArgumentParser(description='C37(b) 의도 좌표 그라운딩 검증')
    ap.add_argument('--backbone', default='gemma-4-e4b',
                    help='registry 식별자 (예: gemma-4-e4b, gpt-4o)')
    ap.add_argument('--scenario', default='S5', help="scenario_id (S5-S8)")
    args = ap.parse_args()

    graph = build_context_graph(args.scenario)
    obj_pos = {o['name']: o['position'] for o in graph['objects']}
    wrapper = get_wrapper(args.backbone)
    cases = _CASES_LIVINGROOM  # 현재 livingroom(S5-S7) 케이스만 정의

    print(f'=== C37(b) 검증 — backbone={args.backbone} scenario={args.scenario} '
          f'({graph["location"]}) ===')
    print(f'objects: {list(obj_pos)}\n')

    n_pass = 0
    for utt, exp_skill, exp_obj in cases:
        r = wrapper.process(IntentInput(utterance=utt, scenario_id=args.scenario,
                                        context_graph=graph))
        ta = r.typed_action
        skill_ok = ta.skill == exp_skill
        grounding_ok = True
        detail = ''

        if ta.skill == SkillName.MOVE_TO:
            pos = ta.args.get('position')
            if exp_obj and pos:
                d = _dist(pos, obj_pos[exp_obj])
                grounding_ok = d < _GROUNDING_TOL_M
                detail = f'position={pos} vs {exp_obj}{obj_pos[exp_obj]} d={d:.2f}m'
            else:
                grounding_ok = pos is not None
                detail = f'position={pos}'
        elif ta.skill == SkillName.INSPECT:
            tid = ta.args.get('target_id')
            if exp_obj:
                grounding_ok = (tid == exp_obj) or (exp_obj in str(tid))
            detail = f'target_id={tid!r}'
        else:
            detail = f'args={dict(ta.args)}'

        ok = skill_ok and grounding_ok
        n_pass += ok
        mark = 'PASS' if ok else 'FAIL'
        rho = r.signals.get('s2_self_consistency')
        print(f'[{mark}] "{utt}"')
        print(f'       skill={ta.skill.value} (exp {exp_skill.value}) '
              f'c={r.confidence_raw:.2f} rho={rho}')
        print(f'       {detail}\n')

    print(f'=== {n_pass}/{len(cases)} PASS ===')
    return 0 if n_pass == len(cases) else 1


if __name__ == '__main__':
    sys.exit(main())
