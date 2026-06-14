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


def render_index_html(manifest: dict) -> str:
    summary = manifest.get('summary', {}) if isinstance(manifest.get('summary'), dict) else {}
    scorecard_summary = manifest.get('scorecard_summary', {}) if isinstance(manifest.get('scorecard_summary'), dict) else {}
    unsupported_layers = manifest.get('unsupported_layers', []) if isinstance(manifest.get('unsupported_layers'), list) else []
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

        scenario_sections.append(
            f"""
            <section class="scenario"> 
              <h2>{title}</h2>
              <p><strong>{scenario_id}</strong> · status <code>{status}</code> · verdict <code>{verdict}</code></p>
              <p>{summary_text}</p>
              <p><strong>Artifacts:</strong> <a href="{html.escape(str(evidence_paths.get('transcript', '')))}">transcript</a> · <a href="{html.escape(str(evidence_paths.get('timeline', '')))}">timeline</a> · <a href="{html.escape(str(evidence_paths.get('raw_result', '')))}">raw result</a></p>
              <p><strong>Metrics:</strong> {html.escape(json.dumps(metrics, sort_keys=True))}</p>
              <p><strong>Final state:</strong> {html.escape(json.dumps(final_state, sort_keys=True))}</p>
              <p><strong>Supported layers:</strong> {html.escape(', '.join(str(item) for item in supported_layers) or 'none')}</p>
              <p><strong>Unsupported layers:</strong> {html.escape(', '.join(str(item) for item in unsupported) or 'none')}</p>
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
        --bg: #f4efe7;
        --card: #fffaf4;
        --ink: #1f2933;
        --accent: #0b7285;
        --muted: #5c6773;
        --border: #d9cbb6;
      }}
      body {{
        margin: 0;
        padding: 2rem;
        background: linear-gradient(180deg, #f8f3ea 0%, #efe4d4 100%);
        color: var(--ink);
        font-family: Georgia, 'Times New Roman', serif;
      }}
      main {{ max-width: 960px; margin: 0 auto; }}
      .hero, .scenario {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 1.25rem 1.5rem;
        box-shadow: 0 18px 40px rgba(31, 41, 51, 0.08);
        margin-bottom: 1rem;
      }}
      h1, h2 {{ margin-top: 0; }}
      code {{ background: rgba(11, 114, 133, 0.08); padding: 0.1rem 0.35rem; border-radius: 6px; }}
      a {{ color: var(--accent); }}
      .meta {{ color: var(--muted); }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <h1>Voice Agent Reliability Lab</h1>
        <p>Private proof site for the seeded synthetic reliability bundle.</p>
        <p><strong>Generated:</strong> {html.escape(str(manifest.get('generated_at', 'unknown')))}</p>
        <p><strong>Runner version:</strong> {html.escape(str(manifest.get('runner_version', 'unknown')))}</p>
        <p><strong>Access boundary:</strong> {html.escape(str(manifest.get('access_boundary', DEFAULT_ACCESS_BOUNDARY)))}</p>
        <p><strong>Source manifest:</strong> <code>{html.escape(str(manifest.get('source_manifest_path', '')))}</code></p>
        <p><strong>Summary:</strong> {html.escape(json.dumps(summary, sort_keys=True))}</p>
        <p><strong>Average score:</strong> {html.escape(str(average_score))}</p>
        <p><strong>Unsupported layers:</strong> {html.escape(', '.join(str(item) for item in unsupported_layers) or 'none')}</p>
        <p><a href="manifest.json">manifest.json</a> · <a href="voice-lab-proof-latest.json">voice-lab-proof-latest.json</a> · <a href="bundle/manifest.json">bundle/manifest.json</a></p>
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
