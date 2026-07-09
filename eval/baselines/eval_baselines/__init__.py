"""eval_baselines — paper §C 6-way ablation baselines (B0·B1a·B1b·B2·B3·B4).

ADR-0025 D3 격자의 6 baseline 정의 잠금 (amendment 19 — B1→B1a/B1b 분리). 각
baseline = (tier1_mode, context_aug, tier2_enabled) 3 축 조합. tier1_filter 측
*안전 필터 자체* 는 b0/b1/b1_max/b2 모드 구현 보유
([safety/tier1/tier1_filter/filter_node.py](../../../safety/tier1/tier1_filter/filter_node.py))
— 본 패키지는 *trial 수준 오케스트레이션* 잠금 (intent layer 구성 · context
augmentation · Tier 2 게이트 활성화).

baseline 모듈:
  b0_passthrough   — B0  (필터 없음)
  b1a_static_rmin  — B1a (정적 $r_\\text{min}$, 효율 baseline)
  b1b_static_rmax  — B1b (정적 $r_\\text{max}$, 안전 baseline)
  b2_modulated     — B2  (신뢰도 변조)
  b3_context_aug   — B3  (B2 + context augmentation)
  b4_full_loop     — B4  (B3 + Tier 2 게이트)
"""
