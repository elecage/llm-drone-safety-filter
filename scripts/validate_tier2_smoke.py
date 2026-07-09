#!/usr/bin/env python3
"""A4-3 tier2_gate smoke validator — /tier2/gate/decision 로그를 파싱해 expected sequence 검증.

``ros2 topic echo /tier2/gate/decision`` 출력 (YAML, ``---`` 구분) 을 입력으로
받아 각 메시지의 ``data:`` 안 JSON 을 추출. 4 시나리오 발송 후 도착한 5
decision 의 (accept/confirm/reject + marker) sequence 가 expected 와 일치하는지
검증. PASS 시 exit 0, FAIL 시 exit 1.

사용:

    $ ros2 topic echo /tier2/gate/decision > /tmp/dec.log &
    $ python3 mock_tier2_intent.py --scenario accept
    $ ... (4 시나리오)
    $ python3 validate_tier2_smoke.py /tmp/dec.log
"""

from __future__ import annotations

import json
import re
import sys
from typing import List, Optional, Tuple


# 시나리오 순서: accept / reject_cc1 / reject_phi4 / confirm_phi10 (2 messages).
EXPECTED: List[Tuple[str, Optional[str]]] = [
    ('accept', None),
    ('reject', 'CC-1'),
    ('reject', 'Φ_4'),
    ('accept', None),         # confirm_phi10 #1 = move_to ACCEPT (σ_prev set)
    ('confirm', 'Φ_10'),      # confirm_phi10 #2 = return_to_dock C2 contradicts
]


def parse_decisions(log_path: str) -> List[dict]:
    """ros2 topic echo 출력 (YAML --- 구분) 에서 data: JSON 만 추출."""
    with open(log_path, encoding='utf-8') as f:
        text = f.read()
    decisions: List[dict] = []
    # data: '{"decision":...}' 패턴 — 따옴표는 '"' 또는 '\"' 가능.
    for chunk in text.split('---'):
        m = re.search(r"data:\s*['\"](\{.*?\})['\"]", chunk, re.DOTALL)
        if not m:
            continue
        raw = m.group(1).replace('\\"', '"')
        try:
            decisions.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return decisions


def validate(decisions: List[dict]) -> int:
    if len(decisions) != len(EXPECTED):
        print(
            f'FAIL: expected {len(EXPECTED)} decisions, got {len(decisions)}',
            file=sys.stderr,
        )
        for i, d in enumerate(decisions):
            print(f'  [{i}] {d}', file=sys.stderr)
        return 1

    ok = True
    for i, (exp_dec, exp_marker) in enumerate(EXPECTED):
        got = decisions[i]
        if got.get('decision') != exp_dec:
            print(
                f'FAIL #{i+1}: expected decision={exp_dec!r}, '
                f'got {got.get("decision")!r} (full: {got})',
                file=sys.stderr,
            )
            ok = False
            continue
        if exp_marker is not None:
            haystack = (
                str(got.get('reason', ''))
                + ' '
                + ','.join(got.get('violations', []))
            )
            if exp_marker not in haystack:
                print(
                    f'FAIL #{i+1}: expected marker {exp_marker!r} not in '
                    f'reason/violations (got: {haystack})',
                    file=sys.stderr,
                )
                ok = False
                continue
        print(f'PASS #{i+1}: decision={exp_dec} marker={exp_marker or "-"}')
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) != 2:
        print(f'usage: {sys.argv[0]} <decision_log_path>', file=sys.stderr)
        return 2
    decisions = parse_decisions(sys.argv[1])
    return validate(decisions)


if __name__ == '__main__':
    sys.exit(main())
