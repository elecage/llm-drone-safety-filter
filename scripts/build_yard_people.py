#!/usr/bin/env python3
"""마당(S8) 사람 로컬 모델 빌드 — Fuel 인체 mesh 복사 + 의류 텍스처 색 재색칠.

[ADR-0033] S8 사람은 외관 색 속성이 시나리오 핵심(빨강 셔츠 아이 = follow target,
빨강 모자 어른 = distractor). Fuel 인체 모델은 고정 텍스처라 이름이 지정한 색을
입지 않으므로, 의류 *diffuse 텍스처만* 휘도 보존 틴트로 재색칠한 **로컬 모델**을
``sim/models/`` 에 생성한다(거실 가구의 Fuel include 패턴과 달리 색 제어 필요).

## 왜 스크립트(바이너리 미커밋)인가

``sim/models/`` 는 ``.gitignore`` 대상(인체 mesh ~28MB/모델). 바이너리 대신 본
*결정론적 빌드 스크립트* 를 커밋해 재현성 확보 — 맥미니·CI 가 1회 실행해 동일
로컬 모델을 생성. Fuel 모델은 ``gz fuel download`` 로 캐시(네트워크 1회).

## 재색칠 가능 베이스 (의류 = 분리 diffuse 텍스처)

- ``OpenRobotics/Walking person`` — 셔츠 = ``tshirt02_texture.png`` (남성 체형).
- ``OpenRobotics/Casual female`` — 정장 = ``female_casualsuit01/02_diffuse.png`` (여성 체형).

VisitorKidWalk(단일 바디 아틀라스 → 피부까지 물듦)·FemaleVisitor(구조 상이)는
*부분 재색칠 불가* 라 비채택. child 는 Walking person 을 ``scale`` 로 축소해 충당.

## 텍스처 파일명 고유화 (필수)

ogre2 가 텍스처를 *basename* 으로 캐싱 → 두 모델이 같은 ``tshirt02_texture.png``
를 참조하면 먼저 로드된 색이 재사용됨(세션 49 blue 가 흰색으로 렌더된 버그).
재색칠 텍스처는 모델별 고유명(``shirt_blue.png`` 등)으로 rename + .dae 갱신.

## mesh uri 상대화

Fuel 캐시 model.sdf 는 mesh 를 Fuel URI 로 참조 → 로컬 틴트 텍스처 미사용.
복사본의 mesh ``<uri>`` 를 상대경로(``meshes/x.dae``)로 바꿔 로컬 .dae(→로컬
텍스처) 를 쓰게 한다.

실행: ``python3 scripts/build_yard_people.py`` (리포 루트, gz + Pillow + numpy 필요).
검증: ``GZ_SIM_RESOURCE_PATH=$PWD/sim/models gz sim -s yard world`` 로 색 확인.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit('Pillow + numpy 필요 — gz relay venv(~/.venvs/llmdrone-gz) 에서 실행하라.')

REPO = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO / 'sim' / 'models'

# (로컬명, Fuel owner/model, dae 파일, 원 셔츠 텍스처, 신규 셔츠명, 틴트 RGB, scale)
SPEC = [
    ('yard_walk_blue', 'OpenRobotics/Walking person', 'walking.dae',
     ['tshirt02_texture.png'], 'shirt_blue.png', (0.15, 0.30, 0.80), 1.0),
    ('yard_child_red', 'OpenRobotics/Walking person', 'walking.dae',
     ['tshirt02_texture.png'], 'shirt_red.png', (0.80, 0.12, 0.12), 0.62),
    ('yard_female_green', 'OpenRobotics/Casual female', 'casual_female.dae',
     ['female_casualsuit01_diffuse.png', 'female_casualsuit02_diffuse.png'],
     'suit_green.png', (0.15, 0.55, 0.20), 1.0),
    ('yard_female_white', 'OpenRobotics/Casual female', 'casual_female.dae',
     ['female_casualsuit01_diffuse.png', 'female_casualsuit02_diffuse.png'],
     'suit_white.png', (0.92, 0.92, 0.92), 1.0),
]


def fuel_cache_dir(owner_model: str) -> Path:
    """Fuel 캐시의 모델 디렉토리(최고 버전) 경로 — 없으면 download."""
    owner, model = owner_model.split('/', 1)
    base = Path.home() / '.gz' / 'fuel' / 'fuel.gazebosim.org' / owner.lower() / 'models' / model.lower()
    if not base.exists():
        url = f'https://fuel.gazebosim.org/1.0/{owner}/models/{model}'
        print(f'  [download] {url}')
        subprocess.run(['gz', 'fuel', 'download', '-u', url], check=True)
    versions = sorted([p for p in base.iterdir() if p.is_dir() and p.name.isdigit()],
                      key=lambda p: int(p.name))
    if not versions:
        raise FileNotFoundError(f'Fuel 캐시 버전 없음: {base}')
    return versions[-1]


def tint(src: Path, dst: Path, rgb: tuple) -> None:
    """휘도 보존 색 틴트 — diffuse 텍스처를 target 색으로(천 주름 음영 유지)."""
    a = np.asarray(Image.open(src).convert('RGBA')).astype(np.float32)
    lum = np.clip((0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) / 255.0 * 1.15, 0, 1)
    out = a.copy()
    for i, c in enumerate(rgb):
        out[..., i] = np.clip(255 * lum * c, 0, 255)
    Image.fromarray(out.astype('uint8'), 'RGBA').save(dst)


def build_one(local_name, owner_model, dae, shirts, new_shirt, rgb, scale) -> None:
    print(f'[{local_name}] <- {owner_model}')
    cache = fuel_cache_dir(owner_model)
    dst = MODELS_DIR / local_name
    if dst.exists():
        shutil.rmtree(dst)
    # 썸네일 제외 복사(용량)
    shutil.copytree(cache, dst, ignore=shutil.ignore_patterns('thumbnails'))

    # model.config name
    cfg = dst / 'model.config'
    cfg.write_text(re.sub(r'<name>[^<]*</name>',
                          f'<name>{local_name}</name>',
                          cfg.read_text(encoding='utf-8'), count=1),
                   encoding='utf-8')

    # model.sdf: mesh uri 상대화 + scale 주입
    sdf = dst / 'model.sdf'
    txt = sdf.read_text(encoding='utf-8')
    txt = re.sub(r'<uri>https://fuel\.gazebosim\.org/[^<]*/files/meshes/([^<]+)</uri>',
                 r'<uri>meshes/\1</uri>', txt)
    if abs(scale - 1.0) > 1e-6:
        # 모든 mesh 블록에 scale 삽입(없을 때만)
        def add_scale(m):
            blk = m.group(0)
            if '<scale>' in blk:
                return blk
            return blk.replace('</uri>', f'</uri><scale>{scale} {scale} {scale}</scale>')
        txt = re.sub(r'<mesh>.*?</mesh>', add_scale, txt, flags=re.S)
    sdf.write_text(txt, encoding='utf-8')

    # 셔츠 텍스처 rename(고유) + 틴트 + .dae init_from 갱신
    tex_dir = dst / 'materials' / 'textures'
    dae_path = dst / 'meshes' / dae
    dae_txt = dae_path.read_text(encoding='utf-8', errors='ignore')
    for shirt in shirts:
        src_tex = tex_dir / shirt
        if not src_tex.exists():
            print(f'  ! 셔츠 텍스처 부재(건너뜀): {shirt}')
            continue
        new_tex = tex_dir / new_shirt
        tint(src_tex, new_tex, rgb)
        if new_tex != src_tex:
            src_tex.unlink()
        dae_txt = dae_txt.replace(shirt, new_shirt)
    dae_path.write_text(dae_txt, encoding='utf-8')
    print(f'  ok -> {dst.relative_to(REPO)} (shirt={new_shirt}, scale={scale})')


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for spec in SPEC:
        build_one(*spec)
    print('\n완료 — sim/models/ 에 4개 로컬 모델 생성. '
          'GZ_SIM_RESOURCE_PATH 에 sim/models 포함 필요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
