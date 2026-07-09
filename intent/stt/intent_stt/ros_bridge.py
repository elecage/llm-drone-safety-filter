"""Docker container ROS 2 토픽 발행 브리지 (ADR-0015 D3)."""

from __future__ import annotations

import re
import subprocess

_TOPIC = "/intent/user_prompt_raw"
_MSG_TYPE = "std_msgs/msg/String"
_ENTRYPOINT = "/usr/local/bin/entrypoint.sh"

# 정본 의도 스택(start_intent_stack.sh)이 /intent/user_prompt_raw 를 구독하는
# 노드 수: wrapper_node + sigma_bridge = 2. 발행 전 -w 로 두 구독자가 모두
# discovery 될 때까지 대기해 e2e 레이스(stale _last_utterance) 회피
# (2026-05-30 e2e progress 이슈 1).
_DEFAULT_WAIT_SUBSCRIBERS = 2

# 드론 음성 명령에 불필요한 bash/YAML 특수문자를 제거.
_UNSAFE = re.compile(r'["\'`\\$]')


def _sanitize(text: str) -> str:
    return _UNSAFE.sub("", text).strip()


def publish_utterance(
    text: str,
    container: str = "llmdrone-sim",
    timeout: float = 15.0,
    wait_subscribers: int = _DEFAULT_WAIT_SUBSCRIBERS,
) -> None:
    """텍스트를 container 내 /intent/user_prompt_raw 에 1회 발행.

    docker exec -e 로 텍스트를 환경변수로 전달해 쉘 주입을 최소화.
    드론 명령에 쓰이지 않는 특수문자(따옴표·백틱·달러·백슬래시)는 제거.

    wait_subscribers: ros2 topic pub -w 옵션 — 발행 전 대기할 구독자 수.
    default=2 (wrapper_node + sigma_bridge). 0 이면 -w 미부착.
    """
    safe = _sanitize(text)
    wait_flag = f" -w {wait_subscribers}" if wait_subscribers > 0 else ""
    bash_cmd = (
        f"ros2 topic pub --once{wait_flag} {_TOPIC} {_MSG_TYPE} "
        f"\"data: '$_STT_TEXT'\""
    )
    subprocess.run(
        [
            "docker", "exec",
            "-e", f"_STT_TEXT={safe}",
            container,
            _ENTRYPOINT, "bash", "-c", bash_cmd,
        ],
        check=True,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
