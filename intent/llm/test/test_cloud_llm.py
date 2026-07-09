"""intent_llm.cloud_llm 단위 테스트 — openai.OpenAI mock.

실 API 호출 없이 CloudLLMWrapper.process() 검증:
  - M=3 독립 API 호출 + 신호 산출 (H/ρ/ℓ).
  - 다수결 skill + confidence_raw = ρ (ASK_USER 시 0.0).
  - API 오류 시 ASK_USER fallback (raise 안 함).
  - OPENAI_API_KEY 미설정 시 RuntimeError (process() 계약).
  - TRIAL_LOG_DIR 설정 시 JSONL 로그 저장.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from intent_llm.cloud_llm import (
    CATEGORY,
    CloudLLMWrapper,
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


_FAKE_API_KEY = 'sk-test-unit-fake'
_INPUT = IntentInput(utterance='go forward', scenario_id='S1')


_DEFAULT_MOVE_TO_ARGS = {'position': [1.0, 0.0, 1.5]}


def _make_response(skill: str, args: dict | None = None, logprob: float = -0.5):
    """단일 openai choice mock 생성 — JSON 응답 + 토큰 logprob."""
    # ADR-0027: move_to 측 position 필수 — args 미지정 시 기본값 주입.
    if args is None and skill == 'move_to':
        args = _DEFAULT_MOVE_TO_ARGS
    content = json.dumps({'skill': skill, 'args': args or {}})
    token = MagicMock()
    token.logprob = logprob
    choice = MagicMock()
    choice.message.content = content
    choice.logprobs.content = [token]
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# -------------------------------------------------------------------- construction


class TestConstruction:
    def test_gpt4o_constructs(self) -> None:
        w = CloudLLMWrapper('gpt-4o')
        assert w.identifier == 'gpt-4o'
        assert w._model_id == 'gpt-4o'

    def test_gpt5_constructs(self) -> None:
        w = CloudLLMWrapper('gpt-5')
        assert w._model_id == 'gpt-5'

    def test_gpt55_constructs(self) -> None:
        w = CloudLLMWrapper('gpt-5.5')
        assert w._model_id == 'gpt-5.5'

    def test_unknown_identifier_raises(self) -> None:
        with pytest.raises(ValueError, match='unknown cloud backbone'):
            CloudLLMWrapper('unknown-model')

    def test_empty_identifier_raises(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            CloudLLMWrapper('')

    def test_whitespace_identifier_raises(self) -> None:
        with pytest.raises(ValueError, match='identifier'):
            CloudLLMWrapper('   ')

    def test_category_attribute(self) -> None:
        assert CloudLLMWrapper.category == CATEGORY == 'cloud_llm'


# -------------------------------------------------------------------- API key required


class TestApiKeyRequired:
    def test_missing_api_key_raises_runtime_error(self) -> None:
        """OPENAI_API_KEY 미설정 → RuntimeError (process() 계약 — 운용 오류)."""
        w = CloudLLMWrapper('gpt-4o')
        env = {k: v for k, v in os.environ.items() if k != 'OPENAI_API_KEY'}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
                w.process(_INPUT)


# -------------------------------------------------------------------- M=3 호출 + 신호


class TestProcessMockedApi:
    """openai.OpenAI mock 측 실 API 없이 process() 동작 검증."""

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_m3_calls_made(self, mock_openai_cls: MagicMock) -> None:
        """M=3 독립 API 호출 검증 — 새 client 측 3회 completions.create."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('move_to')

        w = CloudLLMWrapper('gpt-4o')
        w.process(_INPUT)

        assert mock_client.chat.completions.create.call_count == _M_SELF_CONSISTENCY

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_majority_skill_returned(self, mock_openai_cls: MagicMock) -> None:
        """3/3 동일 skill → majority skill 반환."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('move_to')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert r.typed_action.skill == SkillName.MOVE_TO

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_confidence_raw_equals_rho_full_agreement(
        self, mock_openai_cls: MagicMock
    ) -> None:
        """3/3 동의 → ρ=1.0 → confidence_raw=1.0 (비-ASK_USER skill)."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('inspect')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert r.confidence_raw == pytest.approx(1.0)
        assert r.signals[SIGNAL_SELF_CONSISTENCY] == pytest.approx(1.0)

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_confidence_raw_min_for_ask_user(
        self, mock_openai_cls: MagicMock
    ) -> None:
        """모든 응답 ASK_USER → confidence_raw = CONFIDENCE_MIN (0.0)."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('ask_user')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert r.typed_action.skill == SkillName.ASK_USER
        assert r.confidence_raw == CONFIDENCE_MIN

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_llm_signals_populated_s1_absent(self, mock_openai_cls: MagicMock) -> None:
        """LLM 산출 신호 ρ/ℓ 모두 None 아님 + s1 부재 (OVD 전용, §2.1)."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('inspect')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert SIGNAL_ENTROPY not in r.signals
        assert r.signals[SIGNAL_SELF_CONSISTENCY] is not None
        assert r.signals[SIGNAL_LOGPROB] is not None

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_logprob_averaged_from_tokens(self, mock_openai_cls: MagicMock) -> None:
        """3회 모두 logprob=-1.0 → SIGNAL_LOGPROB = -1.0."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response(
            'move_to', logprob=-1.0
        )

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert r.signals[SIGNAL_LOGPROB] == pytest.approx(-1.0)

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_s3_capability_true(self, mock_openai_cls: MagicMock) -> None:
        """ADR-0020 D8 — cloud 는 logprob 능력 보유 → s3_capability=True."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response(
            'move_to', logprob=-1.0
        )

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert r.signals[SIGNAL_S3_CAPABILITY] is True

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_two_third_majority(self, mock_openai_cls: MagicMock) -> None:
        """2/3 동의 → majority skill 선택, ρ = 2/3."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = [
            _make_response('move_to'),
            _make_response('move_to'),
            _make_response('inspect'),
        ]

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert r.typed_action.skill == SkillName.MOVE_TO
        assert r.signals[SIGNAL_SELF_CONSISTENCY] == pytest.approx(2 / 3)

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_returns_intent_result_type(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('move_to')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert isinstance(r, IntentResult)


# -------------------------------------------------------------------- error fallback


class TestErrorFallback:
    """API 예외 → ASK_USER fallback (raise 안 함) — interface.py 계약."""

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_api_error_returns_ask_user(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError('API 오류')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert isinstance(r, IntentResult)
        assert r.typed_action.skill == SkillName.ASK_USER
        assert r.confidence_raw == CONFIDENCE_MIN

    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_api_error_signals_max_uncertainty(
        self, mock_openai_cls: MagicMock
    ) -> None:
        """API 오류 fallback — 최대 불확실 신호 (ρ=0.0, ℓ=-10.0). s1 은 OVD
        전용이라 fallback signals 에도 부재 (§2.1)."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ConnectionError('연결 오류')

        r = CloudLLMWrapper('gpt-4o').process(_INPUT)
        assert SIGNAL_ENTROPY not in r.signals
        assert r.signals[SIGNAL_SELF_CONSISTENCY] == _ERROR_SIGNALS[SIGNAL_SELF_CONSISTENCY]
        assert r.signals[SIGNAL_LOGPROB] == _ERROR_SIGNALS[SIGNAL_LOGPROB]


# -------------------------------------------------------------------- TRIAL_LOG_DIR


class TestTrialLog:
    @patch('openai.OpenAI')
    @patch.dict(os.environ, {'OPENAI_API_KEY': _FAKE_API_KEY})
    def test_log_written_when_trial_log_dir_set(
        self, mock_openai_cls: MagicMock
    ) -> None:
        """TRIAL_LOG_DIR 설정 시 cloud_llm_<model>.jsonl 저장."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('move_to')

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {'TRIAL_LOG_DIR': tmpdir}):
                CloudLLMWrapper('gpt-4o').process(_INPUT)

            log_path = os.path.join(tmpdir, 'cloud_llm_gpt-4o.jsonl')
            assert os.path.exists(log_path)
            with open(log_path) as f:
                entry = json.loads(f.readline())
            assert entry['model'] == 'gpt-4o'
            assert 'skills' in entry
            assert 'rho' in entry
            # RQ3 latency (ADR-0039 D3-②) — mock 호출이라 ≈0 but 필드 존재·비음수.
            assert 'inference_latency_s' in entry
            assert entry['inference_latency_s'] >= 0.0

    @patch('openai.OpenAI')
    def test_no_log_when_trial_log_dir_unset(
        self, mock_openai_cls: MagicMock, tmp_path
    ) -> None:
        """TRIAL_LOG_DIR 미설정 시 파일 없음 (smoke)."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response('move_to')

        env = {k: v for k, v in os.environ.items() if k != 'TRIAL_LOG_DIR'}
        env['OPENAI_API_KEY'] = _FAKE_API_KEY
        with patch.dict(os.environ, env, clear=True):
            CloudLLMWrapper('gpt-4o').process(_INPUT)

        assert not list(tmp_path.glob('cloud_llm_*.jsonl'))


# -------------------------------------------------------------------- protocol


class TestProtocol:
    def test_satisfies_intent_wrapper(self) -> None:
        assert isinstance(CloudLLMWrapper('gpt-4o'), IntentWrapper)
