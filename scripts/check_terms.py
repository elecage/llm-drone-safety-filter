#!/usr/bin/env python3
"""폐기 용어 검사기 — CLAUDE.md 용어 규율을 *기계적으로* 강제.

배경: 폐기 용어(표준격자·접지·처분·과보수성 등)를 "기억해서 안 쓰기"가 반복 실패 →
메모 대신 *검사*로 전환(연구자 결정 2026-06-29). paper/ 활성 원고를 스캔해 폐기 용어를
적발하고, 발견 시 exit 1 (CI 게이트). 매핑은 본 파일 DEPRECATED 가 단일 소스 —
CLAUDE.md 는 이 파일을 가리킨다.

설계 원칙(거짓 양성 방지 = 검사기가 무시당하지 않게):
- **고정밀 패턴만** — 거의 항상 틀린 표현만 잡는다(예 "표준 격자" O, 맨 "표준" X —
  "표준 CBF" 는 정당).
- **allow**: 같은 용어의 정당한 문맥(예 "전기 접지", 코드 식별자 백틱)은 제외.
- 새 폐기 용어는 DEPRECATED 에 한 줄 추가(+CLAUDE.md 매핑) — 그게 전부.

사용: `python3 scripts/check_terms.py`  (리포 루트). CI(tests.yml)·로컬 둘 다.
범위: 기본 paper/**/*.md (정본 원고). --paths 로 추가 가능.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# (정규식, 권장 대체, [allow 정규식들]) — paper 평문에서 거의 항상 틀린 표현만.
# allow 정규식에 매칭되는 *줄* 은 면제(정당 문맥/코드 식별자).
DEPRECATED: list[tuple[str, str, list[str]]] = [
    (r"표준\s*격자",         "통합 스택 격자 (ADR-0039 폐기)", []),
    (r"처분",                "게이트 결과는 동사로 풀거나 '검증' (CLAUDE.md)", []),
    (r"과보수성",            "평균 회피 반경 \\bar r (단위 불일치로 폐기)", []),
    (r"보수\s*측",           "보수적 / 보수성 (비표준 '측' 표기 폐기)", []),
    (r"조이|조인다|조여",      "변조 방향은 '보수성을 높이는' — '조이다' 구어 폐기 (CLAUDE.md)", []),
    (r"그라운딩",            "지시 대상 결정 / 의미 식별", []),
    (r"접지\s*엔트로피",      "지시 후보 엔트로피", []),
    # '접지' — 전기 접지/대지 접지/접지선 등 전기 문맥은 면제, 그 외(grounding 의미)는 금지.
    (r"접지",                "지시 대상 결정 / 의미 식별 (grounding 평문 금지)",
        [r"전기\s*접지", r"대지\s*접지", r"접지선", r"접지\s*저항", r"earthing"]),
    (r"불가지",              "무관 (intent-agnostic = '*의도해석기*와 무관')", []),
    (r"타입드\s*목표",        "행동 집합 (비표준 용어 폐기)", []),
    (r"no-fly\s*버블|버블",   "사용자 회피 영역 / 안전 집합 (차용어 금지)", []),
    (r"의도→제어\s*인터페이스", "의도-제어 변환 모듈 (화살표·인터페이스 표기 폐기)", []),
    # sub-grid 명칭 (ADR-0039 D1) — 코드 식별자 `--track-b`·`track_b` 는 코드 스팬 면제.
    (r"Track\s*[AB]\b",       "통합 스택 평가 / 티어 1 격리 하한 검증 (ADR-0039 명칭 폐기)", []),
]

# 메타/작업 문서 — 폐기 용어를 *논의·인용*(교체하라/폐기됨)하므로 검사 제외.
# 정본 원고(introduction·problem_formulation·architecture·…·results·cmsm-proof)만 검사.
META_FILE_PAT = re.compile(
    r"(OUTLINE|README|.*-review|.*_seed)\.md$|/figures/", re.IGNORECASE
)
# 용어를 *사용*이 아니라 *논의*하는 줄(교체·폐기·금지·매핑 언급, 취소선)은 면제.
DISCUSS_PAT = re.compile(r"교체|폐기|사용\s*금지|CLAUDE\.md|매핑|~~|deprecated", re.IGNORECASE)

# 코드/수식 라인 면제용 — 백틱 코드 스팬·수식 안은 식별자 허용(평문만 검사).
_CODE_SPAN = re.compile(r"`[^`]*`")
_MATH_SPAN = re.compile(r"\$[^$]*\$")


def _strip_code_math(line: str) -> str:
    """백틱 코드·인라인 수식 제거(평문만 남김) — 코드 식별자 면제."""
    return _MATH_SPAN.sub(" ", _CODE_SPAN.sub(" ", line))


def scan_file(path: Path) -> list[tuple[int, str, str, str]]:
    out = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if DISCUSS_PAT.search(raw):      # 용어를 논의·인용하는 줄은 면제(사용 아님).
            continue
        plain = _strip_code_math(raw)
        for pat, fix, allows in DEPRECATED:
            if not re.search(pat, plain):
                continue
            if any(re.search(a, plain) for a in allows):
                continue
            m = re.search(pat, plain)
            out.append((i, m.group(0), fix, raw.strip()[:100]))
    return out


def _self_test() -> int:
    """검사기 자기검증 — detection·allow·논의면제·코드면제가 회귀하지 않게."""
    import tempfile
    cases = [
        ("표준 격자를 돌렸다.", 1, "표준 격자 적발"),
        ("드론이 접지했다.", 1, "접지(grounding) 적발"),
        ("전기 접지 저항을 측정.", 0, "전기 접지 면제"),
        ("'버블'→'회피 영역'으로 교체.", 0, "논의 줄 면제(교체)"),
        ("`grounding_entropy` 변수.", 0, "코드 스팬 면제"),
        ("통합 스택 격자를 돌렸다.", 0, "정본 용어 통과"),
    ]
    ok = True
    for text, expect, desc in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write("# t\n" + text + "\n")
            p = Path(fh.name)
        n = len(scan_file(p))
        p.unlink()
        hit = 1 if n else 0
        mark = "✓" if hit == expect else "✗"
        if hit != expect:
            ok = False
        print(f"  {mark} {desc}: 적발={n} (기대 {'>0' if expect else '0'})")
    print("✓ self-test 통과" if ok else "✗ self-test 실패")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--self-test" in argv[1:]:
        return _self_test()
    paths = [a for a in argv[1:] if not a.startswith("-")]
    roots = [Path(p) for p in paths] if paths else [Path("paper")]
    include_meta = "--all" in argv[1:]
    files: list[Path] = []
    for r in roots:
        cand = sorted(r.rglob("*.md")) if r.is_dir() else [r]
        files += cand if include_meta else [c for c in cand if not META_FILE_PAT.search(str(c))]

    violations = 0
    for f in files:
        hits = scan_file(f)
        for ln, term, fix, ctx in hits:
            violations += 1
            print(f"{f}:{ln}: 폐기 용어 '{term}' → {fix}\n    | {ctx}")
    if violations:
        print(f"\n✗ 폐기 용어 {violations}건 ({len(files)} 파일 검사). "
              f"매핑 = scripts/check_terms.py DEPRECATED.")
        return 1
    print(f"✓ 폐기 용어 0건 ({len(files)} 파일 검사).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
