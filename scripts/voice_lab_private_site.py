from __future__ import annotations

import argparse
import html
import importlib.util
import json
import shutil
import sys
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'apps' / 'api'))

from app.services.voice_lab import build_default_voice_lab_runner, seeded_voice_lab_scenarios


DEFAULT_ACCESS_BOUNDARY = 'Private localhost-only proof site bound to 127.0.0.1. Synthetic/demo data only.'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Build and serve the private Voice Lab proof site.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    build_parser = subparsers.add_parser('build', help='Generate a new proof bundle and static report site.')
    _add_common_site_args(build_parser)

    serve_parser = subparsers.add_parser('serve', help='Serve an existing static report site over HTTP.')
    serve_parser.add_argument('--site-root', type=Path, default=Path('artifacts/voice-lab-private-site/current'))
    serve_parser.add_argument('--host', default='127.0.0.1')
    serve_parser.add_argument('--port', type=int, default=18766)

    smoke_parser = subparsers.add_parser('smoke', help='Verify a served proof site and latest manifest.')
    smoke_parser.add_argument('--base-url', default='http://127.0.0.1:18766')

    args = parser.parse_args(argv)

    if args.command == 'build':
        manifest_path, site_root = build_private_site(
            artifact_root=args.artifact_root,
            site_root=args.site_root,
            access_boundary=args.access_boundary,
        )
        print(f'Saved voice lab proof manifest: {manifest_path}')
        print(f'Saved private proof site: {site_root}')
        return 0

    if args.command == 'serve':
        serve_private_site(site_root=args.site_root, host=args.host, port=args.port)
        return 0

    if args.command == 'smoke':
        smoke_private_site(args.base_url)
        return 0

    parser.error(f'Unsupported command: {args.command}')
    return 2


def _add_common_site_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--artifact-root', type=Path, default=Path('artifacts/voice-lab'))
    parser.add_argument('--site-root', type=Path, default=Path('artifacts/voice-lab-private-site/current'))
    parser.add_argument('--access-boundary', default=DEFAULT_ACCESS_BOUNDARY)


def build_private_site(*, artifact_root: Path, site_root: Path, access_boundary: str) -> tuple[Path, Path]:
    proof_module = _load_voice_lab_proof_module()
    runner = build_default_voice_lab_runner(PROJECT_ROOT)
    report = runner.run(seeded_voice_lab_scenarios())
    manifest_path = proof_module.write_evidence_bundle(report, _resolve_path(artifact_root))
    materialize_private_site(manifest_path=manifest_path, site_root=_resolve_path(site_root), access_boundary=access_boundary)
    return manifest_path, _resolve_path(site_root)


def materialize_private_site(*, manifest_path: Path, site_root: Path, access_boundary: str) -> Path:
    manifest = json.loads(Path(manifest_path).read_text())
    bundle_root = Path(manifest['bundle_root'])
    if not bundle_root.exists():
        raise FileNotFoundError(f'Bundle root is missing: {bundle_root}')

    if site_root.exists():
        shutil.rmtree(site_root)
    site_root.mkdir(parents=True, exist_ok=True)

    copied_bundle_root = site_root / 'bundle'
    shutil.copytree(bundle_root, copied_bundle_root)

    site_manifest = _manifest_for_site(manifest)
    site_manifest['access_boundary'] = access_boundary
    site_manifest['source_manifest_path'] = str(Path(manifest_path).resolve())
    site_manifest['source_bundle_root'] = str(bundle_root.resolve())

    _write_json(site_root / 'manifest.json', site_manifest)
    _write_json(site_root / 'voice-lab-proof-latest.json', site_manifest)
    (site_root / 'index.html').write_text(render_index_html(site_manifest))
    return site_root


def _manifest_for_site(manifest: dict) -> dict:
    site_manifest = deepcopy(manifest)
    site_manifest['bundle_root'] = 'bundle'
    for scenario in site_manifest.get('scenarios', []):
        scenario_id = scenario.get('scenario_id', 'unknown-scenario')
        scenario_dir = Path('bundle') / scenario_id
        timeline = scenario.get('timeline', {})
        if isinstance(timeline, dict):
            timeline['path'] = str(scenario_dir / 'timeline.json')
        evidence_paths = scenario.get('evidence_paths', {})
        if isinstance(evidence_paths, dict):
            evidence_paths['transcript'] = str(scenario_dir / 'transcript.txt')
            evidence_paths['timeline'] = str(scenario_dir / 'timeline.json')
            evidence_paths['raw_result'] = str(scenario_dir / 'result.json')
    return site_manifest


def _compact_label(value: object) -> str:
    return str(value).replace('_', ' ').strip().title()


def _format_list(items: list, *, limit: int = 4) -> str:
    values = [_compact_label(item) for item in items if str(item).strip()]
    if not values:
        return 'None'
    shown = values[:limit]
    suffix = f' +{len(values) - limit} more' if len(values) > limit else ''
    return ', '.join(shown) + suffix


def _scenario_insights(*, metrics: dict, final_state: dict, integration_status: dict) -> list[str]:
    insights = []
    turn_count = metrics.get('turn_count')
    event_count = metrics.get('platform_event_count')
    latency_marks = metrics.get('latency_mark_count')
    if turn_count is not None:
        insights.append(f'{turn_count} turns captured')
    if event_count is not None:
        insights.append(f'{event_count} platform events')
    if latency_marks is not None:
        insights.append(f'{latency_marks} latency marks')

    flow_state = final_state.get('flow_state')
    if flow_state:
        insights.append(f'Flow ended in {_compact_label(flow_state)}')

    fallback = final_state.get('demo_fallback')
    if isinstance(fallback, dict):
        mode = fallback.get('mode')
        if fallback.get('armed') and mode:
            insights.append(f'Fail-closed fallback armed: {_compact_label(mode)}')
        elif fallback.get('armed') is False:
            insights.append('Fallback cleared')

    operator = final_state.get('operator_steer')
    if isinstance(operator, dict):
        action = operator.get('lastAction')
        if action:
            insights.append(f'Operator action: {_compact_label(action)}')

    supported_layers = integration_status.get('supported_layers', [])
    if isinstance(supported_layers, list) and supported_layers:
        insights.append(f'{len(supported_layers)} supported proof layers')

    return insights[:6]


def render_index_html(manifest: dict) -> str:
    summary = manifest.get('summary', {}) if isinstance(manifest.get('summary'), dict) else {}
    scorecard_summary = manifest.get('scorecard_summary', {}) if isinstance(manifest.get('scorecard_summary'), dict) else {}
    unsupported_layers = manifest.get('unsupported_layers', []) if isinstance(manifest.get('unsupported_layers'), list) else []
    pass_count = summary.get('pass_count', 0)
    blocked_count = summary.get('blocked_count', 0)
    fail_count = summary.get('fail_count', 0)
    scenario_count = summary.get('scenario_count', 0)
    generated_at = html.escape(str(manifest.get('generated_at', 'unknown')))
    source_manifest = html.escape(str(manifest.get('source_manifest_path', '')))
    scenario_sections = []
    for scenario in manifest.get('scenarios', []):
        if not isinstance(scenario, dict):
            continue
        scenario_id = html.escape(str(scenario.get('scenario_id', 'unknown-scenario')))
        title = html.escape(str(scenario.get('title', 'Untitled scenario')))
        summary_text = html.escape(str(scenario.get('summary', '')))
        status = html.escape(str(scenario.get('status', 'unknown')))
        verdict = html.escape(str(scenario.get('verdict', 'unknown')))
        metrics = scenario.get('metrics', {}) if isinstance(scenario.get('metrics'), dict) else {}
        final_state = scenario.get('final_state', {}) if isinstance(scenario.get('final_state'), dict) else {}
        integration_status = scenario.get('integration_status', {}) if isinstance(scenario.get('integration_status'), dict) else {}
        evidence_paths = scenario.get('evidence_paths', {}) if isinstance(scenario.get('evidence_paths'), dict) else {}
        supported_layers = integration_status.get('supported_layers', []) if isinstance(integration_status.get('supported_layers'), list) else []
        unsupported = integration_status.get('unsupported_layers', []) if isinstance(integration_status.get('unsupported_layers'), list) else []
        insights = ''.join(f'<li>{html.escape(item)}</li>' for item in _scenario_insights(metrics=metrics, final_state=final_state, integration_status=integration_status))
        supported_summary = html.escape(_format_list(supported_layers, limit=3))
        unsupported_summary = html.escape(_format_list(unsupported, limit=3))

        scenario_sections.append(
            f"""
            <section class="scenario"> 
              <div class="scenario-head">
                <div>
                  <p class="eyebrow">{scenario_id}</p>
                  <h2>{title}</h2>
                </div>
                <span class="status status-{verdict}">{verdict}</span>
              </div>
              <p class="summary">{summary_text}</p>
              <ul class="insights">{insights}</ul>
              <div class="layer-grid">
                <div>
                  <span>Supported</span>
                  <strong>{supported_summary}</strong>
                </div>
                <div>
                  <span>Still Missing</span>
                  <strong>{unsupported_summary}</strong>
                </div>
              </div>
              <div class="actions">
                <a href="{html.escape(str(evidence_paths.get('transcript', '')))}">Transcript</a>
                <a href="{html.escape(str(evidence_paths.get('timeline', '')))}">Timeline</a>
                <a href="{html.escape(str(evidence_paths.get('raw_result', '')))}">Raw Result</a>
              </div>
            </section>
            """
        )

    scenario_html = '\n'.join(scenario_sections) if scenario_sections else '<p>No scenarios recorded.</p>'
    average_score = scorecard_summary.get('average_overall_score')
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Voice Agent Reliability Lab Private Proof</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f8fb;
        --panel: #ffffff;
        --ink: #16202a;
        --accent: #0f766e;
        --accent-soft: #dff7f4;
        --muted: #657282;
        --border: #d7dee8;
        --warn: #9a5b12;
      }}
      body {{
        margin: 0;
        background: var(--bg);
        color: var(--ink);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
      .hero {{
        padding: 28px 0 22px;
      }}
      .eyebrow {{
        color: var(--accent);
        font-size: 0.76rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        margin: 0 0 8px;
        text-transform: uppercase;
      }}
      h1 {{
        font-size: 2.4rem;
        line-height: 1.05;
        margin: 0 0 10px;
      }}
      h2 {{ margin: 0; font-size: 1.1rem; }}
      .lede {{
        color: var(--muted);
        font-size: 1.05rem;
        max-width: 760px;
        margin: 0;
      }}
      .dashboard {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin: 22px 0;
      }}
      .explain-grid {{
        display: grid;
        gap: 12px;
        grid-template-columns: 1.1fr 1fr 1fr;
        margin: 0 0 22px;
      }}
      .metric, .scenario {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        box-shadow: 0 10px 28px rgba(22, 32, 42, 0.06);
      }}
      .explain {{
        background: #ffffff;
        border: 1px solid var(--border);
        border-left: 4px solid var(--accent);
        border-radius: 8px;
        padding: 16px;
      }}
      .explain h2 {{
        font-size: 0.98rem;
        margin-bottom: 8px;
      }}
      .explain p {{
        color: var(--muted);
        line-height: 1.45;
        margin: 0;
      }}
      .metric {{ padding: 16px; }}
      .metric span, .layer-grid span {{
        color: var(--muted);
        display: block;
        font-size: 0.78rem;
        font-weight: 700;
        margin-bottom: 5px;
        text-transform: uppercase;
      }}
      .metric strong {{ font-size: 1.8rem; }}
      .scenario {{ padding: 18px; margin-bottom: 14px; }}
      .scenario-head {{
        align-items: flex-start;
        display: flex;
        gap: 16px;
        justify-content: space-between;
      }}
      .status {{
        background: var(--accent-soft);
        border-radius: 999px;
        color: var(--accent);
        font-size: 0.78rem;
        font-weight: 800;
        padding: 6px 10px;
        text-transform: uppercase;
      }}
      .status-fail {{ background: #fee2e2; color: #b91c1c; }}
      .status-blocked {{ background: #fff7ed; color: var(--warn); }}
      .summary {{ color: var(--ink); line-height: 1.45; }}
      .insights {{
        display: grid;
        gap: 8px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        list-style: none;
        margin: 14px 0;
        padding: 0;
      }}
      .insights li {{
        background: #f7fafc;
        border: 1px solid #e7edf4;
        border-radius: 8px;
        color: #2d3b49;
        font-size: 0.9rem;
        padding: 10px;
      }}
      .layer-grid {{
        display: grid;
        gap: 12px;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin-top: 14px;
      }}
      .layer-grid div {{
        border-top: 1px solid var(--border);
        padding-top: 12px;
      }}
      .layer-grid strong {{ font-size: 0.95rem; }}
      .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
      .actions a, .hero-actions a {{
        align-items: center;
        border: 1px solid var(--border);
        border-radius: 8px;
        display: inline-flex;
        font-weight: 700;
        min-height: 36px;
        padding: 0 12px;
        text-decoration: none;
      }}
      .hero-actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
      code {{
        background: #eef3f8;
        border-radius: 6px;
        font-size: 0.85rem;
        padding: 0.15rem 0.35rem;
      }}
      a {{ color: var(--accent); }}
      .meta {{ color: var(--muted); font-size: 0.92rem; }}
      @media (max-width: 760px) {{
        main {{ padding: 18px; }}
        h1 {{ font-size: 1.8rem; }}
        .dashboard, .explain-grid, .insights, .layer-grid {{ grid-template-columns: 1fr; }}
        .scenario-head {{ align-items: stretch; flex-direction: column; }}
        .status {{ width: fit-content; }}
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <p class="eyebrow">Local Proof Environment</p>
        <h1>Voice Agent Reliability Lab</h1>
        <p class="lede">Private localhost dashboard for the current synthetic reliability proof bundle. It shows what passed, what evidence was captured, and which live voice layers are still outside this proof tier.</p>
        <div class="dashboard">
          <div class="metric"><span>Scenarios</span><strong>{html.escape(str(scenario_count))}</strong></div>
          <div class="metric"><span>Passed</span><strong>{html.escape(str(pass_count))}</strong></div>
          <div class="metric"><span>Blocked</span><strong>{html.escape(str(blocked_count))}</strong></div>
          <div class="metric"><span>Failed</span><strong>{html.escape(str(fail_count))}</strong></div>
        </div>
        <div class="explain-grid">
          <section class="explain">
            <h2>What You Are Looking At</h2>
            <p>A generated reliability report from three synthetic voice-agent scenarios. Each scenario links to the transcript, event timeline, and raw result that produced the pass/fail verdict.</p>
          </section>
          <section class="explain">
            <h2>Why It Is Valuable</h2>
            <p>It turns a demo conversation into inspectable engineering evidence: task outcome, tool behavior, fallback handling, latency marks, and missing live-media layers are visible in one bundle.</p>
          </section>
          <section class="explain">
            <h2>What It Does Not Prove Yet</h2>
            <p>This page proves the report/evidence loop. Live ASR, TTS, SIP trunking, WebRTC media, waveform capture, and barge-in still need higher-tier proof before customer-facing claims.</p>
          </section>
        </div>
        <p class="meta">Generated {generated_at}. Average score: {html.escape(str(average_score))}. Unsupported layers: {html.escape(_format_list(unsupported_layers, limit=8))}.</p>
        <p class="meta">Source manifest: <code>{source_manifest}</code></p>
        <div class="hero-actions">
          <a href="manifest.json">Site Manifest</a>
          <a href="voice-lab-proof-latest.json">Latest Proof JSON</a>
          <a href="bundle/manifest.json">Bundle Manifest</a>
        </div>
      </section>
      {scenario_html}
    </main>
  </body>
</html>
"""


def serve_private_site(*, site_root: Path, host: str, port: int) -> None:
    resolved_site_root = _resolve_path(site_root)
    if not (resolved_site_root / 'index.html').exists():
        raise FileNotFoundError(f'Missing static site index: {resolved_site_root / "index.html"}')

    handler = partial(SimpleHTTPRequestHandler, directory=str(resolved_site_root))
    server = ThreadingHTTPServer((host, port), handler)
    print(f'Serving Voice Lab private proof site at http://{host}:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def smoke_private_site(base_url: str) -> None:
    import urllib.request

    normalized_base = base_url.rstrip('/')
    index_response = urllib.request.urlopen(f'{normalized_base}/', timeout=10)
    manifest_response = urllib.request.urlopen(f'{normalized_base}/manifest.json', timeout=10)
    if index_response.status != 200:
        raise RuntimeError(f'Unexpected index status: {index_response.status}')
    if manifest_response.status != 200:
        raise RuntimeError(f'Unexpected manifest status: {manifest_response.status}')

    manifest = json.loads(manifest_response.read().decode('utf-8'))
    summary = manifest.get('summary', {}) if isinstance(manifest.get('summary'), dict) else {}
    scenario_count = summary.get('scenario_count', 0)
    if scenario_count < 1:
        raise RuntimeError('Smoke check failed: manifest reports zero scenarios.')
    print(json.dumps({'base_url': normalized_base, 'summary': summary}, indent=2))


def _load_voice_lab_proof_module():
    script_path = PROJECT_ROOT / 'scripts' / 'voice_lab_proof.py'
    spec = importlib.util.spec_from_file_location('voice_lab_proof_script', script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load proof module from {script_path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(f'{json.dumps(payload, indent=2)}\n')


if __name__ == '__main__':
    raise SystemExit(main())
