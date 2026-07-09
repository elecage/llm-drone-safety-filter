#!/usr/bin/env python3
"""experiment control panel — paper §C 실험 격자 웹 컨트롤 패널.

브라우저에서 scenario(S5-S8) · baseline(B0-B4) · fault(5종) · episode 를 클릭으로
선택 → 격자 미리보기 · JSON export · 단일 trial sim 환경 기동(up.sh).

## 실행

    PYTHONPATH=eval/runner:eval/baselines:eval/faults \
        .venv/bin/python3 scripts/experiment_panel.py

    # 또는 (sys.path 자동 설정됨)
    .venv/bin/python3 scripts/experiment_panel.py --port 8765

브라우저에서 http://127.0.0.1:8765 접속.

## 범위 (단계적, ADR 미정)

- 격자 구성·미리보기·export·명령 생성: 완전 동작 (host venv).
- 단일 trial sim 환경 기동: scripts/up.sh (장소 + tier1 mode) — Mac mini 콘솔 전용.
- 무인 배치 실행: runner.py (미구현, B7 #12 분할 2/N) 필요 — 본 패널 밖.

표준 라이브러리만 사용 (http.server) — 추가 의존성 없음.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]

# eval 패키지 + scenario_params(SCENARIO_LOCATION) + intent_llm(backbone registry) 경로.
for _pkg in ('eval/runner', 'eval/baselines', 'eval/faults',
             'sim/scenario_params', 'intent/llm'):
    _p = str(_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eval_runner import panel  # noqa: E402


# --------------------------------------------------------------------- HTML

_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>실험 컨트롤 패널 — paper §C</title>
<style>
  :root { --bg:#0f1419; --panel:#1a2129; --line:#2c3a47; --fg:#e6edf3;
          --muted:#8b98a5; --accent:#4493f8; --ok:#3fb950; --warn:#d29922; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  header { padding:16px 24px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header .sub { color:var(--muted); font-size:12px; margin-top:4px; }
  main { display:grid; grid-template-columns: 1fr 1fr; gap:16px; padding:24px;
         max-width:1200px; margin:0 auto; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:8px;
          padding:16px; }
  .card h2 { margin:0 0 12px; font-size:14px; color:var(--accent); }
  label.opt { display:flex; align-items:flex-start; gap:8px; padding:6px 8px;
              border-radius:6px; cursor:pointer; }
  label.opt:hover { background:#222c36; }
  label.opt input { margin-top:3px; }
  .opt .meta { color:var(--muted); font-size:12px; }
  .row { display:flex; align-items:center; gap:10px; margin:8px 0; flex-wrap:wrap; }
  input[type=number], select { background:#0d1117; color:var(--fg);
          border:1px solid var(--line); border-radius:6px; padding:6px 8px; }
  button { background:var(--accent); color:#fff; border:0; border-radius:6px;
           padding:8px 14px; cursor:pointer; font-size:13px; }
  button.ghost { background:transparent; border:1px solid var(--line); color:var(--fg); }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .total { font-size:28px; font-weight:700; }
  .total small { font-size:13px; color:var(--muted); font-weight:400; }
  table { width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }
  th, td { text-align:left; padding:4px 6px; border-bottom:1px solid var(--line);
           white-space:nowrap; }
  th { color:var(--muted); font-weight:600; }
  pre { background:#0d1117; border:1px solid var(--line); border-radius:6px;
        padding:10px; overflow:auto; font-size:12px; }
  .note { color:var(--warn); font-size:12px; }
  .muted { color:var(--muted); }
  .full { grid-column: 1 / -1; }
  .pill { display:inline-block; background:#222c36; border-radius:10px;
          padding:1px 8px; font-size:11px; color:var(--muted); }
  #msg { min-height:18px; font-size:12px; }
  .err { color:#f85149; } .ok { color:var(--ok); }
</style>
</head>
<body>
<header>
  <h1>실험 컨트롤 패널 <span class="pill">paper §C 격자</span></h1>
  <div class="sub">scenario × baseline × fault × episode 격자를 클릭으로 구성 ·
    미리보기 · export · 단일 trial sim 기동</div>
</header>
<main>
  <section class="card">
    <h2>시나리오 (장소 자동)</h2>
    <div id="scenarios"></div>
  </section>

  <section class="card">
    <h2>알고리즘 (baseline)</h2>
    <div id="baselines"></div>
  </section>

  <section class="card">
    <h2>Fault 채널</h2>
    <div id="faults"></div>
  </section>

  <section class="card">
    <h2>LLM backbone <span class="pill">run-level</span></h2>
    <div class="note" style="margin-bottom:6px">backbone 은 격자 차원이 아님 —
      선택 backbone 마다 격자 1회 run (paired 비교).</div>
    <div id="backbones"></div>
  </section>

  <section class="card">
    <h2>반복 · 동작</h2>
    <div class="row">
      <label>episodes <input type="number" id="nep" min="1" value="10" style="width:80px"></label>
    </div>
    <div class="row">
      <button id="btn-preview">미리보기</button>
      <button id="btn-export" class="ghost">JSON export</button>
    </div>
    <div id="msg"></div>
  </section>

  <section class="card full">
    <h2>격자 미리보기</h2>
    <div class="total"><span id="total">—</span> <small>trial</small>
      <span id="breakdown" class="muted"></span></div>
    <div id="locations" class="muted" style="margin-top:4px"></div>
    <div style="overflow:auto"><table id="sample"></table></div>
    <details style="margin-top:10px"><summary class="muted">재현 Python 스니펫</summary>
      <pre id="snippet">미리보기를 먼저 실행하세요.</pre></details>
  </section>

  <section class="card full">
    <h2>runner 무인 배치 실행 명령 <span class="pill">eval-runner</span></h2>
    <div class="row">
      <span id="runcount" class="muted"></span>
      <button id="btn-runner-cmd" class="ghost">명령 생성</button>
    </div>
    <pre id="runner-cmd">backbone 선택 후 명령 생성을 누르세요.</pre>
    <div class="note">runner 실 실행은 sim 스택(gz/PX4/ROS 2)이 필요 — Mac mini 콘솔.
      이 MacBook 에선 격자 구성·export·명령 생성·dry-run 까지 가능.</div>
  </section>

  <section class="card full">
    <h2>단일 trial sim 환경 기동 <span class="pill">대화형 · Mac mini 콘솔</span></h2>
    <div class="note">up.sh 는 sim 환경(장소 + tier1 mode)만 구성합니다.
      fault · intent layer · Tier 2 · trial rosbag 은 runner.py(미구현) 필요.</div>
    <div class="row" style="margin-top:10px">
      <label>scenario <select id="launch-scenario"></select></label>
      <label>baseline <select id="launch-baseline"></select></label>
      <label><input type="checkbox" id="launch-g2"> g2 waypoint</label>
      <button id="btn-cmd" class="ghost">명령 생성</button>
      <button id="btn-launch">기동</button>
    </div>
    <pre id="launch-cmd">명령 생성 또는 기동을 누르세요.</pre>
  </section>
</main>

<script>
const $ = (s) => document.querySelector(s);
const j = (el, t) => { el.textContent = t; };
let OPTIONS = null;

function msg(t, cls) { const m=$('#msg'); m.textContent=t; m.className=cls||''; }

function checked(sel) {
  return [...document.querySelectorAll(sel+' input:checked')].map(i=>i.value);
}

async function loadOptions() {
  const r = await fetch('/api/options'); OPTIONS = await r.json();
  const sc = $('#scenarios'); sc.innerHTML='';
  OPTIONS.scenarios.forEach(s => {
    sc.insertAdjacentHTML('beforeend',
      `<label class="opt"><input type="checkbox" value="${s.id}" checked>
       <span><b>${s.id}</b> <span class="meta">→ ${s.location}</span></span></label>`);
  });
  const bl = $('#baselines'); bl.innerHTML='';
  OPTIONS.baselines.forEach(b => {
    bl.insertAdjacentHTML('beforeend',
      `<label class="opt"><input type="checkbox" value="${b.mode}" checked>
       <span><b>${b.mode.toUpperCase()}</b>
       <span class="meta">tier1=${b.tier1_mode} · ctx=${b.context_aug} · tier2=${b.tier2_enabled}</span></span></label>`);
  });
  const fl = $('#faults'); fl.innerHTML='';
  OPTIONS.faults.forEach(f => {
    const v = f.variant ? ' · '+f.variant : '';
    fl.insertAdjacentHTML('beforeend',
      `<label class="opt"><input type="checkbox" value="${f.name}" checked>
       <span><b>${f.channel}</b> <span class="meta">${f.name}${v}</span></span></label>`);
  });
  const bb = $('#backbones'); bb.innerHTML='';
  OPTIONS.backbones.forEach(b => {
    const def = b.id === OPTIONS.defaults.backbone;
    bb.insertAdjacentHTML('beforeend',
      `<label class="opt"><input type="checkbox" value="${b.id}" ${def?'checked':''}>
       <span><b>${b.id}</b>${def?' <span class="meta">(default)</span>':''}</span></label>`);
  });
  $('#nep').value = OPTIONS.defaults.n_episodes;
  // launch selects
  const ls=$('#launch-scenario'), lb=$('#launch-baseline');
  OPTIONS.scenarios.forEach(s => ls.insertAdjacentHTML('beforeend',
    `<option value="${s.id}">${s.id} (${s.location})</option>`));
  OPTIONS.baselines.forEach(b => lb.insertAdjacentHTML('beforeend',
    `<option value="${b.mode}">${b.mode.toUpperCase()}</option>`));
}

function selection() {
  return {
    scenarios: checked('#scenarios'),
    baselines: checked('#baselines'),
    faults: checked('#faults'),
    n_episodes: parseInt($('#nep').value, 10),
  };
}

async function post(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                            body: JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || ('HTTP '+r.status));
  return data;
}

async function preview() {
  try {
    msg('미리보기 생성 중...', 'muted');
    const sel = selection();
    const d = await post('/api/preview', sel);
    $('#total').textContent = d.total.toLocaleString();
    const b = d.breakdown;
    $('#breakdown').textContent =
      ` = ${b.scenarios} scenario × ${b.baselines} baseline × ${b.faults} fault × ${b.episodes} ep`;
    $('#locations').textContent = '장소: ' + d.locations.join(', ');
    const rows = d.sample;
    const cols = ['trial_id','scenario_id','location','baseline','fault_channel','fault_variant','episode_id','seed'];
    let html = '<tr>'+cols.map(c=>`<th>${c}</th>`).join('')+'</tr>';
    rows.forEach(rec => { html += '<tr>'+cols.map(c=>`<td>${rec[c]===null?'—':rec[c]}</td>`).join('')+'</tr>'; });
    $('#sample').innerHTML = html;
    $('#snippet').textContent = d.python_snippet;
    msg(`미리보기 완료 — 샘플 ${rows.length}/${d.total} 행`, 'ok');
  } catch(e) { msg('오류: '+e.message, 'err'); }
}

async function exportGrid() {
  try {
    const sel = selection();
    const d = await post('/api/export', sel);
    msg(`export 완료 — ${d.total} trial → ${d.path}`, 'ok');
  } catch(e) { msg('오류: '+e.message, 'err'); }
}

async function launchCmd(confirmRun) {
  try {
    const body = {
      scenario_id: $('#launch-scenario').value,
      baseline: $('#launch-baseline').value,
      g2: $('#launch-g2').checked,
      confirm: !!confirmRun,
    };
    if (confirmRun && !confirm('Mac mini 콘솔에서 up.sh 를 기동합니다. 계속할까요?')) return;
    const d = await post('/api/launch', body);
    let txt = '$ ' + d.command + '\\n\\nenv: ' + JSON.stringify(d.env, null, 2);
    if (d.executed) txt += `\\n\\n[기동됨] pid=${d.pid} — Terminal 창과 gz GUI ▶ 확인`;
    else txt += '\\n\\n[생성만] 위 명령을 Mac mini 콘솔에서 실행하세요.';
    $('#launch-cmd').textContent = txt;
  } catch(e) { $('#launch-cmd').textContent = '오류: '+e.message; }
}

async function runnerCmd() {
  try {
    const sel = selection();
    const backbones = checked('#backbones');
    if (backbones.length === 0) {
      $('#runner-cmd').textContent = 'backbone 을 최소 1개 선택하세요.'; return;
    }
    const d = await post('/api/runner-command', {...sel, backbones});
    $('#runner-cmd').textContent = d.command;
  } catch(e) { $('#runner-cmd').textContent = '오류: '+e.message; }
}

function updateRunCount() {
  const total = parseInt(($('#total').textContent || '0').replace(/,/g,''), 10) || 0;
  const nbb = checked('#backbones').length;
  $('#runcount').textContent =
    `격자 ${total.toLocaleString()} trial × backbone ${nbb} = ${(total*nbb).toLocaleString()} trial-run`;
}

$('#btn-preview').onclick = async () => { await preview(); updateRunCount(); };
$('#btn-export').onclick = exportGrid;
$('#btn-cmd').onclick = () => launchCmd(false);
$('#btn-launch').onclick = () => launchCmd(true);
$('#btn-runner-cmd').onclick = runnerCmd;
document.addEventListener('change', (e) => {
  if (e.target.closest('#backbones')) updateRunCount();
});
loadOptions().then(preview).then(updateRunCount);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------- snippet


def _python_snippet(sel: dict) -> str:
    modes = ', '.join(f'BaselineMode.{m.upper()}' for m in sel['baselines'])
    names = '{' + ', '.join(repr(f) for f in sel['faults']) + '}'
    expect = (len(sel['scenarios']) * len(sel['baselines'])
              * len(sel['faults']) * sel['n_episodes'])
    return (
        'from eval_baselines.schemas import BaselineMode\n'
        'from eval_faults.fault_scenario import load_fault_scenario\n'
        'from eval_runner.grid import (generate_trial_grid, '
        'default_fault_scenario_paths)\n'
        f'names = {names}\n'
        'faults = [p for p in default_fault_scenario_paths()\n'
        '          if load_fault_scenario(p).name in names]\n'
        f'grid = generate_trial_grid({sel["scenarios"]!r}, [{modes}],\n'
        f'                           faults, n_episodes={sel["n_episodes"]})\n'
        f'assert len(grid) == {expect}'
    )


# --------------------------------------------------------------------- handler


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def log_message(self, *args) -> None:  # 조용히
        pass

    def do_GET(self) -> None:
        if self.path in ('/', '/index.html'):
            self._send(200, _HTML.encode('utf-8'), 'text/html; charset=utf-8')
        elif self.path == '/api/options':
            self._json(200, panel.build_options())
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:
        try:
            body = self._read_body()
            if self.path == '/api/preview':
                out = panel.build_grid_preview(
                    body['scenarios'], body['baselines'],
                    body['faults'], int(body['n_episodes']),
                )
                out['python_snippet'] = _python_snippet(body)
                self._json(200, out)
            elif self.path == '/api/runner-command':
                cmd = panel.runner_command(
                    body['scenarios'], body['baselines'],
                    body['faults'], int(body['n_episodes']),
                    body['backbones'],
                )
                self._json(200, {'command': cmd, 'n_backbones': len(body['backbones'])})
            elif self.path == '/api/export':
                default_out = _ROOT / 'results' / 'grids' / 'experiment_grid.json'
                out = panel.export_grid_json(
                    body['scenarios'], body['baselines'],
                    body['faults'], int(body['n_episodes']),
                    body.get('output_path', default_out),
                )
                self._json(200, out)
            elif self.path == '/api/launch':
                g2 = 'y0_yard_child_follow' if body.get('g2') else ''
                env = panel.up_sh_env_for_trial(
                    body['scenario_id'], body['baseline'], g2_scenario=g2,
                )
                cmd = (f"SCENARIO={env['SCENARIO']} TIER1_MODE={env['TIER1_MODE']} "
                       f"G2_SCENARIO={env['G2_SCENARIO']!r} ./scripts/up.sh")
                if not body.get('confirm'):
                    self._json(200, {'command': cmd, 'env': env, 'executed': False})
                else:
                    proc = subprocess.Popen(
                        ['bash', 'scripts/up.sh'], cwd=str(_ROOT),
                        env={**os.environ, **env}, start_new_session=True,
                    )
                    self._json(200, {'command': cmd, 'env': env,
                                     'executed': True, 'pid': proc.pid})
            else:
                self._json(404, {'error': 'not found'})
        except (KeyError, ValueError, TypeError) as exc:
            self._json(400, {'error': str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {'error': f'{type(exc).__name__}: {exc}'})


def main() -> None:
    ap = argparse.ArgumentParser(description='paper §C 실험 컨트롤 패널 (웹)')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true', help='브라우저 자동 열기 비활성화')
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    url = f'http://{args.host}:{args.port}'
    print(f'[panel] 실험 컨트롤 패널: {url}')
    print('[panel] 종료: Ctrl-C')
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[panel] 종료')
        server.shutdown()


if __name__ == '__main__':
    main()
