"""intent_stt.ros_bridge 단위 테스트 — subprocess mock."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from intent_stt.ros_bridge import _sanitize, publish_utterance, _TOPIC


def _mock_run(returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    return r


# --- _sanitize ---

def test_sanitize_removes_double_quote():
    assert '"' not in _sanitize('say "hello"')


def test_sanitize_removes_single_quote():
    assert "'" not in _sanitize("it's cold")


def test_sanitize_removes_backtick():
    assert "`" not in _sanitize("run `cmd`")


def test_sanitize_removes_dollar():
    assert "$" not in _sanitize("cost $5")


def test_sanitize_removes_backslash():
    assert "\\" not in _sanitize("path\\file")


def test_sanitize_strips_whitespace():
    assert _sanitize("  hello  ") == "hello"


def test_sanitize_preserves_normal_text():
    assert _sanitize("go to the kitchen") == "go to the kitchen"


# --- publish_utterance ---

def test_publish_calls_docker_exec():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("go to kitchen")
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "docker"
    assert cmd[1] == "exec"


def test_publish_passes_container_name():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("test", container="my-sim")
    cmd = mock_run.call_args[0][0]
    assert "my-sim" in cmd


def test_publish_uses_env_for_text():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("hover here")
    cmd = mock_run.call_args[0][0]
    # -e flag should carry the text
    assert "-e" in cmd
    env_entry = cmd[cmd.index("-e") + 1]
    assert "_STT_TEXT=hover here" == env_entry


def test_publish_topic_in_bash_cmd():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("test")
    cmd = mock_run.call_args[0][0]
    bash_cmd = cmd[-1]
    assert _TOPIC in bash_cmd


def test_publish_default_wait_subscribers_is_two():
    """e2e race 회피 — default 로 wrapper + sigma_bridge 2 구독자 대기 (-w 2)."""
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("test")
    bash_cmd = mock_run.call_args[0][0][-1]
    assert "-w 2" in bash_cmd


def test_publish_wait_subscribers_override():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("test", wait_subscribers=3)
    bash_cmd = mock_run.call_args[0][0][-1]
    assert "-w 3" in bash_cmd


def test_publish_wait_subscribers_zero_omits_flag():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("test", wait_subscribers=0)
    bash_cmd = mock_run.call_args[0][0][-1]
    assert "-w" not in bash_cmd


def test_publish_sanitizes_quotes():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("it's a \"test\"")
    cmd = mock_run.call_args[0][0]
    env_entry = cmd[cmd.index("-e") + 1]
    assert "'" not in env_entry
    assert '"' not in env_entry.split("=", 1)[1]


def test_publish_raises_on_docker_error():
    with patch(
        "intent_stt.ros_bridge.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "docker"),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            publish_utterance("test")


def test_publish_check_true_is_set():
    with patch("intent_stt.ros_bridge.subprocess.run", return_value=_mock_run()) as mock_run:
        publish_utterance("test")
    _, kwargs = mock_run.call_args
    assert kwargs.get("check") is True
