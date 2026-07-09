"""Open-Vocabulary 어휘 정의.

cmsm-proof §10.1 의 ξ_ovd context 채널 *입력 측* — 무엇을 찾을지 텍스트
프롬프트로 지정. ROS·ultralytics 의존성 없는 순수 데이터 구조.

운용 가정 (cmsm-proof §10.5 (CA-2) OVD 정확성):
- 어휘는 시나리오마다 *유한·고정* (시뮬 진입 시점에 결정). 실시간 추가는
  paper-1 범위 밖 (paper-2 명료화 루프에서 고려).
- 텍스트는 소문자·공백 trim. 'Couch ' 와 'couch' 는 동일 클래스로 취급.
- 중복은 자동 제거 (set-like). 순서는 *첫 등장 순* 유지 (deterministic
  output → cmsm-proof §10.4 명료화 루프 단조 수렴 정형 측 입력).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Tuple


def _normalize(prompt: str) -> str:
    """소문자 + 양끝 공백 trim. 내부 공백은 유지 ('living room' → 'living room')."""
    return prompt.strip().lower()


@dataclass(frozen=True)
class Vocabulary:
    """*의도해석기*에 공급되는 어휘 prompt 집합.

    Args:
        prompts: 정규화 *후* 의 텍스트 prompt 들. 생성자 직접 호출보다
            ``Vocabulary.from_strings(["Couch", " Table "])`` 권장 — 정규화·중복
            제거를 알아서 해 줌.

    Raises:
        ValueError: prompts 가 비었거나 정규화 후 빈 문자열 포함.
    """

    prompts: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.prompts:
            raise ValueError("Vocabulary 는 prompts 가 비어 있을 수 없음")
        for p in self.prompts:
            if not isinstance(p, str):
                raise TypeError(f"Vocabulary prompt 는 str 이어야 함: {p!r}")
            if not p or p != _normalize(p):
                raise ValueError(
                    f"Vocabulary prompt 가 정규화 안됨 (소문자/trim): {p!r}. "
                    f"Vocabulary.from_strings() 사용 권장.",
                )
        # 중복 거부 — module docstring 의 "set-like" 약속 유지. 직접 생성자 호출자도
        # ``Vocabulary.from_strings()`` 권장 메시지로 유도.
        if len(set(self.prompts)) != len(self.prompts):
            raise ValueError(
                f"Vocabulary prompts 에 중복 (set-like): {self.prompts!r}. "
                f"Vocabulary.from_strings() 사용 권장.",
            )

    @classmethod
    def from_strings(cls, prompts: Iterable[str]) -> "Vocabulary":
        """텍스트 list 에서 Vocabulary 생성 — 정규화 + dedup + 순서 보존."""
        seen: List[str] = []
        for raw in prompts:
            if not isinstance(raw, str):
                raise TypeError(f"prompt 는 str 이어야 함: {raw!r}")
            normalized = _normalize(raw)
            if not normalized:
                raise ValueError(f"빈 prompt (정규화 후): {raw!r}")
            if normalized not in seen:
                seen.append(normalized)
        return cls(prompts=tuple(seen))

    def as_list(self) -> List[str]:
        """ultralytics ``model.set_classes(list_of_strings)`` 에 직접 전달 가능한 형식."""
        return list(self.prompts)

    def __len__(self) -> int:
        return len(self.prompts)

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        return _normalize(item) in self.prompts
