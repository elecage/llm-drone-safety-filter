"""eval_metrics.autonomy 단위 테스트 — ARS = 1 - n_ask/n_commands."""

from __future__ import annotations

import pytest

from eval_metrics.autonomy import autonomy_response_score


class TestAutonomyResponseScore:
    def test_no_ask_full_autonomy(self):
        """ask_user 없음 → ARS = 1.0."""
        assert autonomy_response_score(n_ask_user=0, n_commands=10) == 1.0

    def test_all_ask_zero_autonomy(self):
        """모든 명령 ask_user → ARS = 0.0."""
        assert autonomy_response_score(n_ask_user=5, n_commands=5) == 0.0

    def test_half_ask_half_autonomy(self):
        """절반 ask_user → ARS = 0.5."""
        assert autonomy_response_score(n_ask_user=3, n_commands=6) == 0.5

    def test_zero_commands_returns_one(self):
        """경계 잠금 (ADR-0025 D2 amendment 2) — n_commands=0 → ARS:=1."""
        assert autonomy_response_score(n_ask_user=0, n_commands=0) == 1.0

    def test_negative_ask_rejected(self):
        with pytest.raises(ValueError, match='n_ask_user 음수'):
            autonomy_response_score(n_ask_user=-1, n_commands=10)

    def test_negative_commands_rejected(self):
        with pytest.raises(ValueError, match='n_commands 음수'):
            autonomy_response_score(n_ask_user=0, n_commands=-1)

    def test_ask_exceeds_commands_rejected(self):
        """정합 위반 — ask_user 는 commands 의 부분 집합."""
        with pytest.raises(ValueError, match='정합 위반'):
            autonomy_response_score(n_ask_user=11, n_commands=10)
