"""eval_runner — paper §C trial 격자 자동화 (ADR-0025 D3 + D5 #12).

ADR-0025 D3 격자 정의:

    N_trial = |scenarios (4)| × |baselines (5)| × |fault_class (5)| × N_episode (10)
            = 4 × 5 × 5 × 10
            = 1,000 trial

본 패키지 = *host venv 측 runner core* — TrialSpec dataclass + 5 차원 deterministic
seed 정책 + 격자 enumeration + ablation chain invariant 자동 검증. ROS 2 launch
composition logic (BaselineConfig 입력 → tier1_filter + intent/llm wrapper +
Tier 2 게이트 + fault injector + rosbag2 record 합성) 은 후속 PR (B7 #12 분할 2/N).

본 PR (B7 #12 분할 1/N) scope:
  - schemas.py — TrialSpec frozen dataclass + trial_id derivation
  - seed_policy.py — 5 차원 sha256 hash → uint32 seed (ROADMAP C25 closure)
  - grid.py — generate_trial_grid() cartesian product → list[TrialSpec]
  - ablation_invariant.py — check_chain_invariant(grid) 자동 검증
  - test/ — 4 모듈 pytest

후속 PR (B7 #12 분할 2/N) scope:
  - launch composition (ROS 2 launch_description 합성 — tier1 + intent/llm wrapper
    + Tier 2 게이트 + injector_node + rosbag2 record)
  - intent/llm wrapper 6 종 (ovd/llm_cloud/llm_edge/vla/classifier/adversarial)
  - trial_meta.yaml 자동 생성 + bag 측 metric pipeline 연결
"""
