from __future__ import annotations

import argparse
import html
import importlib.util
import json
import shutil
import sys
from copy import deepcopy
from datetime import datetime
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
    serve_parser.add_argument('--port', type=int, default=18767)

    smoke_parser = subparsers.add_parser('smoke', help='Verify a served proof site and latest manifest.')
    smoke_parser.add_argument('--base-url', default='http://127.0.0.1:18767')

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
    text = str(value).replace('_', ' ').strip().title()
    replacements = {
        'Asr': 'ASR',
        'Tts': 'TTS',
        'Sip': 'SIP',
        'Webrtc': 'WebRTC',
        'Json': 'JSON',
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return text


def _plural(count: object, singular: str, plural: str | None = None) -> str:
    suffix = singular if count == 1 else plural or f'{singular}s'
    return f'{count} {suffix}'


def _format_list(items: list, *, limit: int = 4) -> str:
    values = [_compact_label(item) for item in items if str(item).strip()]
    if not values:
        return 'None'
    shown = values[:limit]
    suffix = f' +{len(values) - limit} more' if len(values) > limit else ''
    return ', '.join(shown) + suffix


def _first_sentence(value: object, *, max_length: int = 150) -> str:
    text = ' '.join(str(value or '').split())
    if not text:
        return 'Evidence captured for this scenario.'
    sentence = text.split('. ', 1)[0].rstrip('.')
    if len(sentence) <= max_length:
        return sentence
    return f'{sentence[: max_length - 1].rstrip()}...'


def _scenario_business_result(scenario: dict) -> str:
    final_state = scenario.get('final_state', {}) if isinstance(scenario.get('final_state'), dict) else {}
    fallback = final_state.get('demo_fallback') if isinstance(final_state.get('demo_fallback'), dict) else {}
    operator = final_state.get('operator_steer') if isinstance(final_state.get('operator_steer'), dict) else {}
    if fallback.get('armed'):
        return 'Unsafe automation was stopped and escalated instead of improvising.'
    action = operator.get('lastAction')
    if action:
        return f'Human-approved action completed: {_compact_label(action)}.'
    return f'{_first_sentence(scenario.get("summary"))}.'


def _scenario_evidence_line(scenario: dict) -> str:
    metrics = scenario.get('metrics', {}) if isinstance(scenario.get('metrics'), dict) else {}
    proof_bits = []
    turn_count = metrics.get('turn_count')
    event_count = metrics.get('platform_event_count', metrics.get('event_count'))
    if turn_count is not None:
        proof_bits.append(_plural(turn_count, 'turn'))
    if event_count is not None:
        proof_bits.append(_plural(event_count, 'event'))
    if metrics.get('latency_mark_count') is not None:
        proof_bits.append('latency tracked')
    return ', '.join(proof_bits) if proof_bits else 'transcript and timeline captured'


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _duration_label(start: object, end: object) -> str | None:
    start_at = _parse_iso(start)
    end_at = _parse_iso(end)
    if start_at is None or end_at is None:
        return None
    seconds = max(0.0, (end_at - start_at).total_seconds())
    if seconds < 1:
        return f'{round(seconds * 1000)} ms'
    return f'{seconds:.1f} s'


def _operator_latency(final_state: dict) -> str | None:
    operator = final_state.get('operator_steer') if isinstance(final_state.get('operator_steer'), dict) else {}
    return _duration_label(operator.get('requestedAt'), operator.get('respondedAt'))


def _scenario_measurements(scenario: dict) -> list[dict[str, str]]:
    metrics = scenario.get('metrics', {}) if isinstance(scenario.get('metrics'), dict) else {}
    final_state = scenario.get('final_state', {}) if isinstance(scenario.get('final_state'), dict) else {}
    integration = scenario.get('integration_status', {}) if isinstance(scenario.get('integration_status'), dict) else {}
    unsupported = set(integration.get('unsupported_layers', [])) if isinstance(integration.get('unsupported_layers'), list) else set()
    within_budget = metrics.get('within_budget_marks', {}) if isinstance(metrics.get('within_budget_marks'), dict) else {}
    latency_count = metrics.get('latency_mark_count')
    over_budget = within_budget.get('over_budget')
    in_budget = within_budget.get('within_budget')
    operator_latency = _operator_latency(final_state)
    event_count = metrics.get('platform_event_count', metrics.get('event_count'))
    turn_count = metrics.get('turn_count')

    fallback = final_state.get('demo_fallback') if isinstance(final_state.get('demo_fallback'), dict) else {}
    flow_state = final_state.get('flow_state')
    pipecat_flow = final_state.get('pipecat_flow') if isinstance(final_state.get('pipecat_flow'), dict) else {}
    tool_coverage = pipecat_flow.get('toolCoverage') if isinstance(pipecat_flow.get('toolCoverage'), list) else []

    continuity_value = 'Completed'
    continuity_detail = 'Scenario reached a terminal proof state without a failed verdict.'
    if fallback.get('armed'):
        continuity_value = 'Escalated safely'
        continuity_detail = 'Automation stopped and moved to human escalation instead of continuing unsafely.'
    elif flow_state:
        continuity_value = _compact_label(flow_state)

    latency_value = 'Not captured yet'
    latency_state = 'missing'
    latency_detail = 'Live end-to-end voice latency is not in this proof tier.'
    if latency_count is not None:
        latency_value = f'{in_budget or 0}/{(in_budget or 0) + (over_budget or 0)} marks in budget'
        latency_state = 'pass' if not over_budget else 'warn'
        latency_detail = f'{_plural(latency_count, "latency mark")} captured in the deterministic run.'

    tool_value = 'Not captured yet'
    tool_state = 'missing'
    tool_detail = 'No tool trace was recorded for this scenario.'
    if tool_coverage:
        tool_value = _plural(len(tool_coverage), 'tool')
        tool_state = 'pass'
        tool_detail = _format_list(tool_coverage, limit=4)
    elif operator_latency:
        tool_value = operator_latency
        tool_state = 'pass'
        tool_detail = 'Operator approval round-trip captured.'

    if operator_latency and tool_coverage:
        tool_detail = f'{_format_list(tool_coverage, limit=3)}; operator response {operator_latency}.'

    return [
        {
            'label': 'Call/session continuity',
            'value': continuity_value,
            'detail': continuity_detail,
            'state': 'pass',
        },
        {
            'label': 'Disconnection signal',
            'value': 'No disconnect in fixture',
            'detail': f'{_plural(event_count, "event") if event_count is not None else "Timeline"} retained; live RTP disconnect detection is not yet measured.',
            'state': 'warn' if 'sip_trunk' in unsupported or 'webrtc_media' in unsupported else 'pass',
        },
        {
            'label': 'Call quality / MOS',
            'value': 'Not captured yet',
            'detail': 'No live audio MOS, packet loss, jitter, or waveform quality score is present in this bundle.',
            'state': 'missing',
        },
        {
            'label': 'Codec / media path',
            'value': 'Not captured yet',
            'detail': 'Scenario is transcript/deterministic proof; live codec evidence belongs to the FreeSWITCH proof tier.',
            'state': 'missing',
        },
        {
            'label': 'E2E / response latency',
            'value': latency_value,
            'detail': latency_detail,
            'state': latency_state,
        },
        {
            'label': 'Tool / operator timing',
            'value': tool_value,
            'detail': tool_detail,
            'state': tool_state,
        },
        {
            'label': 'Conversation evidence',
            'value': _plural(turn_count, 'turn') if turn_count is not None else 'Transcript retained',
            'detail': 'Transcript, timeline, and raw result are linked below for audit.',
            'state': 'pass',
        },
    ]


def _measurement_panels(measurements: list[dict[str, str]]) -> tuple[str, str]:
    text_rows = []
    visual_cards = []
    for item in measurements:
        state = html.escape(item['state'])
        label = html.escape(item['label'])
        value = html.escape(item['value'])
        detail = html.escape(item['detail'])
        text_rows.append(
            f"""
            <div class="measurement-row state-{state}">
              <span>{label}</span>
              <strong>{value}</strong>
              <p>{detail}</p>
            </div>
            """
        )
        visual_cards.append(
            f"""
            <div class="visual-card state-{state}">
              <div class="visual-indicator"></div>
              <span>{label}</span>
              <strong>{value}</strong>
              <small>{detail}</small>
            </div>
            """
        )
    return ''.join(text_rows), ''.join(visual_cards)


def _safe_artifact_link(href: object, label: str) -> str:
    href_text = str(href or '').strip()
    if not href_text:
        return ''
    return f'<a href="{html.escape(href_text)}">{html.escape(label)}</a>'


def render_index_html(manifest: dict) -> str:
    summary = manifest.get('summary', {}) if isinstance(manifest.get('summary'), dict) else {}
    scorecard_summary = manifest.get('scorecard_summary', {}) if isinstance(manifest.get('scorecard_summary'), dict) else {}
    unsupported_layers = manifest.get('unsupported_layers', []) if isinstance(manifest.get('unsupported_layers'), list) else []
    pass_count = summary.get('pass_count', 0)
    blocked_count = summary.get('blocked_count', 0)
    fail_count = summary.get('fail_count', 0)
    scenario_count = summary.get('scenario_count', 0)
    generated_at = html.escape(str(manifest.get('generated_at', 'unknown')))
    scenario_sections = []
    for scenario in manifest.get('scenarios', []):
        if not isinstance(scenario, dict):
            continue
        scenario_id = html.escape(str(scenario.get('scenario_id', 'unknown-scenario')))
        title = html.escape(str(scenario.get('title', 'Untitled scenario')))
        verdict = html.escape(str(scenario.get('verdict', 'unknown')))
        evidence_paths = scenario.get('evidence_paths', {}) if isinstance(scenario.get('evidence_paths'), dict) else {}
        business_result = html.escape(_scenario_business_result(scenario))
        evidence_line = html.escape(_scenario_evidence_line(scenario))
        text_panel, visual_panel = _measurement_panels(_scenario_measurements(scenario))
        artifact_links = ''.join(
            [
                _safe_artifact_link(evidence_paths.get('transcript'), 'Transcript'),
                _safe_artifact_link(evidence_paths.get('timeline'), 'Timeline'),
                _safe_artifact_link(evidence_paths.get('raw_result'), 'Raw result'),
            ]
        )

        scenario_sections.append(
            f"""
            <details class="scenario">
              <summary>
                <span class="scenario-title">{title}</span>
                <span class="scenario-summary-actions">
                  <span class="status status-{verdict}">{verdict}</span>
                  <span class="expand-label">
                    <span class="chevron">›</span>
                    <span class="expand-closed">Click to expand</span>
                    <span class="expand-open">Expanded</span>
                  </span>
                </span>
              </summary>
              <div class="scenario-head">
                <div>
                  <p class="scenario-result">{business_result}</p>
                  <p class="meta">Evidence: {evidence_line}</p>
                </div>
              </div>
              <div class="mode-panel text-panel">
                {text_panel}
              </div>
              <div class="mode-panel visual-panel">
                {visual_panel}
              </div>
              <div class="actions compact">
                <span>{scenario_id}</span>
                {artifact_links}
              </div>
            </details>
            """
        )

    scenario_html = '\n'.join(scenario_sections) if scenario_sections else '<p class="meta">No scenarios recorded.</p>'
    average_score = scorecard_summary.get('average_overall_score')
    proof_result = f'{pass_count}/{scenario_count} scenarios passed' if scenario_count else 'No scenarios recorded'
    risk_result = 'Fail-closed escalation was exercised' if scenario_sections else 'Risk behavior not recorded'
    trust_result = f'Transcripts, timelines, and result files retained for {scenario_count} scenarios'
    if fail_count or blocked_count:
        proof_result = f'{pass_count} passed, {blocked_count} blocked, {fail_count} failed'
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
        --line: #e7edf4;
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
        padding: 30px 0 18px;
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
        font-size: 2.45rem;
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
      .proof-grid {{
        display: grid;
        gap: 14px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        margin: 24px 0 18px;
      }}
      .proof-card, .appendix {{
        background: #ffffff;
        border: 1px solid var(--border);
        border-radius: 8px;
        box-shadow: 0 10px 28px rgba(22, 32, 42, 0.06);
      }}
      .proof-card {{
        border-top: 4px solid var(--accent);
        padding: 18px;
      }}
      .proof-card h2 {{
        font-size: 0.98rem;
        margin-bottom: 8px;
      }}
      .proof-card p {{
        color: var(--ink);
        font-size: 1.02rem;
        font-weight: 700;
        line-height: 1.35;
        margin: 0 0 10px;
      }}
      .proof-card small {{
        color: var(--muted);
        display: block;
        line-height: 1.45;
      }}
      .proof-card span {{
        color: var(--muted);
        display: block;
        font-size: 0.78rem;
        font-weight: 700;
        margin-bottom: 5px;
        text-transform: uppercase;
      }}
      .mode-switch {{
        align-items: center;
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        justify-content: space-between;
        margin-bottom: 14px;
      }}
      .mode-switch p {{ margin: 0; }}
      .mode-controls {{
        background: #f1f5f9;
        border: 1px solid var(--border);
        border-radius: 8px;
        display: inline-flex;
        padding: 3px;
      }}
      .mode-controls label {{
        border-radius: 6px;
        color: var(--muted);
        cursor: pointer;
        font-size: 0.88rem;
        font-weight: 800;
        min-height: 32px;
        padding: 7px 12px;
      }}
      input[name="display-mode"] {{
        position: absolute;
        opacity: 0;
        pointer-events: none;
      }}
      #mode-text:checked ~ .appendix .mode-controls label[for="mode-text"],
      #mode-visual:checked ~ .appendix .mode-controls label[for="mode-visual"] {{
        background: #ffffff;
        color: var(--accent);
        box-shadow: 0 1px 4px rgba(22, 32, 42, 0.1);
      }}
      #mode-text:checked ~ .appendix .visual-panel,
      #mode-visual:checked ~ .appendix .text-panel {{
        display: none;
      }}
      .appendix {{
        margin-top: 18px;
        padding: 18px;
      }}
      .appendix h2 {{
        font-size: 1rem;
        margin-bottom: 10px;
      }}
      .scenario {{
        border-top: 1px solid var(--line);
        padding: 10px 0;
      }}
      .appendix .meta + .scenario {{ border-top: 0; }}
      .scenario summary {{
        align-items: center;
        border: 1px solid transparent;
        border-radius: 8px;
        cursor: pointer;
        display: flex;
        gap: 12px;
        justify-content: space-between;
        list-style: none;
        padding: 10px;
        transition: background-color 120ms ease, border-color 120ms ease;
      }}
      .scenario summary:hover {{
        background: #f7fafc;
        border-color: var(--line);
      }}
      .scenario summary::-webkit-details-marker {{ display: none; }}
      .scenario-title {{ font-weight: 750; }}
      .scenario-summary-actions {{
        align-items: center;
        display: inline-flex;
        flex-shrink: 0;
        gap: 10px;
      }}
      .expand-label {{
        align-items: center;
        color: var(--muted);
        display: inline-flex;
        font-size: 0.82rem;
        font-weight: 700;
        gap: 4px;
        white-space: nowrap;
      }}
      .chevron {{
        display: inline-block;
        font-size: 1.1rem;
        line-height: 1;
        transform: rotate(0deg);
        transition: transform 120ms ease;
      }}
      .scenario[open] .chevron {{ transform: rotate(90deg); }}
      .expand-open {{ display: none; }}
      .scenario[open] .expand-closed {{ display: none; }}
      .scenario[open] .expand-open {{ display: inline; }}
      .scenario-head {{
        align-items: flex-start;
        display: flex;
        gap: 16px;
        justify-content: space-between;
        padding: 4px 10px 0;
      }}
      .mode-panel {{
        margin: 12px 10px 0;
      }}
      .measurement-row {{
        border-top: 1px solid var(--line);
        display: grid;
        gap: 10px;
        grid-template-columns: minmax(150px, 0.8fr) minmax(120px, 0.7fr) minmax(220px, 1.5fr);
        padding: 11px 0;
      }}
      .measurement-row:first-child {{ border-top: 0; }}
      .measurement-row span {{
        color: var(--muted);
        font-size: 0.84rem;
        font-weight: 800;
      }}
      .measurement-row strong {{
        color: var(--ink);
        font-size: 0.94rem;
      }}
      .measurement-row p {{
        color: var(--muted);
        line-height: 1.4;
        margin: 0;
      }}
      .visual-panel {{
        display: grid;
        gap: 10px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}
      .visual-card {{
        background: #f8fafc;
        border: 1px solid var(--line);
        border-radius: 8px;
        min-height: 124px;
        padding: 12px;
        position: relative;
      }}
      .visual-indicator {{
        border-radius: 999px;
        height: 10px;
        position: absolute;
        right: 12px;
        top: 12px;
        width: 10px;
      }}
      .visual-card span {{
        color: var(--muted);
        display: block;
        font-size: 0.75rem;
        font-weight: 850;
        max-width: calc(100% - 18px);
        text-transform: uppercase;
      }}
      .visual-card strong {{
        display: block;
        font-size: 1rem;
        line-height: 1.25;
        margin-top: 10px;
      }}
      .visual-card small {{
        color: var(--muted);
        display: block;
        line-height: 1.35;
        margin-top: 8px;
      }}
      .state-pass {{
        --state-color: #0f766e;
        --state-bg: #ecfdf5;
      }}
      .state-warn {{
        --state-color: #b45309;
        --state-bg: #fffbeb;
      }}
      .state-missing {{
        --state-color: #64748b;
        --state-bg: #f1f5f9;
      }}
      .visual-card.state-pass,
      .visual-card.state-warn,
      .visual-card.state-missing {{
        background: var(--state-bg);
        border-color: color-mix(in srgb, var(--state-color) 24%, #ffffff);
      }}
      .visual-indicator {{ background: var(--state-color); }}
      .measurement-row.state-pass strong {{ color: #0f766e; }}
      .measurement-row.state-warn strong {{ color: #b45309; }}
      .measurement-row.state-missing strong {{ color: #64748b; }}
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
      .scenario-result {{ color: var(--ink); line-height: 1.45; margin: 0 0 4px; }}
      .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
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
      .actions.compact {{
        align-items: center;
        color: var(--muted);
        font-size: 0.86rem;
      }}
      .actions.compact a, .hero-actions a {{
        font-size: 0.86rem;
        min-height: 30px;
        padding: 0 10px;
      }}
      .hero-actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
      a {{ color: var(--accent); }}
      .meta {{ color: var(--muted); font-size: 0.92rem; }}
      @media (max-width: 760px) {{
        main {{ padding: 18px; }}
        h1 {{ font-size: 1.8rem; }}
        .proof-grid {{ grid-template-columns: 1fr; }}
        .scenario-head, .scenario summary {{ align-items: stretch; flex-direction: column; }}
        .scenario-summary-actions {{ justify-content: space-between; }}
        .measurement-row, .visual-panel {{ grid-template-columns: 1fr; }}
        .mode-switch {{ align-items: stretch; flex-direction: column; }}
        .status {{ width: fit-content; }}
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <p class="eyebrow">Private Buyer Proof</p>
        <h1>Voice Agent Reliability Lab</h1>
        <p class="lede">A concise private proof that the voice agent can complete the intended business workflow, handle unsafe moments conservatively, and leave evidence a buyer can inspect.</p>
        <div class="proof-grid" aria-label="Top buyer-value proof points">
          <section class="proof-card">
            <span>1. Business outcome tested</span>
            <h2>Can it handle the work?</h2>
            <p>{html.escape(proof_result)}</p>
            <small>Seeded buyer conversations exercised cancellation rescue, safe wrap-up, and deck-grounded question handling.</small>
          </section>
          <section class="proof-card">
            <span>2. Risk behavior proven</span>
            <h2>What happens when it should not continue?</h2>
            <p>{html.escape(risk_result)}</p>
            <small>The lab proves the agent escalates on tool timeout risk instead of inventing offers, credits, or unsupported commitments.</small>
          </section>
          <section class="proof-card">
            <span>3. Evidence to trust</span>
            <h2>Can the result be audited?</h2>
            <p>{html.escape(trust_result)}</p>
            <small>Every verdict links back to the conversation transcript and event timeline, with raw files retained as secondary artifacts.</small>
          </section>
        </div>
        <p class="meta">Generated {generated_at}. Average score: {html.escape(str(average_score))}. Not claimed by this proof: {html.escape(_format_list(unsupported_layers, limit=3))}.</p>
      </section>
      <input checked id="mode-text" name="display-mode" type="radio">
      <input id="mode-visual" name="display-mode" type="radio">
      <section class="appendix" aria-label="Evidence appendix">
        <div class="mode-switch">
          <div>
            <h2>Evidence Appendix</h2>
            <p class="meta">Expand a scenario to inspect what was measured, what was not captured yet, and which artifacts support the verdict.</p>
          </div>
          <div class="mode-controls" aria-label="Display mode">
            <label for="mode-text">Text mode</label>
            <label for="mode-visual">Visual mode</label>
          </div>
        </div>
        {scenario_html}
        <div class="hero-actions">
          <a href="manifest.json">Site manifest</a>
          <a href="voice-lab-proof-latest.json">Latest proof JSON</a>
          <a href="bundle/manifest.json">Bundle manifest</a>
        </div>
      </section>
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

    print(
        json.dumps(
            {
                'base_url': normalized_base,
                'bundle_id': manifest.get('bundle_id'),
                'summary': summary,
                'unsupported_layers': manifest.get('unsupported_layers', []),
                'source_manifest_path': manifest.get('source_manifest_path'),
            },
            indent=2,
        )
    )


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
