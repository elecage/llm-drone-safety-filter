"""intent_llm.edge_llm 단위 테스트 — requests.post mock.

실 Ollama HTTP 호출 없이 EdgeLLMWrapper.process() 검증:
  - M=3 독립 HTTP 호출 + 신호 산출 (H/ρ).
  - 다수결 skill + confidence_raw.
  - Ollama logprob fallback 값 (-2.0) 고정.
  - 연결 오류 시 ASK_USER fallback (catch-all — interface.py 계약).
  - TRIAL_LOG_DIR 설정 시 JSONL 로그 저장.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from intent_llm.edge_llm import (
    CATEGORY,
    EdgeLLMWrapper,
    _ERROR_SIGNALS,
    _M_SELF_CONSISTENCY,
)
from intent_llm.interface import (
    CONFIDENCE_MIN,
    SIGNAL_ENTROPY,
    SIGNAL_LOGPROB,
    SIGNAL_S3_CAPABILITY,
    SIGNAL_SELF_CONSISTENCY,
    IntentInput,
    IntentResult,
    IntentWrapper,
)
from intent_llm.skill_catalog import SkillName


_INPUT = IntentInput(utterance='go forward', scenario_id='S1')


_DEFAULT_MOVE_TO_ARGS = {'position': [1.0, 0.0, 1.5]}


def _make_ollama_response(skill: str, args: dict | None = None):
    """requests.post mock response — Ollama /api/chat 응답 형식."""
    # ADR-0027: move_to 측 position 필수 — args 미지정 시 기본값 주입.
    if args is None and skill == 'move_to':
        args = _DEFAULT_MOVE_TO_ARGS
    content = json.dumps({'skill': skill, 'args': args or {}})
    resp = MagicMock()
    resp.json.return_value = {'message': {'content': content}}
    resp.raise_for_status.return_value = None
    return resp


# -------------------------------------------------------------------- construction


class TestConstruction:
    def test_llama_constructs(self) -> None:
        w = EdgeLLMWrapper('llama-3.2-11b-vision')
        assert w.identifier == 'llama-3.2-11b-vision'
        assert w._model_tag == 'llama3.2-vision:11b'

    def test_qwen_constructs(self) -> None:
        w = EdgeLLMWrapper('qwen2.5-vl-7b')
        assert w._model_tag == 'qwen2.5-vl:7b'

    def test_gemma_constructs(self) -> None:
        w = EdgeLLMWrapper('gemma-4-e4b')
        assert w._model_tag == 'gemma4:e4b'

    def test_unknown_identifier_raises(self) -> None:
        with pytest.raises(ValueError, match='unknown edge backbone'):
            EdgeLLMWrapper('unknown-model')

    def test_empty_identifier_raises(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            EdgeLLMWrapper('')

    def test_category_attribute(self) -> None:
        assert EdgeLLMWrapper.category == CATEGORY == 'edge_llm'


# -------------------------------------------------------------------- M=3 호출 + 신호


class TestProcessMockedHttp:
    """requests.post mock 측 Ollama 없이 process() 동작 검증."""

    @patch('requests.post')
    def test_m3_calls_made(self, mock_post: MagicMock) -> None:
        """M=3 독립 HTTP POST 호출 검증."""
        mock_post.return_value = _make_ollama_response('move_to')

        EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)

        assert mock_post.call_count == _M_SELF_CONSISTENCY

    @patch('requests.post')
    def test_majority_skill_returned(self, mock_post: MagicMock) -> None:
        """3/3 동일 skill → majority skill 반환."""
        mock_post.return_value = _make_ollama_response('inspect')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert r.typed_action.skill == SkillName.INSPECT

    @patch('requests.post')
    def test_confidence_raw_equals_rho_full_agreement(
        self, mock_post: MagicMock
    ) -> None:
        """3/3 동의 → ρ=1.0 → confidence_raw=1.0."""
        mock_post.return_value = _make_ollama_response('move_to')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert r.confidence_raw == pytest.approx(1.0)

    @patch('requests.post')
    def test_confidence_raw_min_for_ask_user(self, mock_post: MagicMock) -> None:
        """모든 응답 ASK_USER → confidence_raw = CONFIDENCE_MIN."""
        mock_post.return_value = _make_ollama_response('ask_user')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert r.typed_action.skill == SkillName.ASK_USER
        assert r.confidence_raw == CONFIDENCE_MIN

    @patch('requests.post')
    def test_s3_structural_absent(self, mock_post: MagicMock) -> None:
        """ADR-0020 D8 — Ollama logprob 무능력 → s3_logprob=None + s3_capability=False.

        종전 sentinel(-2.0) 발행 폐기. 소비자(estimator)가 곱에서 s3 제외(neutral).
        """
        mock_post.return_value = _make_ollama_response('move_to')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert r.signals[SIGNAL_LOGPROB] is None
        assert r.signals[SIGNAL_S3_CAPABILITY] is False

    @patch('requests.post')
    def test_rho_populated_s1_absent(self, mock_post: MagicMock) -> None:
        """ρ 신호 None 아님 + s1 부재 (OVD 전용, §2.1). ℓ는 구조적 부재(None)."""
        mock_post.return_value = _make_ollama_response('inspect')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert SIGNAL_ENTROPY not in r.signals
        assert r.signals[SIGNAL_SELF_CONSISTENCY] is not None

    @patch('requests.post')
    def test_two_third_majority(self, mock_post: MagicMock) -> None:
        """2/3 동의 → majority skill, ρ = 2/3."""
        mock_post.side_effect = [
            _make_ollama_response('move_to'),
            _make_ollama_response('move_to'),
            _make_ollama_response('inspect'),
        ]

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert r.typed_action.skill == SkillName.MOVE_TO
        assert r.signals[SIGNAL_SELF_CONSISTENCY] == pytest.approx(2 / 3)

    @patch('requests.post')
    def test_ollama_base_url_override(self, mock_post: MagicMock) -> None:
        """OLLAMA_BASE_URL 환경변수 → endpoint 재정의."""
        custom_url = 'http://mac-mini.local:11434'
        mock_post.return_value = _make_ollama_response('move_to')

        with patch.dict(os.environ, {'OLLAMA_BASE_URL': custom_url}):
            EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)

        called_url = mock_post.call_args[0][0]
        assert called_url.startswith(custom_url)


# -------------------------------------------------------------------- error fallback


class TestErrorFallback:
    """연결 오류 → ASK_USER fallback (catch-all — interface.py 계약)."""

    @patch('requests.post')
    def test_connection_error_returns_ask_user(
        self, mock_post: MagicMock
    ) -> None:
        mock_post.side_effect = ConnectionRefusedError('Ollama 미기동')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert isinstance(r, IntentResult)
        assert r.typed_action.skill == SkillName.ASK_USER
        assert r.confidence_raw == CONFIDENCE_MIN

    @patch('requests.post')
    def test_http_error_signals_max_uncertainty(
        self, mock_post: MagicMock
    ) -> None:
        """HTTP 오류 fallback — 최대 불확실 신호 (ρ=0.0, ℓ=-10.0). s1 은 OVD
        전용이라 fallback signals 에도 부재 (§2.1)."""
        mock_post.side_effect = RuntimeError('서버 오류')

        r = EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)
        assert SIGNAL_ENTROPY not in r.signals
        assert r.signals[SIGNAL_SELF_CONSISTENCY] == _ERROR_SIGNALS[SIGNAL_SELF_CONSISTENCY]
        assert r.signals[SIGNAL_LOGPROB] == _ERROR_SIGNALS[SIGNAL_LOGPROB]


# -------------------------------------------------------------------- TRIAL_LOG_DIR


class TestTrialLog:
    @patch('requests.post')
    def test_log_written_when_trial_log_dir_set(
        self, mock_post: MagicMock
    ) -> None:
        """TRIAL_LOG_DIR 설정 시 edge_llm_<safe_tag>.jsonl 저장."""
        mock_post.return_value = _make_ollama_response('move_to')

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {'TRIAL_LOG_DIR': tmpdir}):
                EdgeLLMWrapper('gemma-4-e4b').process(_INPUT)

            log_path = os.path.join(tmpdir, 'edge_llm_gemma4_e4b.jsonl')
            assert os.path.exists(log_path)
            with open(log_path) as f:
                entry = json.loads(f.readline())
            assert entry['model'] == 'gemma4:e4b'
            assert 'skills' in entry
            # RQ3 latency (ADR-0039 D3-②) — mock 호출이라 ≈0 but 필드 존재·비음수.
            assert 'inference_latency_s' in entry
            assert entry['inference_latency_s'] >= 0.0


# -------------------------------------------------------------------- protocol


class TestProtocol:
    def test_satisfies_intent_wrapper(self) -> None:
        assert isinstance(EdgeLLMWrapper('gemma-4-e4b'), IntentWrapper)
