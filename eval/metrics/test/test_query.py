"""eval_metrics.query 단위 테스트 — QR = n_ask/T [1/s]."""

from __future__ import annotations

import pytest

from eval_metrics.query import query_rate


class TestQueryRate:
    def test_zero_ask_zero_rate(self):
        assert query_rate(n_ask_user=0, episode_duration_s=60.0) == 0.0

    def test_typical_rate(self):
        """5 ask_user / 60 s = 0.0833 1/s."""
        rate = query_rate(n_ask_user=5, episode_duration_s=60.0)
        assert abs(rate - 5 / 60.0) < 1e-9

    def test_high_rate(self):
        """1 ask_user / 0.5 s = 2.0 1/s."""
        assert query_rate(n_ask_user=1, episode_duration_s=0.5) == 2.0

    def test_negative_ask_rejected(self):
        with pytest.raises(ValueError, match='n_ask_user 음수'):
            query_rate(n_ask_user=-1, episode_duration_s=60.0)

    def test_zero_duration_rejected(self):
        with pytest.raises(ValueError, match='episode_duration_s'):
            query_rate(n_ask_user=5, episode_duration_s=0.0)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match='episode_duration_s'):
            query_rate(n_ask_user=5, episode_duration_s=-1.0)
