#!/usr/bin/env python3
"""S5 모호 referent(머그컵) 로컬 모델 빌드 — Fuel 커피잔 mesh 복사.

[ADR-0035] S5 "모호한 referent"(ADR-0006)는 식탁 위 외형 동일 머그컵 3개다.
primitive cylinder 는 외형 기반 검출기 YOLO-World 가 'cup' 으로 검출하지 못해
($s_1\\approx0$, ADR-0033 S8 사람과 동형) 모호성(C2 핵심)을 입증할 수 없으므로,
**사실적 Fuel 커피잔 mesh** 를 로컬 모델로 복사해 ``sim/models/coffee_mug`` 에
생성한다. 3 인스턴스(mug_left/center/right)는 ``livingroom_base.sdf`` 가 이 단일
모델을 동일 외형으로 ``<include>`` 한다(위치만 다름 = 모호성의 원인).

## 베이스 모델

``GoogleResearch/ACE_Coffee_Mug_Kristen_16_oz_cup`` — Google Scanned Objects 의
실제 photogrammetry 커피잔(손잡이 + 텍스처). 바운딩 박스 ≈ 0.126(w) × 0.091(d)
× 0.135(h) m — 표준 머그 크기라 OVD 검출에 유리. 사람 모델(build_yard_people.py)
과 달리 색 제어가 불필요(3개 동일)하므로 재색칠 단계가 없다.

라이선스: Creative Commons Attribution 4.0 International (CC-BY 4.0).
출처: Google Research / Google Scanned Objects, via Gazebo Fuel
(https://fuel.gazebosim.org/1.0/GoogleResearch/models/ACE_Coffee_Mug_Kristen_16_oz_cup).

## 왜 스크립트(바이너리 미커밋)인가

``sim/models/`` 는 ``.gitignore`` 대상(mesh ~1MB). 바이너리 대신 본 *결정론적
빌드 스크립트* 를 커밋해 재현성 확보 — 맥미니·CI 가 1회 실행해 동일 로컬 모델을
생성. Fuel 모델은 ``gz fuel download`` 로 캐시(네트워크 1회).

실행: ``python3 scripts/build_mug.py`` (리포 루트, gz 필요).
검증: ``GZ_SIM_RESOURCE_PATH=$PWD/sim/models gz sim -s livingroom world`` 로 확인.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO / 'sim' / 'models'

FUEL_MODEL = 'GoogleResearch/ACE_Coffee_Mug_Kristen_16_oz_cup'
LOCAL_NAME = 'coffee_mug'


def fuel_cache_dir(owner_model: str) -> Path:
    """Fuel 캐시의 모델 디렉토리(최고 버전) 경로 — 없으면 download."""
    owner, model = owner_model.split('/', 1)
    base = (Path.home() / '.gz' / 'fuel' / 'fuel.gazebosim.org'
            / owner.lower() / 'models' / model.lower())
    if not base.exists():
        url = f'https://fuel.gazebosim.org/1.0/{owner}/models/{model}'
        print(f'  [download] {url}')
        subprocess.run(['gz', 'fuel', 'download', '-u', url], check=True)
    versions = sorted([p for p in base.iterdir() if p.is_dir() and p.name.isdigit()],
                      key=lambda p: int(p.name))
    if not versions:
        raise FileNotFoundError(f'Fuel 캐시 버전 없음: {base}')
    return versions[-1]


def build() -> None:
    print(f'[{LOCAL_NAME}] <- {FUEL_MODEL}')
    cache = fuel_cache_dir(FUEL_MODEL)
    dst = MODELS_DIR / LOCAL_NAME
    if dst.exists():
        shutil.rmtree(dst)
    # 썸네일 제외 복사(용량)
    shutil.copytree(cache, dst, ignore=shutil.ignore_patterns('thumbnails'))

    # model.config name → 로컬명
    cfg = dst / 'model.config'
    cfg.write_text(re.sub(r'<name>[^<]*</name>',
                          f'<name>{LOCAL_NAME}</name>',
                          cfg.read_text(encoding='utf-8'), count=1),
                   encoding='utf-8')

    # model.sdf: (1) 혹시 Fuel URI mesh 참조가 있으면 상대경로화(이 모델은 이미 상대),
    # (2) collision 블록 제거 → visual-only. ADR-0035 D1: visual 은 이 mesh 를
    # <include>, collision 은 livingroom_base.sdf 가 작은 정적 cylinder 로 별도 부여
    # (mesh collision 이중화·물리 불안정 회피). 머그컵은 정적 식탁 소품이라 드론이
    # 접촉하지 않는다.
    sdf = dst / 'model.sdf'
    txt = sdf.read_text(encoding='utf-8')
    txt = re.sub(r'<uri>https://fuel\.gazebosim\.org/[^<]*/files/meshes/([^<]+)</uri>',
                 r'<uri>meshes/\1</uri>', txt)
    txt = re.sub(r'\s*<collision[^>]*>.*?</collision>', '', txt, flags=re.S)
    sdf.write_text(txt, encoding='utf-8')

    print(f'  ok -> {dst.relative_to(REPO)} (CC-BY 4.0, GoogleResearch)')


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    build()
    print('\n완료 — sim/models/coffee_mug 생성. '
          'GZ_SIM_RESOURCE_PATH 에 sim/models 포함 필요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
