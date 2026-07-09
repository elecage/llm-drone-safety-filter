"""Docker container ROS 2 토픽 구독 브리지 — ask_user 질문 수신 (ADR-0016 D3).

STT 의 [ros_bridge](../../stt/intent_stt/ros_bridge.py) *역방향* — STT 는 host
텍스트를 container 로 *발행*(pub), TTS 는 container 의 ask_user 질문을 host 로
*구독*(echo)한다. host 에 ROS 2 미설치 → `docker exec ros2 topic echo` 로
스트리밍 수신 후 파싱 ([ADR-0015 D3](../../../docs/handover/decisions/0015-stt-module-lock.md)
의 docker exec 브리지 패턴 정합).
"""
from __future__ import annotations

import re
import subprocess
from typing import Callable, Iterator, Optional

TOPIC = "/intent/ask_user_question"
MSG_TYPE = "std_msgs/msg/String"
_ENTRYPOINT = "/usr/local/bin/entrypoint.sh"
# ros2 topic echo 는 (1) workspace overlay 환경 + (2) ros2 daemon 이 필요.
# pub(STT, --once)은 daemon 없이 즉시 발행되나, echo 는 publisher discovery 에
# daemon xmlrpc 를 쓴다. 컨테이너 새로 생성 후 daemon 미기동이면 echo 가
# `rclpy.ok()` False 로 실패 → source + daemon start 를 echo 앞에 보장 (실 sim
# 종단 검증에서 확인, 2026-05-29). daemon start 는 idempotent (이미 떠있으면 no-op).
_SETUP = (
    "source /workspace/install/setup.bash && "
    "(ros2 daemon start >/dev/null 2>&1 || true)"
)

# ros2 topic echo std_msgs/String 출력의 한 메시지: ``data: '질문...'`` 또는
# ``data: 질문...``. 메시지 구분자 ``---``.
_DATA_LINE = re.compile(r"^data:\s*(.*?)\s*$")
_QUOTES = re.compile(r"^['\"](.*)['\"]$")


def parse_question(data_field: str) -> str:
    """``data:`` 필드 값에서 양끝 따옴표 제거 후 질문 텍스트 반환."""
    m = _QUOTES.match(data_field.strip())
    return (m.group(1) if m else data_field).strip()


def iter_questions_from_echo(lines: Iterator[str]) -> Iterator[str]:
    """``ros2 topic echo`` stdout 라인 스트림 → 질문 텍스트 제너레이터.

    각 ``data:`` 라인을 한 질문으로 산출 (빈 질문은 건너뜀). 순수 파싱 함수 —
    host venv 단위 테스트 가능 (subprocess 무관).
    """
    for line in lines:
        m = _DATA_LINE.match(line)
        if not m:
            continue
        q = parse_question(m.group(1))
        if q:
            yield q


def stream_questions(
    on_question: Callable[[str], None],
    container: str = "llmdrone-sim",
    topic: str = TOPIC,
    _popen=subprocess.Popen,
) -> None:
    """container 의 ask_user 질문 토픽을 echo 구독 → 각 질문마다 콜백 호출.

    블로킹 — Ctrl+C 까지 실행. ``_popen`` 은 테스트 주입용.
    """
    bash_cmd = f"{_SETUP} && ros2 topic echo {topic} {MSG_TYPE}"
    proc = _popen(
        [
            "docker", "exec", container,
            _ENTRYPOINT, "bash", "-c", bash_cmd,
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        if proc.stdout is not None:
            for q in iter_questions_from_echo(proc.stdout):
                on_question(q)
    finally:
        proc.terminate()


def first_question(
    container: str = "llmdrone-sim",
    topic: str = TOPIC,
    timeout: float = 15.0,
    _run=subprocess.run,
) -> Optional[str]:
    """``--once`` 로 단일 질문 수신 (테스트/일회성). 없으면 None."""
    bash_cmd = f"{_SETUP} && ros2 topic echo --once {topic} {MSG_TYPE}"
    result = _run(
        ["docker", "exec", container, _ENTRYPOINT, "bash", "-c", bash_cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    for q in iter_questions_from_echo(iter(result.stdout.splitlines())):
        return q
    return None
