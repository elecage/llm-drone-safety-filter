"""ADR-0013 D4·D5 + ADR-0019 D4 — 운용 임계 (1차 시안).

paper §C 민감도 분석 대상. 변경 시 두 ADR과 cmsm-proof §9.3 본문도 같이 갱신.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    """게이트 결정에 쓰이는 운용 임계 묶음 — 단일 진실원."""

    # ADR-0013 D4 — 신뢰도 임계 (Φ_4 자동 거부 + confirm 구간 경계).
    c_lo: float = 0.4
    c_hi: float = 0.7

    # ADR-0013 D5 — 운영 임계.
    B_rtl: float = 30.0    # 배터리 RTL 트리거 [%], Φ_5.
    T_link: float = 3.0    # 링크 손실 대응 시간 [s], Φ_6.
    N_sc: int = 3          # 자기수정 임계 [count], Φ_8.
    T_resp: float = 30.0   # confirm 응답 timeout [s], Φ_9.

    # ADR-0019 D4 — contradicts 술어 임계.
    # (D_cancel 제거 — ADR-0049 D6: C1이 의미 인자 판(target 변경·방향 반전)으로
    #  전환돼 위치 거리 임계 불요.)
    eps_vp: float = 0.1       # inspect viewpoint 도달 허용 [m], C3·C4·C5.
    tau_settle: float = 1.0   # inspect-in-progress 종료 안정 시간 [s].
    eps_dock: float = 0.2     # return_to_dock 도달 허용 [m], C6·C7.

    def __post_init__(self) -> None:
        assert 0.0 <= self.c_lo <= self.c_hi <= 1.0, (
            f'c_lo={self.c_lo}, c_hi={self.c_hi} 조건 위반 '
            '(0 ≤ c_lo ≤ c_hi ≤ 1, ADR-0013 D4)'
        )
        assert self.N_sc >= 1, f'N_sc={self.N_sc} ≥ 1 강제 (ADR-0013 D5)'
        assert self.T_resp > 0, f'T_resp={self.T_resp} > 0'
        assert self.T_link > 0, f'T_link={self.T_link} > 0'
        assert self.eps_vp > 0, f'eps_vp={self.eps_vp} > 0'
        assert self.eps_dock > 0, f'eps_dock={self.eps_dock} > 0'
        assert self.tau_settle > 0, f'tau_settle={self.tau_settle} > 0'
        assert 0.0 <= self.B_rtl <= 100.0, f'B_rtl={self.B_rtl} ∈ [0, 100]'


DEFAULT = Thresholds()
"""ADR-0013 D4·D5 + ADR-0019 D4의 1차 시안 값."""
