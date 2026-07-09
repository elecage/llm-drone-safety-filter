"""eval_metrics.success 단위 테스트."""

from __future__ import annotations

import pytest

from eval_metrics.success import task_success_rate


class TestTaskSuccessRate:
    def test_all_success(self):
        assert task_success_rate([True, True, True, True]) == 1.0

    def test_all_fail(self):
        assert task_success_rate([False, False, False]) == 0.0

    def test_half_half(self):
        assert task_success_rate([True, False, True, False]) == 0.5

    def test_single_success(self):
        assert task_success_rate([True]) == 1.0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match='빈 list'):
            task_success_rate([])

    def test_non_bool_rejected(self):
        with pytest.raises(TypeError, match='bool'):
            task_success_rate([True, 1, False])  # type: ignore
