"""cross-launch parameter sanity test (ROADMAP C31).

세 출처의 tier1_filter "effective parameter" 정합을 host venv 에서 검증:

1. `safety/tier1/launch/tier1_b{0,1,1_max,2}.launch.py` — canonical CBF spec
   (ADR-0025 amendment 19 — B1→B1a(b1)/B1b(b1_max)). B1a/B1b/B2 측 user 좌표 +
   r_min 은 `resolve_scenario_params()` 동적 lookup (단일 진실 소스
   [`scenario_params.params`](../../../sim/scenario_params/scenario_params/params.py)).
2. `eval/baselines/launch/b{0..4}_*.launch.py` — paper §C trial launch. tier1
   파라미터를 *literal 하드코딩* (재현성 + tier1_filter declare_parameter default
   drift 회피). 이 하드코딩이 단일 진실 소스와 *조용히 divergence* 할 위험.
3. `eval/runner` composition (compose_trial_node_specs + node_spec_to_node_kwargs)
   — tier1 NodeSpec 측 mode + scenario_id(S5-S8) 만 전달, user_local·r_min·r_max·
   dot_c_max 는 filter_node 가 scenario_id 로 `scenario_params.tier1_cbf_params`
   (ADR-0023, 시나리오별 r_max) 에서 내부 resolve → *correct by construction*
   (literal 부재). scenario/mode wiring 은 `test_launch_composition.py`, r_max
   derive 값은 `sim/scenario_params/test/test_params.py` 가 cover.

따라서 silent-divergence 위험은 (2) baseline launch 의 하드코딩 literal 에만 존재.
본 모듈은 그 literal 을 AST 로 추출해 단일 진실 소스 및 canonical launch 와 비교.
(2) 의 r_max=1.5/dot_c_max=0.833 은 livingroom *manual 기본값* — 실험 경로(3)는
scenario_id 별 resolve 라 본 literal 과 별개. baseline launch 는 runner 미사용.

ROS 2 (launch_ros) import 불요 — launch 파일을 *실행하지 않고* AST 파싱만 하므로
host venv pytest 로 완전 cover (Mac mini Docker colcon test 불요).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from scenario_params.params import tier1_local_params


# ----------------------------------------------------------------- 경로 + 상수

_ROOT = Path(__file__).resolve().parents[3]
_BASELINE_LAUNCH = _ROOT / 'eval' / 'baselines' / 'launch'
_TIER1_LAUNCH = _ROOT / 'safety' / 'tier1' / 'launch'

# CBF spec 을 갖는 baseline (b0 제외; ADR-0025 amendment 19 — B1→B1a/B1b).
_BASELINE_CBF = [
    'b1a_static_rmin', 'b1b_static_rmax',
    'b2_modulated', 'b3_context_aug', 'b4_full_loop',
]
# tier1_filter mode='b2' 측 literal 동일군.
_BASELINE_B2 = ['b2_modulated', 'b3_context_aug', 'b4_full_loop']

# 모든 launch 측 동일해야 할 인터페이스 토픽.
_SHARED_TOPICS = {
    'input_twist_topic': '/cmd/trajectory_setpoint_nominal',
    'input_pose_topic': '/cmd/pose_setpoint_nominal',
    'output_twist_topic': '/cmd/trajectory_setpoint_safe',
    'output_pose_topic': '/cmd/pose_setpoint_safe',
}

_ALL_LAUNCH = [
    (_TIER1_LAUNCH, 'tier1_b0'),
    (_TIER1_LAUNCH, 'tier1_b1'),
    (_TIER1_LAUNCH, 'tier1_b1_max'),
    (_TIER1_LAUNCH, 'tier1_b2'),
    (_BASELINE_LAUNCH, 'b0_passthrough'),
    (_BASELINE_LAUNCH, 'b1a_static_rmin'),
    (_BASELINE_LAUNCH, 'b1b_static_rmax'),
    (_BASELINE_LAUNCH, 'b2_modulated'),
    (_BASELINE_LAUNCH, 'b3_context_aug'),
    (_BASELINE_LAUNCH, 'b4_full_loop'),
]


# ----------------------------------------------------------------- AST 추출기

_DYNAMIC = object()  # 비리터럴 값 sentinel.


def _eval_literal(node: ast.AST) -> Any:
    """AST 노드 → Python 리터럴. Constant + 음수/양수 UnaryOp 지원, 그 외 _DYNAMIC."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        operand = _eval_literal(node.operand)
        if operand is _DYNAMIC:
            return _DYNAMIC
        return -operand if isinstance(node.op, ast.USub) else +operand
    return _DYNAMIC


def _find_node_param_dict(tree: ast.AST) -> ast.Dict:
    """AST 측 Node(...) 호출 측 parameters=[{...}] 의 ast.Dict 반환."""
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
        if name != 'Node':
            continue
        for kw in call.keywords:
            if (
                kw.arg == 'parameters'
                and isinstance(kw.value, ast.List)
                and kw.value.elts
                and isinstance(kw.value.elts[0], ast.Dict)
            ):
                return kw.value.elts[0]
    raise AssertionError('Node(parameters=[{...}]) 측 미발견')


def extract_tier1_params(launch_path: Path) -> Tuple[Dict[str, Any], bool]:
    """launch 파일 측 tier1_filter Node parameters 리터럴 추출 (실행 없이 AST 파싱).

    Returns:
        (literals, has_unpack)
        - literals: 명시 리터럴 key→value (비리터럴 값은 제외)
        - has_unpack: `**unpack` (예: `**user_params`) 존재 여부
    """
    tree = ast.parse(launch_path.read_text(encoding='utf-8'))
    param_dict = _find_node_param_dict(tree)
    literals: Dict[str, Any] = {}
    has_unpack = False
    for key_node, val_node in zip(param_dict.keys, param_dict.values):
        if key_node is None:  # **unpack
            has_unpack = True
            continue
        val = _eval_literal(val_node)
        if val is not _DYNAMIC:
            literals[_eval_literal(key_node)] = val
    return literals, has_unpack


# ----------------------------------------------------------------- 추출기 sanity


class TestExtractorSanity:
    """AST 추출기 자체 검증 — 깨진 추출기가 다른 test 를 공허하게 통과시키는 것 방지."""

    def test_extracts_known_literals(self) -> None:
        literals, has_unpack = extract_tier1_params(
            _BASELINE_LAUNCH / 'b2_modulated.launch.py'
        )
        assert literals['mode'] == 'b2'
        assert literals['use_sim_time'] is True
        assert literals['r_min'] == 0.9
        assert literals['user_local_x'] == -0.5  # 음수 UnaryOp 처리 확인 (v4 layout)
        assert has_unpack is False

    def test_dynamic_unpack_detected(self) -> None:
        literals, has_unpack = extract_tier1_params(
            _TIER1_LAUNCH / 'tier1_b1.launch.py'
        )
        assert has_unpack is True
        assert 'user_local_x' not in literals  # **user_params 측 동적
        assert literals['gamma'] == 4.0


# ----------------------------------------------------------------- PRIMARY guard


class TestBaselineUserCoordsMatchSource:
    """PRIMARY — baseline launch 측 하드코딩 user 좌표 + r_min ↔ 단일 진실 소스
    (scenario_params livingroom). scenario_params 변경 시 silent divergence guard.
    """

    @pytest.mark.parametrize('stem', _BASELINE_CBF)
    def test_user_coords_match_livingroom_source(self, stem: str) -> None:
        literals, _ = extract_tier1_params(_BASELINE_LAUNCH / f'{stem}.launch.py')
        src = tier1_local_params('livingroom')
        for key in ('user_local_x', 'user_local_y', 'user_local_z', 'r_min'):
            assert literals[key] == src[key], (
                f"{stem}: {key} 측 하드코딩 {literals[key]} ≠ "
                f"scenario_params livingroom {src[key]} — silent divergence"
            )


# ----------------------------------------------------------------- CBF spec 정합


class TestBaselineCbfParamsConsistent:
    """baseline 간 CBF spec literal 정합."""

    @pytest.mark.parametrize('stem', _BASELINE_CBF)
    def test_gamma_u_max_locked(self, stem: str) -> None:
        """cmsm-proof §7.1 gamma=4.0 /s, u_max=0.5 m/s 측 모든 CBF baseline 동일."""
        literals, _ = extract_tier1_params(_BASELINE_LAUNCH / f'{stem}.launch.py')
        assert literals['gamma'] == 4.0
        assert literals['u_max'] == 0.5

    @pytest.mark.parametrize('stem', _BASELINE_B2)
    def test_b2_family_modulation_params(self, stem: str) -> None:
        """B2 군 측 r_max=1.5, dot_c_max=0.833, mode='b2'."""
        literals, _ = extract_tier1_params(_BASELINE_LAUNCH / f'{stem}.launch.py')
        assert literals['mode'] == 'b2'
        assert literals['r_max'] == 1.5
        assert literals['dot_c_max'] == 0.833

    def test_b1a_has_no_modulation_params(self) -> None:
        """B1a 정적 $r_\\text{min}$ — r_max / dot_c_max / 신뢰도 토픽 부재."""
        literals, _ = extract_tier1_params(
            _BASELINE_LAUNCH / 'b1a_static_rmin.launch.py'
        )
        assert literals['mode'] == 'b1'
        assert 'r_max' not in literals
        assert 'dot_c_max' not in literals
        assert 'grounding_confidence_topic' not in literals

    def test_b1b_static_rmax_params(self) -> None:
        """B1b 정적 $r_\\text{max}$ — mode='b1_max', r_max=1.5, dot_c_max / 신뢰도
        토픽 부재 (정적 baseline, 변조 없음)."""
        literals, _ = extract_tier1_params(
            _BASELINE_LAUNCH / 'b1b_static_rmax.launch.py'
        )
        assert literals['mode'] == 'b1_max'
        assert literals['r_max'] == 1.5
        assert 'dot_c_max' not in literals
        assert 'grounding_confidence_topic' not in literals


class TestB2FamilyIdentical:
    """b2_modulated ≡ b3_context_aug ≡ b4_full_loop 측 tier1 파라미터 literal 동일
    (각 docstring claim 정합 — B2/B3/B4 차이는 intent layer 측, tier1 동작 동일).
    """

    def test_b2_b3_b4_literal_identical(self) -> None:
        dicts = {
            stem: extract_tier1_params(_BASELINE_LAUNCH / f'{stem}.launch.py')[0]
            for stem in _BASELINE_B2
        }
        ref = dicts['b2_modulated']
        for stem, d in dicts.items():
            assert d == ref, f"{stem} 측 tier1 파라미터 ≠ b2_modulated literal"


# ----------------------------------------------------------------- canonical 정합


class TestTier1LaunchMatchesBaseline:
    """safety/tier1/launch 측 canonical CBF spec ↔ eval/baselines/launch 복제 정합.

    baseline 은 tier1 launch 의 spec 을 복제 — 두 출처 측 CBF 상수가 어긋나면
    paper §C trial (baseline) 과 수동 검증 (tier1 launch) 측 결과 불일치.
    """

    def test_b1a_cbf_spec_matches(self) -> None:
        t, _ = extract_tier1_params(_TIER1_LAUNCH / 'tier1_b1.launch.py')
        b, _ = extract_tier1_params(_BASELINE_LAUNCH / 'b1a_static_rmin.launch.py')
        for key in ('mode', 'gamma', 'u_max'):
            assert t[key] == b[key], f"B1a {key}: tier1 {t[key]} ≠ baseline {b[key]}"

    def test_b1b_cbf_spec_matches(self) -> None:
        t, _ = extract_tier1_params(_TIER1_LAUNCH / 'tier1_b1_max.launch.py')
        b, _ = extract_tier1_params(_BASELINE_LAUNCH / 'b1b_static_rmax.launch.py')
        for key in ('mode', 'gamma', 'u_max'):
            assert t[key] == b[key], f"B1b {key}: tier1 {t[key]} ≠ baseline {b[key]}"

    def test_b2_cbf_spec_matches(self) -> None:
        t, _ = extract_tier1_params(_TIER1_LAUNCH / 'tier1_b2.launch.py')
        b, _ = extract_tier1_params(_BASELINE_LAUNCH / 'b2_modulated.launch.py')
        for key in ('mode', 'r_max', 'gamma', 'u_max', 'dot_c_max'):
            assert t[key] == b[key], f"B2 {key}: tier1 {t[key]} ≠ baseline {b[key]}"


class TestTier1DynamicFilesUseSingleSource:
    """tier1_b1/b2 측 user 좌표 + r_min 측 `**resolve_scenario_params` 동적 unpack —
    하드코딩 재도입 시 baseline 과 동일한 divergence 위험 → 하드코딩 금지 guard.
    """

    @pytest.mark.parametrize('stem', ['tier1_b1', 'tier1_b1_max', 'tier1_b2'])
    def test_user_coords_not_hardcoded(self, stem: str) -> None:
        literals, has_unpack = extract_tier1_params(_TIER1_LAUNCH / f'{stem}.launch.py')
        for key in ('user_local_x', 'user_local_y', 'user_local_z', 'r_min'):
            assert key not in literals, (
                f"{stem}: {key} 측 하드코딩 — 동적 lookup (단일 진실 소스) 사용 의무"
            )
        assert has_unpack, f"{stem}: `**user_params` unpack 측 미발견"

    @pytest.mark.parametrize('stem', ['tier1_b1', 'tier1_b1_max', 'tier1_b2'])
    def test_references_resolve_scenario_params(self, stem: str) -> None:
        src = (_TIER1_LAUNCH / f'{stem}.launch.py').read_text(encoding='utf-8')
        assert 'resolve_scenario_params' in src


# ----------------------------------------------------------------- B0 + 토픽


class TestPassthroughConsistent:
    """B0 측 b0_passthrough ≡ tier1_b0 복제 정합 + CBF 파라미터 부재."""

    def test_b0_copies_tier1_b0(self) -> None:
        b, _ = extract_tier1_params(_BASELINE_LAUNCH / 'b0_passthrough.launch.py')
        t, _ = extract_tier1_params(_TIER1_LAUNCH / 'tier1_b0.launch.py')
        assert b == t

    def test_b0_has_no_cbf_params(self) -> None:
        b, _ = extract_tier1_params(_BASELINE_LAUNCH / 'b0_passthrough.launch.py')
        assert b['mode'] == 'b0'
        for key in ('r_min', 'r_max', 'gamma', 'u_max', 'dot_c_max'):
            assert key not in b


class TestSharedTopicsConsistent:
    """모든 launch 측 input/output twist/pose 토픽 4종 동일 (인터페이스 정합)."""

    @pytest.mark.parametrize('directory, stem', _ALL_LAUNCH)
    def test_topics_match(self, directory: Path, stem: str) -> None:
        literals, _ = extract_tier1_params(directory / f'{stem}.launch.py')
        for key, val in _SHARED_TOPICS.items():
            assert literals[key] == val, (
                f"{stem}: {key} 측 {literals.get(key)} ≠ {val}"
            )
