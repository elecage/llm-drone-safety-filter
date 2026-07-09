"""eval_runner.bag_integrity 단위 테스트 — bag 무결성 판정 + run 집계 스캔.

세션 34 전체 리뷰 P2 후속 — bag 기록 중 실패 trial 의 조용한 제외(silent drop)
방지. host venv pytest (rosbag2 불요 — metadata.yaml 텍스트 fixture).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from eval_baselines.schemas import BaselineMode
from eval_faults.fault_scenario import (
    FAULT_CHANNEL_FAULTED_TOPIC,
    FaultChannel,
)

from eval_runner.bag_integrity import (
    BAG_STATUS_COMPLETE,
    BAG_STATUS_FAULT_NOT_APPLICABLE,
    BAG_STATUS_INCOMPLETE,
    BAG_STATUS_UNKNOWN,
    ROSBAG_METADATA_FILENAME,
    check_bag_integrity,
    find_rosbag_metadata,
    format_bag_status_scan,
    rejudge_trial_bag_statuses,
    required_min_counts,
    scan_trial_bag_statuses,
)


# -------------------------------------------------------------------- fixtures


def _write_rosbag_metadata(
    bag_dir: Path,
    topic_counts: dict,
    nested_name: str = '',
) -> Path:
    """rosbag2 metadata.yaml fixture — Humble 측 실 스키마 골격 모사."""
    target_dir = bag_dir / nested_name if nested_name else bag_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    entries = '\n'.join(
        (
            '    - topic_metadata:\n'
            f'        name: {name}\n'
            '        type: std_msgs/msg/String\n'
            '        serialization_format: cdr\n'
            f'      message_count: {count}'
        )
        for name, count in topic_counts.items()
    )
    text = (
        'rosbag2_bagfile_information:\n'
        '  version: 5\n'
        '  storage_identifier: sqlite3\n'
        f'  message_count: {sum(topic_counts.values())}\n'
        '  topics_with_message_count:\n'
        f'{entries}\n'
    )
    path = target_dir / ROSBAG_METADATA_FILENAME
    path.write_text(text, encoding='utf-8')
    return path


def _healthy_counts() -> dict:
    return {
        '/fmu/out/vehicle_local_position_v1': 100,
        '/cmd/trajectory_setpoint_safe': 200,
        '/intent/estimator/report': 50,
        '/tier2/decision': 3,
        '/clock': 1000,
    }


def _write_trial_meta(root: Path, trial_id: str, body: str) -> Path:
    d = root / trial_id
    d.mkdir(parents=True)
    p = d / 'trial_meta.yaml'
    p.write_text(body, encoding='utf-8')
    return p


def _write_llm_jsonl(
    trial_dir: Path,
    skills_per_record,
    name: str = 'cloud_llm_gpt-4o.jsonl',
) -> Path:
    """LLM TRIAL_LOG JSONL fixture — intent_llm `_write_trial_log` 골격 모사."""
    trial_dir.mkdir(parents=True, exist_ok=True)
    path = trial_dir / name
    lines = [
        json.dumps({
            'timestamp': 1.0 + i,
            'model': 'gpt-4o',
            'skills': skills,
            'rho': 1.0,
            'entropy': 0.0,
            'logprob': None,
            'inference_latency_s': 0.5,
        }, ensure_ascii=False)
        for i, skills in enumerate(skills_per_record)
    ]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _sigma_absent_counts() -> dict:
    """S5×hallucination 명료화 후퇴 bag 모사 — σ_raw 0(주입 표면 닫힘),
    나머지 필수 토픽(estimator 생존 포함)은 건강."""
    counts = _healthy_counts()
    # /intent/llm_sigma_raw (dispatch = hallucination _faulted) 부재.
    counts.pop('/intent/llm_sigma_raw', None)
    return counts


# -------------------------------------------------------------------- required_min_counts


class TestRequiredMinCounts:
    def test_b0_b1a_b1b_no_estimator_requirement(self) -> None:
        for mode in (BaselineMode.B0, BaselineMode.B1A, BaselineMode.B1B):
            counts = required_min_counts(mode)
            assert counts == {
                '/fmu/out/vehicle_local_position_v1': 1,
                '/cmd/trajectory_setpoint_safe': 2,
            }

    def test_b2_plus_requires_estimator_report(self) -> None:
        for mode in (BaselineMode.B2, BaselineMode.B3, BaselineMode.B4):
            counts = required_min_counts(mode)
            assert counts['/intent/estimator/report'] == 1

    def test_setpoint_min_two_matches_baginputs(self) -> None:
        """BagInputs / extract_loop_periods 측 n >= 2 강제 정합."""
        assert required_min_counts(BaselineMode.B0)[
            '/cmd/trajectory_setpoint_safe'
        ] == 2

    def test_no_fault_channel_no_faulted_requirement(self) -> None:
        """fault_channel=None (back-compat) 측 _faulted 토픽 요구 없음."""
        counts = required_min_counts(BaselineMode.B0)
        assert all('_faulted' not in t for t in counts)

    def test_none_channel_no_faulted_requirement(self) -> None:
        """NONE 채널 측 변형 출력 없음 → _faulted 요구 없음 (baseline trial)."""
        counts = required_min_counts(BaselineMode.B0, FaultChannel.NONE)
        assert all('_faulted' not in t for t in counts)
        assert FaultChannel.NONE not in FAULT_CHANNEL_FAULTED_TOPIC

    def test_active_fault_channel_requires_faulted_topic(self) -> None:
        """활성 fault 채널(NONE 제외) 측 _faulted 출력 ≥1 요구 — 미주입 검출."""
        for channel, topic in FAULT_CHANNEL_FAULTED_TOPIC.items():
            counts = required_min_counts(BaselineMode.B0, channel)
            assert counts[topic] == 1


# -------------------------------------------------------------------- find_rosbag_metadata


class TestFindRosbagMetadata:
    def test_direct_metadata_found(self, tmp_path: Path) -> None:
        p = _write_rosbag_metadata(tmp_path, _healthy_counts())
        assert find_rosbag_metadata(tmp_path) == p

    def test_nested_single_found(self, tmp_path: Path) -> None:
        p = _write_rosbag_metadata(tmp_path, _healthy_counts(), nested_name='bag0')
        assert find_rosbag_metadata(tmp_path) == p

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert find_rosbag_metadata(tmp_path) is None

    def test_nested_multiple_ambiguous_returns_none(self, tmp_path: Path) -> None:
        _write_rosbag_metadata(tmp_path, _healthy_counts(), nested_name='bag0')
        _write_rosbag_metadata(tmp_path, _healthy_counts(), nested_name='bag1')
        assert find_rosbag_metadata(tmp_path) is None

    def test_direct_wins_over_nested(self, tmp_path: Path) -> None:
        nested = _write_rosbag_metadata(
            tmp_path, _healthy_counts(), nested_name='bag0',
        )
        direct = _write_rosbag_metadata(tmp_path, _healthy_counts())
        found = find_rosbag_metadata(tmp_path)
        assert found == direct
        assert found != nested


# -------------------------------------------------------------------- check_bag_integrity


class TestCheckBagIntegrity:
    def test_healthy_bag_complete(self, tmp_path: Path) -> None:
        _write_rosbag_metadata(tmp_path, _healthy_counts())
        result = check_bag_integrity(tmp_path, BaselineMode.B2)
        assert result.status == BAG_STATUS_COMPLETE
        assert result.reasons == ()

    def test_missing_metadata_incomplete(self, tmp_path: Path) -> None:
        """metadata.yaml 부재 = record 비정상 종료 추정 → incomplete."""
        result = check_bag_integrity(tmp_path, BaselineMode.B0)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('metadata.yaml' in r for r in result.reasons)

    def test_missing_required_topic_incomplete(self, tmp_path: Path) -> None:
        counts = _healthy_counts()
        del counts['/fmu/out/vehicle_local_position_v1']
        _write_rosbag_metadata(tmp_path, counts)
        result = check_bag_integrity(tmp_path, BaselineMode.B0)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('/fmu/out/vehicle_local_position_v1' in r for r in result.reasons)

    def test_setpoint_below_min_incomplete(self, tmp_path: Path) -> None:
        counts = _healthy_counts()
        counts['/cmd/trajectory_setpoint_safe'] = 1  # < 2
        _write_rosbag_metadata(tmp_path, counts)
        result = check_bag_integrity(tmp_path, BaselineMode.B0)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('trajectory_setpoint_safe' in r for r in result.reasons)

    def test_b2_missing_estimator_report_incomplete(self, tmp_path: Path) -> None:
        counts = _healthy_counts()
        counts['/intent/estimator/report'] = 0
        _write_rosbag_metadata(tmp_path, counts)
        result = check_bag_integrity(tmp_path, BaselineMode.B2)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('/intent/estimator/report' in r for r in result.reasons)

    def test_b0_ignores_missing_estimator_report(self, tmp_path: Path) -> None:
        """B0/B1 측 estimator report 미요구 — 정적 r_max (빈 list OK)."""
        counts = _healthy_counts()
        counts['/intent/estimator/report'] = 0
        _write_rosbag_metadata(tmp_path, counts)
        result = check_bag_integrity(tmp_path, BaselineMode.B0)
        assert result.status == BAG_STATUS_COMPLETE

    def test_multiple_failures_all_reported(self, tmp_path: Path) -> None:
        counts = {
            '/fmu/out/vehicle_local_position_v1': 0,
            '/cmd/trajectory_setpoint_safe': 0,
            '/intent/estimator/report': 0,
        }
        _write_rosbag_metadata(tmp_path, counts)
        # B2 — position·setpoint·estimator 3 요구(B4 는 /tier2/decision 추가 요구라
        # 본 3-실패 의도와 무관하게 4개 됨, 세션 49 tier2 통합).
        result = check_bag_integrity(tmp_path, BaselineMode.B2)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert len(result.reasons) == 3

    def test_corrupt_yaml_incomplete(self, tmp_path: Path) -> None:
        (tmp_path / ROSBAG_METADATA_FILENAME).write_text(
            'rosbag2_bagfile_information: [unclosed', encoding='utf-8',
        )
        result = check_bag_integrity(tmp_path, BaselineMode.B0)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('parse 실패' in r for r in result.reasons)

    def test_missing_root_key_incomplete(self, tmp_path: Path) -> None:
        (tmp_path / ROSBAG_METADATA_FILENAME).write_text(
            'not_rosbag: {}\n', encoding='utf-8',
        )
        result = check_bag_integrity(tmp_path, BaselineMode.B0)
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_nested_bag_dir_checked(self, tmp_path: Path) -> None:
        """`ros2 bag record -o <trial_id>` 측 1-단계 하위 출력도 판정."""
        _write_rosbag_metadata(tmp_path, _healthy_counts(), nested_name='trial0')
        result = check_bag_integrity(tmp_path, BaselineMode.B2)
        assert result.status == BAG_STATUS_COMPLETE

    def test_fault_declared_but_not_injected_incomplete(self, tmp_path: Path) -> None:
        """fault×sigma 비호환 → injector 조용한 no-op → _faulted 0 sample →
        'incomplete' (격자 smoke 2026-06-14 노출 버그의 회귀 가드)."""
        # healthy bag 이지만 hallucination _faulted 토픽 부재 (injector no-op).
        _write_rosbag_metadata(tmp_path, _healthy_counts())
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE
        faulted = FAULT_CHANNEL_FAULTED_TOPIC[FaultChannel.HALLUCINATION]
        assert any(faulted in r for r in result.reasons)

    def test_fault_injected_complete(self, tmp_path: Path) -> None:
        """_faulted 토픽 ≥1 sample (정상 주입) 측 complete."""
        counts = _healthy_counts()
        counts[FAULT_CHANNEL_FAULTED_TOPIC[FaultChannel.HALLUCINATION]] = 5
        _write_rosbag_metadata(tmp_path, counts)
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_COMPLETE

    def test_none_channel_no_faulted_topic_complete(self, tmp_path: Path) -> None:
        """NONE 채널(baseline) 측 _faulted 요구 없음 — healthy bag 그대로 complete."""
        _write_rosbag_metadata(tmp_path, _healthy_counts())
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.NONE,
        )
        assert result.status == BAG_STATUS_COMPLETE


# ----------------------------------------------------- B4 게이트 reject 처리 (세션 53)


_TOPIC_DISPATCH = '/intent/llm_sigma_raw'


def _b4_counts(*, dispatch: int, setpoint: int) -> dict:
    """B4 trial metadata fixture — dispatch(accept) 수·setpoint 수 가변.

    B4 필수 토픽: position·estimator·decision(게이트 작동)·clock + dispatch·setpoint.
    """
    return {
        '/fmu/out/vehicle_local_position_v1': 100,
        '/cmd/trajectory_setpoint_safe': setpoint,
        '/intent/estimator/report': 50,
        '/tier2/decision': 5,
        _TOPIC_DISPATCH: dispatch,
        '/clock': 1000,
    }


class TestRequiredMinCountsB4GateDispatch:
    def test_b4_reject_all_waives_setpoint(self) -> None:
        """게이트가 한 번도 accept 안 함(dispatch 0) → setpoint 요구 면제 (C3 정상)."""
        counts = required_min_counts(BaselineMode.B4, gate_dispatched=False)
        assert '/cmd/trajectory_setpoint_safe' not in counts
        # 게이트 작동(/tier2/decision)·estimator·position 은 여전히 요구.
        assert counts['/tier2/decision'] == 1
        assert counts['/intent/estimator/report'] == 1
        assert counts['/fmu/out/vehicle_local_position_v1'] == 1

    def test_b4_accept_requires_setpoint(self) -> None:
        """게이트가 ≥1 accept(dispatch) → actuation 흘렀어야 → setpoint ≥2 요구."""
        counts = required_min_counts(BaselineMode.B4, gate_dispatched=True)
        assert counts['/cmd/trajectory_setpoint_safe'] == 2

    def test_non_b4_ignores_gate_dispatched_flag(self) -> None:
        """B4 외 baseline 은 gate_dispatched 무관 — setpoint 항상 요구."""
        for mode in (BaselineMode.B0, BaselineMode.B2, BaselineMode.B3):
            counts = required_min_counts(mode, gate_dispatched=False)
            assert counts['/cmd/trajectory_setpoint_safe'] == 2


class TestCheckBagIntegrityB4GateReject:
    def test_reject_all_zero_setpoint_complete(self, tmp_path: Path) -> None:
        """게이트 전부 reject(dispatch 0)·setpoint 0 → valid-complete (C3 정상 동작).

        세션 53 B4 게이트 sim e2e 가 드러낸 핵심: reject→actuation 차단은 정상이지
        기록 실패가 아니다 → resume 무한 재실행 차단."""
        _write_rosbag_metadata(tmp_path, _b4_counts(dispatch=0, setpoint=0))
        result = check_bag_integrity(tmp_path, BaselineMode.B4)
        assert result.status == BAG_STATUS_COMPLETE
        assert result.reasons == ()

    def test_accept_with_setpoint_complete(self, tmp_path: Path) -> None:
        """게이트 accept(dispatch≥1)·setpoint≥2 → 정상 actuation → complete."""
        _write_rosbag_metadata(tmp_path, _b4_counts(dispatch=5, setpoint=200))
        result = check_bag_integrity(tmp_path, BaselineMode.B4)
        assert result.status == BAG_STATUS_COMPLETE

    def test_accept_but_no_setpoint_incomplete(self, tmp_path: Path) -> None:
        """게이트 accept(dispatch≥1)인데 setpoint 부재 → 진짜 incomplete (actuation
        이 흘렀어야 하는데 기록 실패) — reject 면제가 이 경우를 가리지 않음."""
        _write_rosbag_metadata(tmp_path, _b4_counts(dispatch=5, setpoint=0))
        result = check_bag_integrity(tmp_path, BaselineMode.B4)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('trajectory_setpoint_safe' in r for r in result.reasons)

    def test_reject_all_still_requires_decision(self, tmp_path: Path) -> None:
        """reject-all 면제는 setpoint 만 — 게이트 크래시(/tier2/decision 0)는 여전히
        incomplete (게이트 작동 자체는 별도 보장)."""
        counts = _b4_counts(dispatch=0, setpoint=0)
        counts['/tier2/decision'] = 0
        _write_rosbag_metadata(tmp_path, counts)
        result = check_bag_integrity(tmp_path, BaselineMode.B4)
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('/tier2/decision' in r for r in result.reasons)


# ------------------------------------ 제3 범주 fault_not_applicable (ADR-0037 amend)


class TestFaultNotApplicable:
    """gpt-4o × S5 × hallucination 명료화 후퇴 — σ 미발행은 하니스 결함이 아닌
    '주입 미정의' (ADR-0037 D1 확장 A′). 판정은 `_is_fault_not_applicable`
    단일 지점 (bag σ_raw=0 + estimator 생존 + JSONL 전 호출 ask_user)."""

    def test_sigma_zero_all_ask_user_jsonl_fault_not_applicable(
        self, tmp_path: Path,
    ) -> None:
        """(a) σ=0 + 전 레코드 ask_user JSONL → fault_not_applicable."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        _write_llm_jsonl(tmp_path, [['ask_user'], ['ask_user'], ['ask_user']])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_FAULT_NOT_APPLICABLE
        assert any('명료화 후퇴' in r for r in result.reasons)

    def test_sigma_zero_without_jsonl_stays_incomplete(
        self, tmp_path: Path,
    ) -> None:
        """(b) σ=0 + JSONL 부재(legacy) → 판정 불가, 보수적 incomplete 유지."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_sigma_zero_with_non_ask_user_skill_stays_incomplete(
        self, tmp_path: Path,
    ) -> None:
        """(c) σ=0 인데 JSONL 에 ask_user 외 skill 포함 — σ 미발행의 다른 원인
        가능성 → 그대로 incomplete (미주입 검출 유지)."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        _write_llm_jsonl(tmp_path, [['ask_user'], ['goto_waypoint']])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_mixed_skills_in_single_record_stays_incomplete(
        self, tmp_path: Path,
    ) -> None:
        """동수(1:1) 샘플 — 엄격 다수결 미달 → 불인정 (incomplete 유지)."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        _write_llm_jsonl(tmp_path, [['ask_user', 'inspect']])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_majority_ask_user_qualifies(self, tmp_path: Path) -> None:
        """자기일관성 샘플 다수결 ask_user (2/3) — wrapper 채택이 ask_user
        (σ 미발행)이므로 명료화 후퇴 증거 인정 (세션 62 gpt-4o 혼합 샘플)."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        _write_llm_jsonl(
            tmp_path,
            [['inspect', 'ask_user', 'ask_user'], ['ask_user'] * 3],
        )
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_FAULT_NOT_APPLICABLE

    def test_b4_tier2_decision_zero_signature_qualifies(
        self, tmp_path: Path,
    ) -> None:
        """B4+hallucination: required 가 σ-raw 대신 /tier2/decision ≥1 을
        요구 — 명령이 게이트에 도달하지 않아 decision 0 인 것도 같은 후퇴
        서명 → fault_not_applicable (세션 62 gpt-4o B4 사례)."""
        counts = _sigma_absent_counts()
        counts['/tier2/decision'] = 0
        _write_rosbag_metadata(tmp_path, counts)
        _write_llm_jsonl(tmp_path, [['ask_user'] * 3])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B4, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_FAULT_NOT_APPLICABLE

    def test_empty_jsonl_stays_incomplete(self, tmp_path: Path) -> None:
        """레코드 0 (빈 JSONL) — 증거 없음 → incomplete."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        _write_llm_jsonl(tmp_path, [])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_other_shortfall_alongside_stays_incomplete(
        self, tmp_path: Path,
    ) -> None:
        """다른 미달 사유(setpoint 부족) 동반 시 fault_not_applicable 아님."""
        counts = _sigma_absent_counts()
        counts['/cmd/trajectory_setpoint_safe'] = 1  # < 2
        _write_rosbag_metadata(tmp_path, counts)
        _write_llm_jsonl(tmp_path, [['ask_user']])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE
        assert any('trajectory_setpoint_safe' in r for r in result.reasons)

    def test_estimator_dead_stays_incomplete(self, tmp_path: Path) -> None:
        """estimator report 0 — 의도 스택 생존 증거 없음 → incomplete."""
        counts = _sigma_absent_counts()
        counts['/intent/estimator/report'] = 0
        _write_rosbag_metadata(tmp_path, counts)
        _write_llm_jsonl(tmp_path, [['ask_user']])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_non_dispatch_faulted_channel_stays_incomplete(
        self, tmp_path: Path,
    ) -> None:
        """faulted 토픽이 dispatch 계열 아님(adversarial 등) → 제3 범주 비대상."""
        _write_rosbag_metadata(tmp_path, _healthy_counts())  # _faulted 부재
        _write_llm_jsonl(tmp_path, [['ask_user']])
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.ADVERSARIAL,
        )
        assert result.status == BAG_STATUS_INCOMPLETE

    def test_edge_llm_jsonl_also_accepted(self, tmp_path: Path) -> None:
        """edge backbone JSONL(edge_llm_*.jsonl)도 같은 glob 으로 판독."""
        _write_rosbag_metadata(tmp_path, _sigma_absent_counts())
        _write_llm_jsonl(
            tmp_path, [['ask_user']], name='edge_llm_gemma4_e4b.jsonl',
        )
        result = check_bag_integrity(
            tmp_path, BaselineMode.B2, FaultChannel.HALLUCINATION,
        )
        assert result.status == BAG_STATUS_FAULT_NOT_APPLICABLE


# -------------------------------------------------------------------- scan


class TestScanTrialBagStatuses:
    def test_empty_root(self, tmp_path: Path) -> None:
        scan = scan_trial_bag_statuses(tmp_path, 'gemma-4-e4b')
        assert scan.complete_ids == ()
        assert scan.incomplete_ids == ()
        assert scan.unknown_ids == ()

    def test_mixed_statuses_classified(self, tmp_path: Path) -> None:
        root = tmp_path / 'bb'
        _write_trial_meta(root, 't_complete', 'bag_status: complete\nscenario: S5\n')
        _write_trial_meta(root, 't_incomplete', 'bag_status: incomplete\n')
        _write_trial_meta(root, 't_legacy', 'scenario: S5\n')  # bag_status 부재
        _write_trial_meta(root, 't_na', 'bag_status: fault_not_applicable\n')
        scan = scan_trial_bag_statuses(tmp_path, 'bb')
        assert scan.complete_ids == ('t_complete',)
        assert scan.incomplete_ids == ('t_incomplete',)
        assert scan.unknown_ids == ('t_legacy',)
        assert scan.fault_not_applicable_ids == ('t_na',)

    def test_corrupt_meta_counted_incomplete(self, tmp_path: Path) -> None:
        root = tmp_path / 'bb'
        _write_trial_meta(root, 't_bad', '{unclosed')
        _write_trial_meta(root, 't_scalar', 'just-a-string')
        scan = scan_trial_bag_statuses(tmp_path, 'bb')
        assert set(scan.incomplete_ids) == {'t_bad', 't_scalar'}

    def test_sorted_by_trial_id(self, tmp_path: Path) -> None:
        root = tmp_path / 'bb'
        for tid in ('t_c', 't_a', 't_b'):
            _write_trial_meta(root, tid, 'bag_status: complete\n')
        scan = scan_trial_bag_statuses(tmp_path, 'bb')
        assert scan.complete_ids == ('t_a', 't_b', 't_c')

    def test_dir_without_meta_excluded(self, tmp_path: Path) -> None:
        """trial_meta.yaml 부재 디렉토리 = 미실행(pending) — 집계 제외."""
        (tmp_path / 'bb' / 't_started').mkdir(parents=True)
        scan = scan_trial_bag_statuses(tmp_path, 'bb')
        assert scan.incomplete_ids == ()
        assert scan.unknown_ids == ()


class TestFormatBagStatusScan:
    def test_counts_in_header(self, tmp_path: Path) -> None:
        root = tmp_path / 'bb'
        _write_trial_meta(root, 't1', 'bag_status: complete\n')
        _write_trial_meta(root, 't2', 'bag_status: incomplete\n')
        _write_trial_meta(root, 't3', 'scenario: S5\n')
        text = format_bag_status_scan(scan_trial_bag_statuses(tmp_path, 'bb'))
        assert '총 3 trial' in text
        assert 'complete 1' in text
        assert 'incomplete 1' in text
        assert 'unknown 1' in text

    def test_incomplete_trial_ids_listed(self, tmp_path: Path) -> None:
        """incomplete trial 측 *trial id 명시* — 조용한 제외 금지."""
        root = tmp_path / 'bb'
        _write_trial_meta(root, 't_dead', 'bag_status: incomplete\n')
        text = format_bag_status_scan(scan_trial_bag_statuses(tmp_path, 'bb'))
        assert 't_dead' in text
        assert '재실행 대상' in text

    def test_unknown_legacy_listed(self, tmp_path: Path) -> None:
        root = tmp_path / 'bb'
        _write_trial_meta(root, 't_old', 'scenario: S5\n')
        text = format_bag_status_scan(scan_trial_bag_statuses(tmp_path, 'bb'))
        assert 't_old' in text
        assert 'legacy' in text

    def test_preview_truncation_reports_count(self, tmp_path: Path) -> None:
        root = tmp_path / 'bb'
        for i in range(5):
            _write_trial_meta(root, f't_{i}', 'bag_status: incomplete\n')
        text = format_bag_status_scan(
            scan_trial_bag_statuses(tmp_path, 'bb'), preview_n=2,
        )
        assert 'incomplete 5' in text
        assert '+3 trial 생략' in text

    def test_fault_not_applicable_counted_and_listed(self, tmp_path: Path) -> None:
        """제3 범주 — 별도 카운트 + trial id 명시 (조용한 제외 금지), '재실행
        대상' 아님 (ADR-0037 amend)."""
        root = tmp_path / 'bb'
        _write_trial_meta(root, 't_na', 'bag_status: fault_not_applicable\n')
        text = format_bag_status_scan(scan_trial_bag_statuses(tmp_path, 'bb'))
        assert 'fault_not_applicable 1' in text
        assert 't_na' in text
        assert '명료화 후퇴로 주입 미정의' in text
        assert '결함 통계 제외' in text
        assert '재실행 대상 아님' in text


# --------------------------------------------------------- rejudge (재분류 경로)


class TestRejudgeTrialBagStatuses:
    _META_INCOMPLETE = (
        'scenario: S5\nbaseline: B2\nfault_class: hallucination\n'
        'fault_variant: target_swap_dangerous\nseed: 7\nwall_clock_s: 42.0\n'
        'bag_status: incomplete\n'
    )

    def _trial_dir(self, tmp_path: Path, backbone: str, trial_id: str) -> Path:
        d = tmp_path / backbone / trial_id
        d.mkdir(parents=True)
        return d

    def test_incomplete_reclassified_to_fault_not_applicable(
        self, tmp_path: Path,
    ) -> None:
        """제3 범주 도입 전 incomplete 로 기록된 trial 이 bag+JSONL 재판정으로
        fault_not_applicable 로 갱신 (다른 키 보존)."""
        d = self._trial_dir(tmp_path, 'gpt-4o', 't_swap')
        (d / 'trial_meta.yaml').write_text(self._META_INCOMPLETE, encoding='utf-8')
        _write_rosbag_metadata(d, _sigma_absent_counts())
        _write_llm_jsonl(d, [['ask_user'], ['ask_user']])

        changes = rejudge_trial_bag_statuses(tmp_path, 'gpt-4o')
        assert changes == (
            ('t_swap', BAG_STATUS_INCOMPLETE, BAG_STATUS_FAULT_NOT_APPLICABLE),
        )
        raw = yaml.safe_load((d / 'trial_meta.yaml').read_text(encoding='utf-8'))
        assert raw['bag_status'] == BAG_STATUS_FAULT_NOT_APPLICABLE
        assert raw['seed'] == 7  # 다른 키 보존
        assert raw['fault_variant'] == 'target_swap_dangerous'

    def test_still_incomplete_left_untouched(self, tmp_path: Path) -> None:
        """재판정 결과가 여전히 incomplete(JSONL 부재) — 무변경·무보고."""
        d = self._trial_dir(tmp_path, 'gpt-4o', 't_dead')
        (d / 'trial_meta.yaml').write_text(self._META_INCOMPLETE, encoding='utf-8')
        _write_rosbag_metadata(d, _sigma_absent_counts())  # JSONL 없음
        changes = rejudge_trial_bag_statuses(tmp_path, 'gpt-4o')
        assert changes == ()
        raw = yaml.safe_load((d / 'trial_meta.yaml').read_text(encoding='utf-8'))
        assert raw['bag_status'] == BAG_STATUS_INCOMPLETE

    def test_complete_trials_not_rejudged(self, tmp_path: Path) -> None:
        """범위 = incomplete 한정 — complete trial 은 재판정·강등하지 않음."""
        d = self._trial_dir(tmp_path, 'gpt-4o', 't_ok')
        (d / 'trial_meta.yaml').write_text(
            self._META_INCOMPLETE.replace(
                'bag_status: incomplete', 'bag_status: complete'),
            encoding='utf-8',
        )
        # bag 자체는 (일부러) 미달 상태 — 그래도 건드리지 않아야 함.
        _write_rosbag_metadata(d, _sigma_absent_counts())
        changes = rejudge_trial_bag_statuses(tmp_path, 'gpt-4o')
        assert changes == ()
        raw = yaml.safe_load((d / 'trial_meta.yaml').read_text(encoding='utf-8'))
        assert raw['bag_status'] == BAG_STATUS_COMPLETE

    def test_unreadable_coords_skipped(self, tmp_path: Path) -> None:
        """baseline/fault_class 판독 불가 meta — 보수적 무변경."""
        d = self._trial_dir(tmp_path, 'gpt-4o', 't_bad')
        (d / 'trial_meta.yaml').write_text(
            'baseline: BX\nfault_class: hallucination\nbag_status: incomplete\n',
            encoding='utf-8',
        )
        assert rejudge_trial_bag_statuses(tmp_path, 'gpt-4o') == ()


# -------------------------------------------------------------------- 상수 정합


class TestStatusConstants:
    def test_metrics_side_allowed_values_superset(self) -> None:
        """read side `TrialMetadata._ALLOWED_BAG_STATUSES` 측 양측 상수 정합."""
        from eval_metrics.schemas import TrialMetadata
        assert set(TrialMetadata._ALLOWED_BAG_STATUSES) == {
            BAG_STATUS_COMPLETE, BAG_STATUS_INCOMPLETE, BAG_STATUS_UNKNOWN,
            BAG_STATUS_FAULT_NOT_APPLICABLE,
        }
