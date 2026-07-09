"""A4-2 — gate_node 가 사용하는 세션 누적 상태 + transition.

cmsm-proof §9.4 게이트 결정의 입력 (sigma_prev, theta_prev, Activity, GateState) 을
*세션 누적 상태*로 묶고, ROS 2 토픽 이벤트에 대한 transition 메서드를 제공.

PR #54 review M-contract 정립:

- **M1** — `activity` 는 mutual exclusive `Activity` Enum (contradicts.py).
- **M2** — `confirm_fired_at` 는 `time.monotonic()` 기준 시각. `on_confirm()` 으로
  set, `on_user_response()` 또는 ACCEPT 직후 (`on_accept()`) 로 clear.
  `to_gate_state()` 가 호출 시점 elapsed 를 계산해 `GateState` 에 채움.
- **M3** — `n_sc` 는 ACCEPT 직후 0 으로 reset (`on_accept()` 안). self-correction
  이벤트는 *ACCEPT 이전의 REJECT/CONFIRM 사이클* 에서만 누적.
- **M4** (별 PR) — flake8 line length 99.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from tier2_gate._geom import l2
from tier2_gate.contradicts import Activity
from tier2_gate.specs import GateState
from tier2_gate.thresholds import Thresholds


@dataclass
class GateSession:
    """gate_node lifecycle 와 같이 살아남는 세션 누적 상태.

    내부 transition 은 모두 `on_*` 메서드로 응집. 외부 직접 set 은 *센서 갱신*
    (drone_pos_enu, battery_pct 등) 에 한정.
    """

    # --- M1: contradicts 평가 입력 ---
    sigma_prev: str | None = None
    theta_prev: Mapping[str, Any] | None = None
    activity: Activity = Activity.IDLE

    # --- M3: 사용자 자기수정 카운트 (Φ_8) ---
    n_sc: int = 0

    # --- M2: confirm 보류 시각 (Φ_9) ---
    confirm_fired_at: float | None = None

    # --- Tier 1 / FCU 센서 입력 (gate_node 가 토픽으로부터 갱신) ---
    drone_pos_enu: tuple[float, float, float] | None = None
    dock_pos_enu: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_poses: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    settle_started_at: float | None = None

    battery_pct: float = 100.0
    link_lost: bool = False
    tier1_active: bool = True
    user_confirmed: bool = False

    # ------------------------------------------------------------------
    # to_gate_state — 누적 상태 → Φ 평가 입력
    # ------------------------------------------------------------------

    def to_gate_state(self, *, now: float | None = None) -> GateState:
        """현재 누적 상태를 결정 함수 입력 `GateState` 로 사상.

        confirm_pending_elapsed_s = now - confirm_fired_at (둘 다 monotonic).
        """
        now_ = now if now is not None else time.monotonic()
        elapsed: float | None = None
        if self.confirm_fired_at is not None:
            elapsed = now_ - self.confirm_fired_at
        return GateState(
            battery_pct=self.battery_pct,
            link_lost=self.link_lost,
            tier1_active=self.tier1_active,
            user_confirmed=self.user_confirmed,
            n_sc=self.n_sc,
            confirm_pending_elapsed_s=elapsed,
        )

    # ------------------------------------------------------------------
    # ACCEPT 직후 transition (M3 reset + activity 전이)
    # ------------------------------------------------------------------

    def on_accept(self, sigma: str, theta: Mapping[str, Any]) -> None:
        """게이트가 ACCEPT 한 직후 호출 — n_sc reset + activity 전이 + σ_prev 갱신.

        ACCEPT 가 새 task 의 시작이므로 self-correction 카운트는 0 으로 reset,
        confirm 보류도 clear, user_confirmed 도 단발성이라 clear.
        """
        self.sigma_prev = sigma
        self.theta_prev = dict(theta)
        self.n_sc = 0
        self.user_confirmed = False
        self.confirm_fired_at = None

        # activity 전이 — ADR-0019 D3 의 in-progress 상태 시작.
        if sigma == 'inspect':
            self.activity = Activity.INSPECT
            self.settle_started_at = None
        elif sigma == 'return_to_dock':
            self.activity = Activity.RETURN
            self.settle_started_at = None
        elif sigma == 'emergency_land':
            # 비상 동작 — 진행 중이던 inspect/return 을 *강제 종료* (ADR-0019 D2
            # "emergency_land 후 새 명령은 contradicts 아님" 의미와 정합 —
            # in-progress 술어가 모두 False 가 되어 후속 명령에 C3/C5/C6/C7 미발동).
            self.activity = Activity.IDLE
            self.settle_started_at = None
        # move_to / ask_user 는 activity 보존 (in-progress 시작 안 함).

    # ------------------------------------------------------------------
    # CONFIRM 직후 / ask_user 응답 / 자기수정 — 단순 transition
    # ------------------------------------------------------------------

    def on_confirm(self, *, now: float | None = None) -> None:
        """게이트가 CONFIRM 한 직후 — Φ_9 타이머 시작."""
        self.confirm_fired_at = now if now is not None else time.monotonic()

    def on_user_response(self, accepted: bool) -> None:
        """ask_user 응답 도착 — 타이머 clear, user_confirmed 갱신."""
        self.confirm_fired_at = None
        self.user_confirmed = accepted

    def on_self_correction(self) -> None:
        """사용자 자기수정 이벤트 (ADR-0017 D2) — n_sc 증가 (Φ_8 입력)."""
        self.n_sc += 1

    # ------------------------------------------------------------------
    # ADR-0019 D3 — drone_pos_enu 기반 in-progress 자동 종료 판정
    # ------------------------------------------------------------------

    def update_activity_progress(
        self, *, thresholds: Thresholds, now: float | None = None
    ) -> None:
        """매 Timer tick 마다 호출 — activity 종료 조건 검사 후 IDLE 전이.

        INSPECT: viewpoint 도달 (`||drone - target|| < eps_vp`) 후 `tau_settle` 유지 → IDLE.
        RETURN:  도크 도달 (`||drone - dock|| < eps_dock`) → IDLE.
        drone_pos_enu 없으면 no-op (센서 입력 대기).
        """
        if self.drone_pos_enu is None or self.activity == Activity.IDLE:
            return
        now_ = now if now is not None else time.monotonic()

        if self.activity == Activity.INSPECT:
            target = self._inspect_target_pose()
            if target is None:
                return
            d = l2(self.drone_pos_enu, target)
            if d < thresholds.eps_vp:
                if self.settle_started_at is None:
                    self.settle_started_at = now_
                elif now_ - self.settle_started_at >= thresholds.tau_settle:
                    self.activity = Activity.IDLE
                    self.settle_started_at = None
            else:
                # viewpoint 벗어남 — settle 타이머 재시작.
                self.settle_started_at = None

        elif self.activity == Activity.RETURN:
            d = l2(self.drone_pos_enu, self.dock_pos_enu)
            if d < thresholds.eps_dock:
                self.activity = Activity.IDLE

    def _inspect_target_pose(self) -> tuple[float, float, float] | None:
        """현재 INSPECT 의 viewpoint 좌표 — target_poses 에서 조회.

        target_poses 는 launch 파라미터로 주입 (known_objects 별 ENU 좌표).
        viewpoint enum (overview/close/top) 은 1차 시안에선 무시 — 같은 좌표 사용.
        후속 PR 에서 viewpoint 별 좌표 분리 가능.
        """
        if self.theta_prev is None:
            return None
        target_id = self.theta_prev.get('target_id')
        if not isinstance(target_id, str):
            return None
        return self.target_poses.get(target_id)
