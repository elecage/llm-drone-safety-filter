#!/usr/bin/env python3
"""GitHub 인라인 수식 렌더 검사기 — `$...$`가 GitHub에서 깨지는지 *원격으로* 확정.

배경: paper 원고는 비공개 저장소라 GitHub 웹에서 렌더를 직접 못 본다. 2026-07 세션이
§5 밀집 인라인 수식이 깨진다는 지적을 3회 이상 받고도 "소스는 정상"이라며 원인을 못 잡음.
2026-07-08: GitHub `/markdown` API가 저장소 접근과 무관하게 임의 텍스트를 렌더하고,
수식으로 *인식된* `$...$`만 `<math-renderer>`로 감싼다는 사실로 원격 검증 경로를 확보.

원리: 한 줄을 `gh api -X POST /markdown -f mode=gfm --field text='<줄>'`로 렌더한 뒤,
`<math-renderer>...</math-renderer>`와 `<code>...</code>`를 제거하고도 literal `$`가
남으면 = GitHub이 그 `$...$`를 수식으로 인식 못 한 것 = 깨진 수식.

확정된 주 트리거(상세 CLAUDE.md §A2 "GitHub-특정 주의 사항"):
- `}_` (닫는 중괄호 바로 뒤 아래첨자) — `\dot{c}_{\max}` 등. 뒤따르는 `$..._...$` span이
  있으면 cascade. 해법 = 단일 토큰 base 중괄호 제거(`\dot c_{\max}`) 또는 집합 표기
  콜론형(`\{... : k=1,\ldots,K\}`).

사용:
  python3 scripts/check_github_math.py                 # paper/**/*.md 중 }_ 인라인 줄만(빠름)
  python3 scripts/check_github_math.py --all           # 모든 인라인 $ 줄(느림, 전수)
  python3 scripts/check_github_math.py --paths a.md b.md
발견 시 exit 1. `gh` 인증 필요(네트워크). CI(ROS-free host)엔 미포함 — 로컬/수동 게이트.
"""
from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys

MATH_RENDERER = re.compile(r"<math-renderer.*?</math-renderer>", re.S)
CODE = re.compile(r"<code.*?</code>", re.S)
# 빠른 경로 후보: 닫는 중괄호 뒤 아래첨자(`\cmd{..}_` 또는 escaped `\}_`)
BRACE_SUB = re.compile(r"\}_")
INLINE_DOLLAR = re.compile(r"\$")


def render(line: str) -> str:
    """GitHub /markdown API로 한 줄을 렌더해 HTML 반환."""
    out = subprocess.run(
        ["gh", "api", "-X", "POST", "/markdown", "-f", "mode=gfm", "--field", f"text={line}"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh api 실패: {out.stderr.strip()}")
    return out.stdout


def is_broken(html: str) -> bool:
    """math-renderer·code 제거 후 남은 literal $ = 깨진 인라인 수식."""
    stripped = CODE.sub("", MATH_RENDERER.sub("", html))
    return "$" in stripped


def iter_candidate_lines(path: str, scan_all: bool):
    """인라인 수식 후보 줄만 산출. `$$...$$` display 블록(구분자 줄·블록 내부)은 제외 —
    display math는 GitHub이 별도 파싱하며 본 검사기의 트리거(`}_` 등)에 영향받지 않고,
    단독 줄로 API에 보내면 delimiter `$$`가 literal 로 남아 거짓 양성이 된다."""
    in_display = False
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            stripped = line.strip()
            # 한 줄 전체가 $$ 구분자 → 블록 토글, 스킵
            if stripped == "$$":
                in_display = not in_display
                continue
            # 한 줄에 $$...$$ 가 통째로(단일 줄 display) → 스킵
            if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 2:
                continue
            if in_display:
                continue
            if "$" not in line:
                continue
            # 인라인 수식은 짝수 개의 $. 최소 2개(한 쌍) 있는 줄만.
            if line.count("$") < 2:
                continue
            if not scan_all and not BRACE_SUB.search(line):
                continue
            yield lineno, line


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paths", nargs="*", help="검사할 파일(미지정 시 paper/**/*.md)")
    ap.add_argument("--all", action="store_true", help="}_ 필터 없이 모든 인라인 $ 줄 검사(느림)")
    args = ap.parse_args()

    if args.paths:
        files = args.paths
    else:
        files = sorted(glob.glob("paper/**/*.md", recursive=True))

    broken: list[str] = []
    checked = 0
    for path in files:
        for lineno, line in iter_candidate_lines(path, args.all):
            checked += 1
            if is_broken(render(line)):
                broken.append(f"{path}:{lineno}")
                print(f"BROKEN {path}:{lineno}", file=sys.stderr)

    scope = "모든 인라인 $ 줄" if args.all else "}_ 포함 인라인 줄"
    print(f"검사 {checked}줄({scope}), 파일 {len(files)}개 — 깨짐 {len(broken)}건")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
