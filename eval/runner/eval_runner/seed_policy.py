"""5 차원 deterministic seed 정책 — ROADMAP C25 closure.

[ROADMAP C25](../../../docs/handover/ROADMAP.md#6-backlog--paper-2-위임):
> paper §C trial seed 정책 — scenario × baseline × fault_class × fault_variant
> × episode 5 차원 hash → fault hook 측 ``random.Random(seed)`` 재현성 보장.

[ADR-0025 D3](../../../docs/handover/decisions/0025-paper-c-experiment-protocol.md#d3)
격자 측 1000 trial 모두 *distinct, deterministic, reproducible* seed 필요.
naive approach (예: trial_index ∈ [0, 999]) 의 약점:
  1. 격자 차원 변경 (S3 도입 또는 baseline 추가 등) 측 *seed shift* — 모든
     trial 측 seed 변동 → 재실행 측 결과 변동.
  2. fault hook 측 *동일 fault·다른 trial* 사이 seed 의존성 — random.Random(0)
     ↔ random.Random(1) sequence 측 *시작값 가까움* 측 분포 측면 미세 bias.

본 정책 = SHA-256 기반 5-tuple hash → uint32:
  - 격자 차원 *추가/변경* 측 영향 받는 trial 만 seed 변경 (5-tuple 다르므로).
  - SHA-256 측 *avalanche effect* — 입력 1 bit 변경 측 출력 50 % bit 변경 →
    distinct trial 측 seed 사이 *통계적 독립* 보장.

참고: zlib.crc32 와 비교 측 SHA-256 측 collision 확률 (2**32 출력) 측 *동일*
(uint32 truncation) 이나 *입력 distinctness 보존* 측 SHA-256 측 표준 보장. crc32
측 *짧은 입력* 측 partial collision 가능 → 본 정책 측 SHA-256 채택.
"""

from __future__ import annotations

import hashlib
from typing import Optional


_SEED_SEPARATOR = '\x00'


def derive_trial_seed(
    scenario_id: str,
    baseline_mode: str,
    fault_channel: str,
    fault_variant: Optional[str],
    episode_id: int,
) -> int:
    """5 차원 deterministic seed — SHA-256(5-tuple) → uint32.

    Args:
        scenario_id: 시나리오 식별자 ('S5' | 'S6').
        baseline_mode: BaselineMode value ('b0' ~ 'b4').
        fault_channel: FaultChannel value ('none' | 'hallucination' |
            'adversarial' | 'cognitive_lapse' | 'attribute_mismatch').
        fault_variant: channel-specific variant string 또는 None (channel='none'
            측). None 측 빈 문자열 처리 — 'none' string 측 구별을 위해.
        episode_id: 0 to n_episodes-1.

    Returns:
        uint32 범위 ([0, 2**32 - 1]) seed integer. SHA-256 측 첫 4 byte
        big-endian uint32 truncation.

    Raises:
        TypeError: episode_id 가 int 아님.
        ValueError: episode_id < 0.

    Note:
        본 함수 측 ``random.Random(seed)`` 입력 측 가정 — Python ``random``
        모듈 측 seed 측 *any int* accept 이나 uint32 truncation 측 cross-language
        portability 보장 (예: NumPy ``np.random.default_rng(seed)`` 측 uint32
        seed 측 표준).
    """
    if not isinstance(episode_id, int) or isinstance(episode_id, bool):
        raise TypeError(
            f'episode_id 는 int 여야 함, got {type(episode_id).__name__}'
        )
    if episode_id < 0:
        raise ValueError(f'episode_id={episode_id} 무효 — 0 이상 필수')

    # 5-tuple 측 null-separated string — 차원 사이 boundary 명확화. ASCII NUL
    # (\x00) 측 scenario_id·baseline_mode·channel·variant 측 *어디에도 등장
    # 불가* 측 separator 보장.
    variant_str = '' if fault_variant is None else str(fault_variant)
    parts = [
        str(scenario_id),
        str(baseline_mode),
        str(fault_channel),
        variant_str,
        str(int(episode_id)),
    ]
    payload = _SEED_SEPARATOR.join(parts).encode('utf-8')

    digest = hashlib.sha256(payload).digest()
    # 첫 4 byte big-endian uint32 truncation — SHA-256 측 전체 32 byte 측 *각
    # bit* 통계적 균일 → 어느 4 byte 선택해도 uint32 distribution 동일.
    return int.from_bytes(digest[:4], byteorder='big', signed=False)
