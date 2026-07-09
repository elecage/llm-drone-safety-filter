r"""s1 신호 소스 — OVD 후보 분포 → grounding entropy H → s1 = 1 − H.

[estimator.py](estimator.py) 의 `GInputs.s1` (의미 접지 집중도) 입력을 생성하는
*순수 로직*. cmsm-proof §2.1 의 s1 = 후보 점수 분포 entropy 의 보수.

## 동기 (ADR-0020 C12 1차 구현)

referential 모호성(예: S5 외형 동일 머그컵 3개)은 LLM self-consistency(s2)로
*안 잡힘* — LLM 이 모호한데도 첫 후보를 일관 선택(position bias)하면 ρ 높음.
실측: gpt-4o S5 "내 머그컵 보여줘" → mug_left 8/8 일관(2026-05-29 calibration).
→ s1(후보 분포 entropy)이 referential 모호성의 *주 신호*. 외형 동일 후보가
여럿이면 OVD confidence 분포가 flat → H 높음 → s1 낮음 → c = s1·s2·s3 낮음 →
$r \to r_\text{max}$ 보수화.

## 신호 정의

- 후보 confidence 분포 $\{s_1, \ldots, s_K\}$ → 정규화 $p_i = s_i / \sum s_j$.
- $H = -\sum p_i \ln p_i / \ln K \in [0, 1]$ (정규화 Shannon entropy, $K \geq 2$).
- $K = 1$ (단일 후보) → $H = 0$ (모호성 없음). $K = 0$ → caller 가 s1_absent 처리.
- $s_1 = 1 - H$.

## 범위 (ADR-0020 C12)

`referent_scores` 는 *class_label 완전일치* 로 후보를 필터. `weighted_referent_scores`
는 여기에 *위치 수식어 disambiguation* 을 더한다 (C12 1차) — anchor(위치 수식어
referent) 좌표 거리 가우시안 가중으로 동일-label 후보를 좁힘. S7(거실탁자 vs 식탁
책)처럼 동일 label 객체가 여럿이고 발화에 위치 단서가 있으면 해소, S5(외형+위치
동일 mug 3개, 위치 단서 없음)는 flat 유지 → 명료화.

**1차 = 순수 로직** (anchor 좌표는 호출자 제공). 후속(OVD 연결 의존): anchor 추출
(발화 위치 수식어 → scene 객체), OVD detection↔scene 좌표 association,
estimator_node 실 wiring.
"""
from __future__ import annotations

import math
from typing import Sequence

from scenario_params.scene import expand_ovd_synonyms


def grounding_entropy(scores: Sequence[float]) -> float:
    """후보 confidence 분포 → 정규화 Shannon entropy $H \\in [0, 1]$.

    Args:
        scores: referent 매칭 후보들의 confidence (각 $\\geq 0$, 최소 1개).

    Returns:
        $H \\in [0, 1]$. 0 = 단일 dominant(집중), 1 = K개 균일(최대 모호).

    Raises:
        ValueError: scores 가 비었거나, 음수 포함, 또는 합이 0.
    """
    if not scores:
        raise ValueError("scores 비어 있음 — caller 가 K=0 (s1_absent) 처리해야 함")
    if any(s < 0 for s in scores):
        raise ValueError(f"confidence 음수 불가: {list(scores)!r}")
    total = float(sum(scores))
    if total <= 0.0:
        raise ValueError(f"confidence 합이 0 — 분포 정의 불가: {list(scores)!r}")
    k = len(scores)
    if k == 1:
        return 0.0  # 단일 후보 = 모호성 없음
    ps = [s / total for s in scores]
    raw_h = -sum(p * math.log(p) for p in ps if p > 0.0)
    return min(1.0, raw_h / math.log(k))


def s1_from_scores(scores: Sequence[float]) -> float:
    """후보 confidence 분포 → $s_1 = 1 - H$ (의미 접지 집중도).

    1 = 단일 dominant referent(확실), 0 = 균일 분포(완전 모호).
    """
    return 1.0 - grounding_entropy(scores)


def _expand_label_tokens(referent_labels: Sequence[str]) -> set:
    """referent label 들을 매칭용 집합으로 확장 — 완전 라벨 + 토큰 + 소문자 정규화.

    direct mode(context_graph 부재)에서 LLM 은 인스턴스 name lookup 이 불가해 OVD
    어휘 밖 합성 라벨('mug_cup')을 낼 수 있다. ``'_'``·``'-'``·공백으로 토큰화해
    ovd class_label('cup') 매칭을 복원한다(sigma_bridge ``_handle_inspect`` 의 keys
    토큰 흡수와 정합 — ADR-0029 블로커 1 "인스턴스 id ↔ 검출 클래스 입도 차이"의
    direct-mode 연장). 단일 토큰 라벨('chair')은 자기 자신만 추가되어 불변.

    동의어 정규화 (세션 62 진단): LLM 이 OVD 어휘의 *동의어*('mug' vs 'cup')를
    내면 토큰 분해로도 매칭이 복원되지 않아 no_match → c=0 (llama S5 10/10 실측).
    ``scenario_params.scene.OVD_CLASS_SYNONYMS`` (단일 소스 — sigma_bridge
    ``inspect_referent_keys`` 와 공유)로 정본 클래스를 집합에 더한다.

    Args:
        referent_labels: LLM ``theta.target_id`` 에서 온 referent label 들.

    Returns:
        매칭용 소문자 라벨·토큰 집합 (+ OVD 정본 클래스 동의어).
    """
    wanted: set = set()
    for label in referent_labels:
        s = str(label).strip().lower()
        if not s:
            continue
        wanted.add(s)
        for tok in s.replace('-', '_').replace(' ', '_').split('_'):
            if tok:
                wanted.add(tok)
    return expand_ovd_synonyms(wanted)


# ── ADR-0040 D7: 동일 라벨 중복박스 dedup ───────────────────────────────────
# 진단(세션 61): YOLO-World 가 같은 객체(예 S6 단일 sofa)를 2박스로 중복 검출
# (전체 박스 + 부분 박스, nested IoU~0.5)하면 매칭 후보 수가 가짜로 늘어 H↑ → s1
# 붕괴(1.0→~0). 동일 라벨 후보 중 박스가 충분히 겹치면(또는 한쪽이 다른 쪽에 거의
# 포함되면) 같은 객체로 보고 confidence 높은 박스만 남긴다 (class-aware greedy NMS).
# nested 케이스(IoU 만으로는 미억제)를 잡으려 IoU 와 containment(작은 박스가 큰
# 박스에 포함되는 비율) 를 병용한다.
_DEDUP_IOU_THR = 0.5       # 표준 NMS 겹침 임계
_DEDUP_CONTAIN_THR = 0.7   # 작은 박스가 큰 박스에 이만큼 포함되면 중복(nested)


def _box_xyxy(bbox) -> tuple:
    """(cx, cy, w, h) → (x0, y0, x1, y1)."""
    cx, cy, w, h = bbox
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def _box_inter_area(a, b) -> float:
    ax0, ay0, ax1, ay1 = _box_xyxy(a)
    bx0, by0, bx1, by1 = _box_xyxy(b)
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    return iw * ih


def _box_area(bbox) -> float:
    _, _, w, h = bbox
    return max(0.0, w) * max(0.0, h)


def is_duplicate_box(
    a, b, iou_thr: float = _DEDUP_IOU_THR, contain_thr: float = _DEDUP_CONTAIN_THR,
) -> bool:
    """두 박스 ``(cx,cy,w,h)`` 가 같은 객체의 중복인지 — IoU 또는 containment 기준."""
    inter = _box_inter_area(a, b)
    if inter <= 0.0:
        return False
    area_a, area_b = _box_area(a), _box_area(b)
    union = area_a + area_b - inter
    iou = inter / union if union > 0.0 else 0.0
    min_area = min(area_a, area_b)
    contain = inter / min_area if min_area > 0.0 else 0.0
    return iou >= iou_thr or contain >= contain_thr


def dedup_overlapping_candidates(candidates) -> list:
    """동일 라벨 중복박스 제거 (class-aware greedy NMS, confidence 높은 박스 유지).

    bbox 없는 후보(``.bbox`` None/부재)는 *항상 유지*(병합 판단 불가 — graceful).
    서로 다른 라벨은 병합하지 않는다(예 cup-in-table 은 별개 객체).

    Args:
        candidates: ``.class_label`` / ``.confidence`` (/ ``.bbox=(cx,cy,w,h)``) 객체들.

    Returns:
        중복 제거된 후보 객체 리스트 (입력 순서 보존; 중복 중 confidence 낮은 박스 제외).
    """
    cands = list(candidates)
    # bbox 있는 후보가 없으면 dedup 불가 — 입력 그대로(순서 보존, fast path).
    if not any(getattr(c, 'bbox', None) is not None for c in cands):
        return cands
    # greedy NMS — confidence 내림차순으로 보고, 같은 라벨·겹치는 *더 낮은* 박스 억제.
    order = sorted(range(len(cands)), key=lambda i: cands[i].confidence, reverse=True)
    suppressed: set = set()
    for pos, i in enumerate(order):
        if i in suppressed:
            continue
        bi = getattr(cands[i], 'bbox', None)
        if bi is None:
            continue
        label_i = str(cands[i].class_label).strip().lower()
        for j in order[pos + 1:]:
            if j in suppressed:
                continue
            bj = getattr(cands[j], 'bbox', None)
            if bj is None or str(cands[j].class_label).strip().lower() != label_i:
                continue
            if is_duplicate_box(bi, bj):
                suppressed.add(j)
    return [c for k, c in enumerate(cands) if k not in suppressed]


def referent_scores(detections, referent_labels: Sequence[str]) -> list:
    """detection 리스트에서 referent class_label 매칭 후보의 confidence 추출.

    duck-typed: 각 detection 은 ``.class_label`` + ``.confidence`` 속성을 가지면 됨
    (intent_ovd.Detection 정합, import 의존 없음). 매칭 후보는 동일 라벨 중복박스
    dedup(ADR-0040 D7) 후 산출.

    위치 수식어 기반 disambiguation 이 필요하면 ``weighted_referent_scores`` 사용.

    Args:
        detections: ``.class_label`` / ``.confidence`` (/ ``.bbox``) 속성 객체들.
        referent_labels: 발화 referent 의 후보 class label 들 (라벨·토큰 매칭,
            ``_expand_label_tokens``).

    Returns:
        매칭된 후보 confidence 리스트 (빈 리스트 가능 → caller 가 s1_absent).
    """
    wanted = _expand_label_tokens(referent_labels)
    matched = [
        d for d in detections
        if str(d.class_label).strip().lower() in wanted
    ]
    return [d.confidence for d in dedup_overlapping_candidates(matched)]


# 위치 수식어 disambiguation 거리 스케일 [m] — 가구 간격 스케일 (호출자 override).
_DEFAULT_SPATIAL_SIGMA_M = 0.5


def spatial_weight(position, anchor, sigma_m: float = _DEFAULT_SPATIAL_SIGMA_M) -> float:
    r"""후보 위치 ↔ anchor 의 수평(xy) 거리 가우시안 가중 $\in (0, 1]$.

    $w = \exp(-d_\text{xy}^2 / (2 \sigma^2))$. "위/아래"(z)는 referent 식별과 무관
    하므로 xy 평면 거리만 사용 ("거실 탁자 위 책" 의 anchor=탁자 와 책은 xy 동일,
    z 차이).

    Args:
        position: 후보 world 좌표 (x, y, ...) — xy 2 성분 사용.
        anchor: 위치 수식어 referent 의 world 좌표 (x, y, ...).
        sigma_m: 거리 스케일 [m, 양수]. 작을수록 anchor 근접 후보만 살아남음.

    Returns:
        가중 $\in (0, 1]$. 거리 0 → 1, 거리=σ → $\exp(-0.5) \approx 0.607$, 큰 거리 → ≈0.

    Raises:
        ValueError: sigma_m 이 양수 아님.
    """
    if sigma_m <= 0.0:
        raise ValueError(f"sigma_m 은 양수여야 함: {sigma_m}")
    dx = float(position[0]) - float(anchor[0])
    dy = float(position[1]) - float(anchor[1])
    d2 = dx * dx + dy * dy
    return math.exp(-d2 / (2.0 * sigma_m * sigma_m))


def weighted_referent_scores(
    candidates,
    referent_labels: Sequence[str],
    anchor=None,
    sigma_m: float = _DEFAULT_SPATIAL_SIGMA_M,
) -> list:
    """label 매칭 후보의 confidence × (anchor 거리 가중) — 위치 disambiguation.

    ADR-0020 C12 — 발화에 위치 단서가 있으면(anchor 제공) 그에 가까운 동일-label
    후보로 분포를 좁힌다 (S7: "거실 탁자 위 책" → coffee_table anchor 근처 book 만
    dominant → s1 높음). anchor 없으면 ``referent_scores`` 와 동일 (label-only, S5
    "내 머그컵" 처럼 위치 단서 없는 발화 → flat → s1 낮음 → 명료화).

    duck-typed: 각 candidate 는 ``.class_label`` + ``.confidence`` (+ anchor 사용 시
    ``.position``). ``.position`` 부재 후보는 anchor 주어져도 weight=1 (graceful
    degrade — 위치 미상 후보는 좁히지 않음).

    Args:
        candidates: ``.class_label`` / ``.confidence`` (/ ``.position``) 속성 객체들.
        referent_labels: 발화 referent 의 후보 class label 들 (완전일치).
        anchor: 위치 수식어 referent 의 world 좌표 (x, y, ...). None 이면 위치 가중
            없음 (label-only).
        sigma_m: 거리 가중 스케일 [m].

    Returns:
        매칭 후보의 (가중된) confidence 리스트 (빈 리스트 가능 → caller 가 s1_absent).
    """
    wanted = _expand_label_tokens(referent_labels)
    matched = [
        c for c in candidates
        if str(c.class_label).strip().lower() in wanted
    ]
    # ADR-0040 D7 — 동일 라벨 중복박스 제거 후 점수화 (가짜 후보 인플레→s1 붕괴 차단).
    matched = dedup_overlapping_candidates(matched)
    out = []
    for c in matched:
        weight = 1.0
        if anchor is not None:
            pos = getattr(c, 'position', None)
            if pos is not None:
                weight = spatial_weight(pos, anchor, sigma_m)
        out.append(c.confidence * weight)
    return out
