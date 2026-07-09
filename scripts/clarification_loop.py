#!/usr/bin/env python3
"""명료화 인터랙션 루프 — STT→LLM→(ask_user)→TTS→재STT 종단 (ADR-0016 D3, B4).

[intent_loop.orchestrator](../intent/loop/intent_loop/orchestrator.py) 의 IO 주입
루프에 실 IO 를 연결: STT(Enter 모드 stdin 또는 whisper 마이크) + wrapper(docker
echo /intent/llm_sigma_raw) + TTS(macOS say) + hover(docker pub return_to_dock).

루프 종료 안전(L4): max 3회 또는 30s 초과 시 hover. 누적 context 는 직전 발화 +
응답 공백 연결 (ADR-0016 D3 step 5, 단순 전략 — 구조화 누적은 후속).

## STT 모드

환경변수 `STT_MODE`:
  - `stdin` (기본) — 콘솔 텍스트 입력 (마이크 권한 불필요 데모 모드).
  - `mic`         — Enter push-to-talk 마이크 → whisper-server (WHISPER_URL).
                    선행: `whisper-server` 가 떠 있어야 함 (run_stt.sh --loop 이
                    한 번에 같이 띄움).

사용:
    # 1) stdin 모드 (whisper 없이 텍스트 입력)
    SAY_VOICE=Yuna CONTAINER_NAME=llmdrone-sim .venv/bin/python scripts/clarification_loop.py

    # 2) 마이크 모드 (whisper-server + push-to-talk Enter)
    ./scripts/run_stt.sh --loop

`VOICE_LANG`(STT·TTS 공통)으로 voice 결정: `ko`→Yuna, `en`→Samantha, `auto`→텍스트
한글 여부로 매 발화 자동 ([say_client.voice_for_lang]).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "intent" / "loop"))
sys.path.insert(0, str(_REPO / "intent" / "tts"))
sys.path.insert(0, str(_REPO / "intent" / "stt"))

from intent_loop.loop_policy import LoopConfig
from intent_loop.orchestrator import LoopIO, run_clarification_loop
from intent_tts.say_client import speak as say_speak, voice_for_lang

CONTAINER = os.environ.get("CONTAINER_NAME", "llmdrone-sim")
# VOICE_LANG = STT·TTS 공통 언어 (ko / en / auto). run_stt.sh 가 동일 변수로
# whisper-server --language 와 여기 TTS voice 선택을 함께 잡는다.
# auto → say_client.pick_voice 가 매 발화 텍스트로 ko/en voice 결정.
VOICE_LANG = os.environ.get("VOICE_LANG", "auto").lower()
STT_MODE = os.environ.get("STT_MODE", "stdin")
WHISPER_URL = os.environ.get("WHISPER_URL", "http://127.0.0.1:8765/inference")
# LoopConfig 데모 override — 정형 T_resp=30s 는 RT 가정 (paper §C). 실 마이크
# 데모는 마이크 녹음 + whisper + OpenAI gpt-4o M=3 + listener polling 합쳐
# 매 라운드 15-25s. 데모 timeout 은 느슨하게.
LOOP_MAX_TURNS = int(os.environ.get("LOOP_MAX_TURNS", "3"))
LOOP_TIMEOUT_S = float(os.environ.get("LOOP_TIMEOUT_S", "120"))
_ENTRYPOINT = "/usr/local/bin/entrypoint.sh"
_SETUP = "source /workspace/install/setup.bash && (ros2 daemon start >/dev/null 2>&1 || true)"

# whisper hallucination 필터 (stt_pipeline 정합) — 구두점/공백만 응답 무시.
_HALLUCINATION_RE = re.compile(r"[.。!?…\s]+")


def _capture_stdin() -> str:
    """Enter 모드 STT 대용 — 콘솔 입력(마이크 권한 불필요 데모). 빈 줄 = 무응답."""
    try:
        return input("[loop] 발화 입력 > ").strip()
    except EOFError:
        return ""


def _make_mic_capture():
    """마이크 push-to-talk capture closure — 매 호출마다 한 라운드 녹음 → 텍스트.

    Enter → 녹음 시작 → Enter → 녹음 중지 → whisper transcribe → 텍스트 반환.
    빈 문자열 = 무응답 (무음 / 너무 짧음 / whisper 실패 / hallucination).
    PushToTalkCapture 인스턴스는 closure 안에서 재사용 (sounddevice stream 비용 절감).
    """
    from intent_stt.mic_capture import PushToTalkCapture
    from intent_stt.whisper_client import transcribe
    import numpy as np

    cap = PushToTalkCapture()

    def capture() -> str:
        try:
            input("[loop] → [Enter] 녹음 시작 ")
        except EOFError:
            return ""
        cap.start()
        print("[loop] ● 녹음 중... (Enter 키로 중지)")
        try:
            input()
        except EOFError:
            cap.stop()
            return ""
        audio = cap.stop()

        n_samples = len(audio)
        duration_s = n_samples / 16000
        if n_samples < 1600:  # 0.1s 미만
            print(f"[loop] ─ 너무 짧음 ({duration_s:.2f}s)")
            return ""

        try:
            text = transcribe(np.asarray(audio), url=WHISPER_URL).strip()
        except ConnectionError:
            print(f"[loop] ✗ whisper-server 연결 실패 ({WHISPER_URL})")
            return ""
        except Exception as exc:  # noqa: BLE001
            print(f"[loop] ✗ whisper 오류: {exc}")
            return ""

        if not text or _HALLUCINATION_RE.fullmatch(text):
            print(f"[loop] ─ 무시됨 ({text!r})")
            return ""
        print(f"[loop] ✓ 인식: {text!r}")
        return text

    return capture


# publish / receive 결합 — race window 회피.
# orchestrator 는 publish_intent_input 그 다음 receive_intent 순서로 호출하지만,
# 두 도커 exec 사이의 1-2s gap 동안 wrapper 가 sigma_raw 발행을 끝내면 receive 의
# `ros2 topic echo --once` 가 메시지를 *놓침* → 영영 다음 발행 기다림 → timeout.
# 해소: publish 호출 시 텍스트만 큐에 저장 → receive 호출에서 listener subprocess
# 를 먼저 띄우고(0.5s settle) 그 다음 publish 실행 → wait. wrapper 의 발행이 echo
# 가 listening 한 *이후* 에 일어나도록 강제.
_pending_publish: "list[str]" = []
_RECEIVE_TIMEOUT_S = 60.0  # gpt-4o M=3 호출 + 누적 발화 ≈ 5-30s, 여유 60s


def _publish_utterance(text: str) -> None:
    """발화를 큐에 저장 — 실제 publish 는 _receive_intent 가 listener 띄운 후 실행."""
    _pending_publish.append(text)


_LISTENER_READY_TIMEOUT_S = 10.0
_LISTENER_POLL_INTERVAL_S = 0.3


def _sigma_sub_count() -> int:
    """현재 /intent/llm_sigma_raw 구독자 수 (실패 시 -1)."""
    try:
        out = subprocess.run(
            ["docker", "exec", CONTAINER, _ENTRYPOINT, "bash", "-c",
             f"{_SETUP} && ros2 topic info /intent/llm_sigma_raw"],
            capture_output=True, text=True, timeout=5.0,
        ).stdout
    except subprocess.SubprocessError:
        return -1
    for line in out.splitlines():
        if "Subscription count:" in line:
            try:
                return int(line.split(":")[-1].strip())
            except ValueError:
                return -1
    return -1


def _receive_intent() -> "tuple[bool, str]":
    """listener 먼저 시작 → listener 가 실 구독자 등록될 때까지 대기 → 큐된 발화
    publish → 응답 수신 (race 회피).
    """
    from intent_stt.ros_bridge import publish_utterance as _ros_publish

    # --full-length: ros2 topic echo 기본 truncate-length=128 로 sigma payload 잘림
    # (signals 필드 등). JSON parse 실패 회피 위해 잘림 비활성.
    # baseline 동적 계산 — listener 시작 전 sigma_raw 구독자 수.
    # sigma_bridge 가 N 개(예: 이전 stack 잔존) 떠 있어도 OK — listener 가 +1
    # 더 등록되면 통과. 고정 baseline(=1) 가정의 race 회피.
    baseline = _sigma_sub_count()
    if baseline < 0:
        baseline = 1  # info 조회 실패 시 보수적 fallback

    bash = (
        f"{_SETUP} && "
        f"ros2 topic echo --once --full-length /intent/llm_sigma_raw std_msgs/msg/String"
    )
    listener = subprocess.Popen(
        ["docker", "exec", CONTAINER, _ENTRYPOINT, "bash", "-c", bash],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    # listener subprocess 가 실제 ros 구독자로 등록될 때까지 polling 대기.
    # docker exec + ros2 cli 부팅 + daemon discovery 가 sleep 으로는 보장 안 됨.
    deadline = time.monotonic() + _LISTENER_READY_TIMEOUT_S
    listener_ready = False
    while time.monotonic() < deadline:
        if _sigma_sub_count() > baseline:
            listener_ready = True
            break
        time.sleep(_LISTENER_POLL_INTERVAL_S)
    if not listener_ready:
        listener.kill()
        listener.communicate()
        print(
            f"[loop] ✗ listener 구독 등록 실패 (baseline={baseline} 초과 못 함, "
            f"max {_LISTENER_READY_TIMEOUT_S}s)"
        )
        return False, ""

    # 큐된 발화 publish (이제 wrapper 발행은 listener 가 받을 수 있는 시점).
    utterance = ""  # 이번 사이클 발화 — 실행 확인 음성의 echo 에 사용.
    if _pending_publish:
        utterance = _pending_publish.pop(0)
        try:
            _ros_publish(utterance, container=CONTAINER)
        except Exception as exc:  # noqa: BLE001
            listener.kill()
            listener.communicate()
            print(f"[loop] ✗ publish 실패: {exc}")
            return False, ""

    try:
        out, err = listener.communicate(timeout=_RECEIVE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        listener.kill()
        listener.communicate()
        print(f"[loop] ✗ receive_intent timeout {_RECEIVE_TIMEOUT_S}s — wrapper 응답 없음")
        return False, ""

    for line in out.splitlines():
        s = line.strip()
        if s.startswith("data:"):
            payload = s[len("data:"):].strip().strip("'\"")
            try:
                d = json.loads(payload)
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"[loop:debug] payload JSON 파싱 실패: {exc} payload={payload[:120]!r}")
                continue
            sigma = str(d.get("sigma", ""))
            theta = d.get("theta", {}) or {}
            print(f"[loop:debug] receive_intent OK: sigma={sigma!r} is_ask_user={sigma=='ask_user'}")
            if sigma == "ask_user":
                return True, str(theta.get("question", "확인이 필요합니다."))
            # EXECUTE — 실행 음성은 여기서 내지 않는다(무음). 실제 수락/거부 음성은
            # sigma_bridge 가 *실제 처분*(정상 이동 / 회피영역 projection / hover)
            # 기반으로 /intent/speech_out 에 발행하고, run_stt --loop 가 동반 가동한
            # tts_pipeline(speech_out 구독)이 say 한다. 의도 수락 시점에 "갈게요"로
            # 단정하던 문제 해소(안전 계층이 막아도 실제 처분과 일치).
            return False, ""
    # 수신 실패 — listener 출력에 data: 라인 미발견.
    print(
        f"[loop:debug] receive_intent: data 라인 미발견. "
        f"stdout({len(out)} chars)={out[:200]!r} "
        f"stderr({len(err)} chars)={err[:200]!r}"
    )
    return False, ""  # 수신 실패 = 명확 취급(EXECUTE) — Tier 1/2 가 검증


def _hover() -> None:
    """TIMEOUT_HOVER 안전 처분 — return_to_dock 발행 (sigma_bridge 가 hover 처리).

    msg 안 double quote 가 bash outer double quote 와 충돌해 yaml parse 깨지는
    문제 회피 — publish_utterance 와 동일하게 env var 로 전달 + yaml single-quoted
    scalar (outer bash double quote 안 literal single quote 가 yaml 단일 quote 로
    해석되어 inner double quote 보존).
    """
    msg = '{"sigma":"return_to_dock","theta":{},"c":0.0}'
    bash = (
        f"{_SETUP} && "
        f"ros2 topic pub --once /intent/llm_sigma_raw std_msgs/msg/String "
        f"\"data: '$_SIGMA_RAW'\""
    )
    subprocess.run(
        ["docker", "exec", "-e", f"_SIGMA_RAW={msg}",
         CONTAINER, _ENTRYPOINT, "bash", "-c", bash],
        capture_output=True, text=True, timeout=15.0,
    )
    print("[loop] ⚠️ 명료화 한도 초과 → hover/return_to_dock (L4 안전)")


def main() -> int:
    mode = STT_MODE.lower()
    if mode == "mic":
        capture = _make_mic_capture()
    elif mode == "stdin":
        capture = _capture_stdin
    else:
        print(f"[loop] STT_MODE={STT_MODE!r} 무효 — 'stdin' | 'mic' 중 하나", file=sys.stderr)
        return 2

    tts_voice = voice_for_lang(VOICE_LANG)  # "auto" | "Yuna" | "Samantha"
    io = LoopIO(
        capture_utterance=capture,
        publish_intent_input=_publish_utterance,
        receive_intent=_receive_intent,
        speak=lambda q: (print(f"[loop] 🔊 {q}"), say_speak(q, voice=tts_voice)),
        hover=_hover,
    )
    print(
        f"[loop] 명료화 루프 시작 (container={CONTAINER}, lang={VOICE_LANG}, "
        f"tts_voice={tts_voice}, stt_mode={mode})"
    )
    print("[loop] Ctrl+C 로 종료. 매 사이클 = 한 명령 (필요 시 명료화 라운드 포함).")

    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"\n[loop] ━━━ 사이클 {cycle} ━━━")
            # 매 사이클 큐 정리 — 이전 사이클 잔여물 제거 (안전).
            _pending_publish.clear()
            result = run_clarification_loop(
                io, LoopConfig(max_turns=LOOP_MAX_TURNS, timeout_s=LOOP_TIMEOUT_S)
            )
            print(
                f"[loop] 사이클 {cycle} 종료: {result.outcome.value} "
                f"(turns={result.turns}, {result.elapsed_s:.1f}s) — "
                f"발화 {result.transcript}"
            )
    except KeyboardInterrupt:
        print(f"\n[loop] Ctrl+C — 종료 (총 {cycle} 사이클).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
