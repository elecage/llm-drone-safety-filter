#!/usr/bin/env python3
"""trial bag 의 신뢰도 신호 분석 — P3 진단 (c̃ 고착 여부).

/intent/grounding_confidence (Float32) + /intent/estimator/report (String JSON)
를 읽어 c 분포 + s1/s2/s3 + s1_reason 집계를 출력한다. 컨테이너에서:
  docker exec llmdrone-sim bash -c \
    'source /opt/ros/humble/setup.bash && source /workspace/install/setup.bash && \
     python3 /workspace/scripts/analyze_bag_confidence.py <bag_dir>'
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import rosbag2_py
from rclpy.serialization import deserialize_message
from std_msgs.msg import Float32, String


def _summ(vals):
    if not vals:
        return 'n=0'
    n = len(vals)
    return (f'n={n} min={min(vals):.4f} max={max(vals):.4f} '
            f'mean={sum(vals)/n:.4f} >0={sum(1 for v in vals if v > 1e-6)}/{n}')


def main() -> int:
    bag = sys.argv[1]
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=bag, storage_id='sqlite3'),
        rosbag2_py.ConverterOptions('', ''),
    )
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}

    gc = []
    reports = []
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic == '/intent/grounding_confidence':
            gc.append(deserialize_message(data, Float32).data)
        elif topic == '/intent/estimator/report':
            reports.append(deserialize_message(data, String).data)

    print(f'bag: {bag.split("/")[-1]}')
    print(f'grounding_confidence(c): {_summ(gc)}')

    # estimator report = JSON. 키 자동 탐색(c_tilde/c_raw/s1/s2/s3/s1_reason).
    parsed = []
    for r in reports:
        try:
            parsed.append(json.loads(r))
        except Exception:
            pass
    if parsed:
        keys = parsed[0].keys()
        print(f'report keys: {list(keys)}')
        for k in ('c_tilde', 'c_raw', 's1', 's2', 's3'):
            vals = [p[k] for p in parsed if isinstance(p.get(k), (int, float))]
            if vals:
                print(f'  {k}: {_summ(vals)}')
        for k in ('s1_reason', 'referent', 'grounded'):
            vals = [str(p.get(k)) for p in parsed if k in p]
            if vals:
                print(f'  {k}: {dict(Counter(vals).most_common(6))}')
        print(f'  [report 표본 0] {json.dumps(parsed[0], ensure_ascii=False)[:300]}')
        print(f'  [report 표본 중간] {json.dumps(parsed[len(parsed)//2], ensure_ascii=False)[:300]}')
    else:
        print('estimator report 파싱 실패 (JSON 아님?) — 원본 표본:')
        if reports:
            print(f'  {reports[len(reports)//2][:300]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
