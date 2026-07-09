"""시나리오 장소별 장면(scene) 객체 — context augmentation 데이터 소스.

[ADR-0026 D3](../../../docs/handover/decisions/0026-paper1-perception-assumptions.md)
ground-truth perception 정합 — paper §C 는 사전 지도(known_objects + 좌표)를 sim
ground truth 로 가정. 본 모듈이 *장소별 정적 장면* 의 단일 진실 소스 (context
graph 발행용, [intent_context](../../../intent/context/) 측 소비).

## 좌표 출처

좌표는 sim world SDF 의 `<model><pose>` (world frame) 를 *전사(transcribe)*:
  - 거실: [sim/worlds/livingroom_base.sdf](../../worlds/livingroom_base.sdf)
  - 마당: [sim/worlds/yard_base.sdf](../../worlds/yard_base.sdf)

> ⚠️ SDF 와 *수동 정합* — SDF 좌표 변경 시 본 모듈 동기 의무. 향후 SDF 자동
> 추출(ADR-0026 D3) 또는 SDF↔scene sanity test (C31 패턴) 로 divergence guard
> 가능 (별 트랙).

## 스키마

``scene_objects_for_location(location)`` → ``[{'name': str, 'position': [x, y, z],
'ovd_class': str | None}, ...]``. 좌표는 world frame [m]. context augmentation prompt
측 *지시 대상 해석* (referent grounding — "저 소파", "거기") 에 사용.

``ovd_class`` = 객체의 OVD(개방 어휘 검출) *클래스* 라벨 — 인스턴스 id(``name``,
예 ``chair_left``)와 검출기 출력 클래스(예 ``chair``)의 입도 차이를 메우는 필드
([ADR-0029](../../../docs/handover/decisions/0029-trial-integration-live-path.md)
블로커 1). estimator 의 $s_1$ 지시 대상 매칭은 *클래스* 기준이라(동일 클래스 객체
다수 → 분포 엔트로피↑ → $s_1$↓ = 모호성 신호, C2) wrapper 가 σ 의 ``theta.target_class``
로 실어 보낸다. 검출 어휘 밖 객체(``tv_stand``·``dock`` 등)는 ``None``.
"""

from __future__ import annotations

from typing import Dict, List, Optional


# 장소별 상호작용 대상 객체 (벽·바닥·조명 등 비상호작용 제외).
# 좌표 = SDF <model><pose> world frame [m].
# ``ovd_class`` = OVD 검출 어휘의 클래스 라벨 (인스턴스 id ↔ 검출 클래스 입도 차이
# 해소, ADR-0029 블로커 1). 거실 어휘 = {sofa, chair, table, cup}, 마당 = {person}
# (본 정의가 OVD 정적 vocabulary 단일 소스 — ovd_vocabulary_* 가 파생). 어휘 밖
# 객체(tv_stand·tv·dock)는 None.
_SCENE_OBJECTS: Dict[str, List[Dict]] = {
    'livingroom': [
        {'name': 'sofa', 'position': [-1.8, 1.5, 0.4], 'ovd_class': 'sofa'},
        {'name': 'coffee_table', 'position': [-1.8, 0.5, 0.2], 'ovd_class': 'table'},
        {'name': 'dining_table', 'position': [2.0, -1.0, 0.375], 'ovd_class': 'table'},
        {'name': 'chair_left', 'position': [2.0, -0.4, 0.425], 'ovd_class': 'chair'},
        {'name': 'chair_right', 'position': [2.0, -1.6, 0.425], 'ovd_class': 'chair'},
        # S5 모호 referent: 식탁 위 외형 동일 머그컵 3개 (ADR-0035/ADR-0006). 위치만
        # 달라 발화 '머그컵'으로 지시 대상을 1개로 좁힐 수 없다 → 지시 후보 엔트로피 H>0.
        {'name': 'mug_left', 'position': [1.7, -1.0, 0.80], 'ovd_class': 'cup'},
        {'name': 'mug_center', 'position': [2.0, -1.0, 0.80], 'ovd_class': 'cup'},
        {'name': 'mug_right', 'position': [2.3, -1.0, 0.80], 'ovd_class': 'cup'},
        {'name': 'tv_stand', 'position': [-1.8, -1.5, 0.3], 'ovd_class': None},
        {'name': 'tv', 'position': [-1.8, -1.5, 1.015], 'ovd_class': None},
        {'name': 'dock', 'position': [0.5, -0.5, 0.025], 'ovd_class': None},
    ],
    'yard': [
        {'name': 'child_red_shirt', 'position': [1.0, 0.0, 0.53], 'ovd_class': 'person'},
        {'name': 'adult_red_hat', 'position': [2.5, 1.0, 0.85], 'ovd_class': 'person'},
        {'name': 'adult_green', 'position': [-1.0, 2.0, 0.85], 'ovd_class': 'person'},
        {'name': 'adult_blue', 'position': [3.0, -1.5, 0.85], 'ovd_class': 'person'},
        {'name': 'adult_white', 'position': [0.5, 3.0, 0.85], 'ovd_class': 'person'},
        {'name': 'dock', 'position': [0.0, -2.0, 0.025], 'ovd_class': None},
    ],
}

VALID_LOCATIONS: frozenset = frozenset(_SCENE_OBJECTS.keys())


def scene_objects_for_location(location: str) -> List[Dict]:
    """장소(location) → 장면 객체 list (매 호출 새 list/dict — mutation 격리).

    Args:
        location: 'livingroom' | 'yard'.

    Returns:
        ``[{'name': str, 'position': [x, y, z], 'ovd_class': str | None}, ...]`` —
        world frame [m]. ``ovd_class`` = 검출 클래스 라벨(어휘 밖이면 None).

    Raises:
        RuntimeError: location 측 unknown.
    """
    if location not in _SCENE_OBJECTS:
        raise RuntimeError(
            f'location={location!r} 측 unknown — 허용 = {sorted(_SCENE_OBJECTS)!r}'
        )
    return [
        {
            'name': obj['name'],
            'position': list(obj['position']),
            'ovd_class': obj.get('ovd_class'),
        }
        for obj in _SCENE_OBJECTS[location]
    ]


# ── OVD 정적 vocabulary 파생 (scene 단일 진실 소스) ──────────────────────────
# 발화 referent 의 OVD 클래스(params.scenario_target_class)가 반드시 어휘에
# 포함되도록 scene 의 ``ovd_class`` 에서 직접 파생한다. 종전 scripts 하드코딩
# ``['couch','table','chair']`` 는 거실 referent 'sofa'·마당 'person' 을 빠뜨려
# S5/S6/S8 grounding 이 영영 실패(검출 0 → s1≈0 → c=0 → B4 게이트 전부 reject)
# 했다 (세션 53 B4 게이트 sim e2e 적발). scripts 가 본 함수로 파생해 scene↔vocab
# drift 를 원천 차단한다.


def ovd_vocabulary_for_location(location: str) -> List[str]:
    """장소(location) → OVD 정적 어휘 (scene 객체의 비-None ``ovd_class``, 정렬·중복 제거).

    Args:
        location: 'livingroom' | 'yard'.

    Returns:
        정렬된 고유 OVD 클래스 라벨 list (어휘 밖 ``None`` 제외).

    Raises:
        RuntimeError: location 측 unknown (scene_objects_for_location 위임).
    """
    classes = {
        obj['ovd_class']
        for obj in scene_objects_for_location(location)
        if obj.get('ovd_class')
    }
    return sorted(classes)


def ovd_vocabulary_all() -> List[str]:
    """전 장소 OVD 클래스 합집합 (정렬) — 영속 OVD detector 단일 인스턴스용.

    본실험 격자(up.sh 영속 셸)는 OVD detector *한 인스턴스* 로 전 시나리오
    (전 장소 = 거실+마당)를 서빙하므로 어휘가 전 장소 클래스를 덮어야 한다. 한
    장면에 없는 클래스 프롬프트는 무해(해당 객체가 없으면 검출 0) — 예 거실에서
    'person' 은 0 검출.

    Returns:
        정렬된 전 장소 고유 OVD 클래스 라벨 list.
    """
    classes: set = set()
    for loc in VALID_LOCATIONS:
        classes.update(ovd_vocabulary_for_location(loc))
    return sorted(classes)


# ── OVD 어휘 동의어 정규화 (세션 62 진단 — llama σ target_id='mug') ──────────
# LLM 이 낸 referent 라벨이 OVD 어휘(``ovd_class``)와 *동의어* 로 어긋나면
# ('mug' vs 'cup') estimator s1 매칭·sigma_bridge vantage 후보 필터가 모두
# 실패해 c=0 로 고착된다 (run 20260630T0538 llama S5 10/10 실측 — +0.5m 상승
# fallback). 동의어 → 정본 ovd_class 매핑의 *단일 소스* 가 본 표다. 소비자 둘:
#   - intent_confidence.grounding._expand_label_tokens (s1 referent 매칭)
#   - intent_sigma_bridge.sigma_bridge_helpers.inspect_referent_keys (vantage 필터)
# 과설계 금지 — scene ``ovd_class`` 어휘 기준 최소 매핑만. 새 항목은 실측 근거와
# 함께 추가 (양방향 불요: OVD 는 ovd_class 정본만 검출하므로 동의어→정본 단방향).
OVD_CLASS_SYNONYMS: Dict[str, str] = {
    'mug': 'cup',      # llama S5 회귀 실측 (σ target_id='mug', OVD 어휘 'cup')
    'couch': 'sofa',   # 종전 scripts 하드코딩 'couch' 계열 — 정본 'sofa'
}


def expand_ovd_synonyms(labels) -> set:
    """소문자 라벨/토큰 집합에 OVD 정본 클래스 동의어를 더해 반환 (원소 보존).

    Args:
        labels: 소문자 정규화된 라벨·토큰 iterable (str).

    Returns:
        입력 원소 ∪ ``OVD_CLASS_SYNONYMS`` 매핑 정본 클래스 집합.
    """
    out = set(labels)
    for lbl in list(out):
        canon = OVD_CLASS_SYNONYMS.get(lbl)
        if canon:
            out.add(canon)
    return out


def ovd_vocabulary_launch_str(location: Optional[str] = None) -> str:
    """ROS launch ``vocabulary:=`` 인자 형식 문자열 — ``['a','b',...]``.

    scripts(up.sh·start_intent_stack.sh)가 OVD_VOCAB 디폴트를 본 함수로 파생해
    scene 정의와의 drift 를 차단한다.

    Args:
        location: None 이면 전 장소 합집합(``ovd_vocabulary_all``, 영속 셸용),
            아니면 해당 장소 어휘(``ovd_vocabulary_for_location``).

    Returns:
        launch 리스트 형식 문자열 (예 ``['chair','person','sofa','table']``).
    """
    vocab = (
        ovd_vocabulary_all()
        if location is None
        else ovd_vocabulary_for_location(location)
    )
    return '[' + ','.join(f"'{c}'" for c in vocab) + ']'
