"""Vocabulary 단위 테스트."""

from __future__ import annotations

import pytest

from intent_ovd.vocabulary import Vocabulary


class TestFromStrings:
    def test_basic(self) -> None:
        v = Vocabulary.from_strings(["couch", "table"])
        assert v.as_list() == ["couch", "table"]
        assert len(v) == 2

    def test_lowercase_and_trim(self) -> None:
        v = Vocabulary.from_strings([" Couch ", "TABLE", "  chair"])
        assert v.as_list() == ["couch", "table", "chair"]

    def test_dedup_preserves_first_order(self) -> None:
        v = Vocabulary.from_strings(["couch", "table", "Couch", "TABLE", "lamp"])
        assert v.as_list() == ["couch", "table", "lamp"]

    def test_internal_whitespace_preserved(self) -> None:
        v = Vocabulary.from_strings(["living room", "Living Room"])
        # 정규화 = lowercase + 양끝 trim; 내부 공백 유지.
        assert v.as_list() == ["living room"]

    def test_empty_iterable_raises(self) -> None:
        with pytest.raises(ValueError, match="비어"):
            Vocabulary.from_strings([])

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="빈 prompt"):
            Vocabulary.from_strings(["couch", ""])

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="빈 prompt"):
            Vocabulary.from_strings(["couch", "   "])

    def test_non_str_raises(self) -> None:
        with pytest.raises(TypeError):
            Vocabulary.from_strings(["couch", 42])  # type: ignore[list-item]


class TestDirectConstructor:
    def test_normalized_input_ok(self) -> None:
        v = Vocabulary(prompts=("couch", "table"))
        assert v.as_list() == ["couch", "table"]

    def test_non_normalized_rejects(self) -> None:
        with pytest.raises(ValueError, match="정규화"):
            Vocabulary(prompts=("Couch",))

    def test_empty_tuple_rejects(self) -> None:
        with pytest.raises(ValueError, match="비어"):
            Vocabulary(prompts=())

    def test_duplicate_rejects(self) -> None:
        """직접 생성자도 dedup 강제 — module docstring 'set-like' 약속 정합."""
        with pytest.raises(ValueError, match="중복"):
            Vocabulary(prompts=("couch", "couch"))


class TestContains:
    def test_normalized_lookup(self) -> None:
        v = Vocabulary.from_strings(["couch", "table"])
        assert "couch" in v
        assert "Couch" in v  # __contains__ 가 정규화 적용
        assert " TABLE " in v

    def test_not_present(self) -> None:
        v = Vocabulary.from_strings(["couch"])
        assert "table" not in v

    def test_non_str(self) -> None:
        v = Vocabulary.from_strings(["couch"])
        assert 42 not in v  # type: ignore[operator]


class TestImmutability:
    def test_frozen(self) -> None:
        v = Vocabulary.from_strings(["couch"])
        with pytest.raises(Exception):  # dataclass FrozenInstanceError
            v.prompts = ("table",)  # type: ignore[misc]
