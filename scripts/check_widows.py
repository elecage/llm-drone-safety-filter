#!/usr/bin/env python3
"""paper/latex/main.pdf 의 문단 끝 widow(조각·초단문 끝줄) 스캔.

2026-07-10 세션에서 인라인으로 5회+ 반복 실행한 검사를 스크립트화
([[repeated-commands-as-scripts-not-prose]]). 2단 IJCAS 조판을 pdftotext
-layout 으로 뽑아 좌/우 컬럼 절반씩 검사한다.

검출 기준:
  - FRAG: 직전 줄이 하이픈으로 끝나고(단어가 끊김) 마지막 줄이 짧음
          (예: "sible." · "tions.") — 반드시 제거 대상.
  - SHORT: 하이픈 없이도 아주 짧은(≤8자) 단독 끝줄 — 검토 대상.

알려진 비대상(수동 확인 후 존치, 2026-07-10): 표/도면 축 라벨 아티팩트('ts.'
'Time [s]'), 참고문헌 항목 끝줄, Springer 판권 고정 문구('iations.'),
CRediT 선언부. 이들은 출력에 나와도 무시.

처방(같은 세션 교훈): 어순 재배열/1단어 절삭이 1순위. 조판 처방은
비분리 공백 `~`(공백 분리만 방지 — 단어 *내부* 하이픈은 못 막음) 또는
`\\mbox{두 단어}`(하이픈까지 방지).

실행: python3 scripts/check_widows.py [main.pdf 경로]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

COL_SPLIT = 62          # pdftotext -layout 2단 출력의 좌/우 경계(문자 열)
MAX_LEN = 14            # 이 길이 이하의 끝줄만 후보
SHORT_LEN = 8           # 비-FRAG 단독 끝줄 보고 임계
SKIP = re.compile(r'^(Fig\.|Table|\(\w\)|\[?\d+[\],.]|[A-Z]\d)')


def main() -> int:
    pdf = Path(sys.argv[1] if len(sys.argv) > 1 else 'paper/latex/main.pdf')
    if not pdf.is_file():
        print(f'{pdf} 없음 — 먼저 latexmk 컴파일')
        return 1
    out = subprocess.run(['pdftotext', '-layout', str(pdf), '-'],
                         capture_output=True, text=True).stdout
    n = 0
    for pno, page in enumerate(out.split('\f'), 1):
        lines = page.split('\n')
        for i, ln in enumerate(lines):
            for side, half in (('L', ln[:COL_SPLIT]), ('R', ln[COL_SPLIT:])):
                s = half.strip()
                if not s or len(s) > MAX_LEN:
                    continue
                if not re.search(r'[.)\]]$', s) or re.match(r'^\d+$', s):
                    continue
                if SKIP.match(s):
                    continue
                prev = (lines[i - 1][:COL_SPLIT] if side == 'L'
                        else lines[i - 1][COL_SPLIT:]).strip()
                frag = prev.endswith('-')
                if not frag and len(s) > SHORT_LEN:
                    continue
                n += 1
                kind = 'FRAG ' if frag else 'SHORT'
                print(f"p{pno:2d} {side} {kind} '{s}'  prev: ...{prev[-32:]}")
    print(f'{n} candidate(s) — 비대상 목록(docstring)은 무시')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
