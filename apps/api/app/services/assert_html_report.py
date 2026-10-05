"""Offline CAE presentation of a persisted ASSERT review; no upstream viewer assets."""
from __future__ import annotations

import html
import json
import math
import re
from typing import Any

from app.services.execution_run_store import deterministic_evaluation_snapshot
from app.services.upstream_assert_judge import assert_judge_input_fingerprint
from app.services.vcon_evidence import redact


TERMINAL = {'completed', 'needs_review', 'failed', 'cancelled', 'canceled'}


def validate_saved_review(run: dict[str, Any], conversation: dict[str, Any], review: dict[str, Any],
                          scenario_contract: dict[str, Any] | None) -> dict[str, Any]:
    """Reject incomplete or changed evidence before generating a portable report."""
    if run.get('status') not in TERMINAL or conversation.get('status') not in TERMINAL:
        raise ValueError('The run and conversation must be terminal before export.')
    result = review.get('judge_result')
    provenance = result.get('provenance') if isinstance(result, dict) else None
    if (review.get('status') not in {'pending_confirmation', 'applied', 'superseded'}
            or not isinstance(provenance, dict) or provenance.get('engine') != 'assert'
            or provenance.get('judge_status') != 'ok'):
        raise ValueError('A completed saved ASSERT review is required for export.')
    for field in ('score_sha256',):
        digest = provenance.get(field)
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('Saved ASSERT score provenance is malformed.')
    dimensions = provenance.get('dimensions')
    nodes = provenance.get('node_judgments')
    if (not isinstance(dimensions, dict) or not dimensions
            or any(not isinstance(key, str) or not isinstance(value, (str, bool, int, float, type(None)))
                   or (isinstance(value, float) and not math.isfinite(value))
                   for key, value in dimensions.items())
            or not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes)
            or any(not isinstance(provenance.get(key, {}), dict) for key in (
                'dimension_justifications', 'dimension_scales', 'dimension_applicability'))):
        raise ValueError('Saved ASSERT dimension or behavior evidence is malformed.')
    snapshot = deterministic_evaluation_snapshot(conversation)
    if snapshot.get('verdict') not in {'pass', 'needs_review', 'fail', 'failed'}:
        raise ValueError('The conversation has no saved deterministic verdict.')
    if review.get('deterministic_snapshot') != snapshot:
        raise ValueError('The saved ASSERT review is stale: conversation evidence changed. Run a new review.')
    model = review.get('model')
    fingerprint = provenance.get('input_fingerprint')
    if not isinstance(model, str) or not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{16}', fingerprint):
        raise ValueError('Saved ASSERT input provenance is unavailable or malformed.')
    # Older persisted reviews omit judge_n. ASSERT admits 1..3; compare the recorded
    # fingerprint against those exact inputs, without guessing scores or judging.
    if not any(assert_judge_input_fingerprint(run=run, conversation=conversation,
                scenario_contract=scenario_contract, model=model, judge_n=n) == fingerprint
               for n in range(1, 4)):
        raise ValueError('The saved ASSERT review is stale: judging inputs changed. Run a new review.')
    return provenance


def report_filename(run_id: str, conversation_id: str, review_id: str) -> str:
    def safe(value: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_-]', '-', value)[:60] or 'unknown'
    return f'assert-{safe(run_id)}-{safe(conversation_id)}-{safe(review_id)}.html'


# Never link or copy local artifact paths; scrub known credentials from business
# evidence using CAE's existing redactor, with text protection for path/token values.
def _clean(value: Any) -> Any:
    value = redact(value)
    if isinstance(value, dict):
        return {str(_clean(str(key))): _clean(item) for key, item in value.items()
                if not re.search(r'(?:artifact|recording|audio|snapshot|inference).*path|^path$|^artifacts$', str(key), re.I)}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r'(?:file://|local-artifact://)[^\s"<>]+|(?:/Users/|/home/|/private/|/tmp/|/var/|/workspace/|/app/|/opt/|/root/|/mnt/|/Volumes/|/etc/|/srv/)[^\s"<>]+|[A-Za-z]:\\[^\s"<>]+', '[internal path omitted]', value)
        value = re.sub(r'(?i)\b(https?://)[^\s/@]+(?::[^\s/@]*)?@', r'\1[credential omitted]@', value)
        value = re.sub(r'''(?i)(["']?)(api[_-]?key|access[_-]?token|authorization|password|secret)\1\s*[:=]\s*(["'])(.*?)\3''', r'\2=[credential omitted]', value)
        value = re.sub(r'(?i)\bBearer\s+[^\s"<>]+', 'Bearer [credential omitted]', value)
        value = re.sub(r'(?i)\b(api[_-]?key|access[_-]?token|authorization|password|secret)\s*[:=]\s*[^\s,;"<>\[]+', r'\1=[credential omitted]', value)
    return value


def _text(value: Any) -> str:
    if value is None or value == '':
        return 'Unavailable'
    return html.escape(str(_clean(value)), quote=True)


def _json(value: Any) -> str:
    if value is None:
        return 'Unavailable'
    return html.escape(json.dumps(_clean(value), ensure_ascii=False, indent=2, default=str), quote=True)


def _metadata(values: dict[str, Any]) -> str:
    return '<dl>' + ''.join(f'<dt>{_text(key)}</dt><dd>{_text(value)}</dd>' for key, value in values.items()) + '</dl>'


def render_assert_html_report(run: dict[str, Any], conversation: dict[str, Any], review: dict[str, Any],
                              provenance: dict[str, Any]) -> str:
    result = review['judge_result']
    dimensions = provenance['dimensions']
    rows = ''
    for name, value in dimensions.items():
        scale = provenance.get('dimension_scales', {}).get(name)
        scale_html = (f'<details><summary>Recorded scale</summary><pre>{_json(scale)}</pre></details>'
                      if scale is not None else '<p class="muted">Scale: Unavailable</p>')
        rows += (f'<article class="dimension"><h3>{_text(name)}</h3>'
                 f'<p><b>Recorded value:</b> <span class="pill">{_text(value)}</span> · '
                 f'<b>Applicable:</b> {_text(provenance.get("dimension_applicability", {}).get(name))}</p>'
                 f'<p>{_text(provenance.get("dimension_justifications", {}).get(name))}</p>{scale_html}</article>')
    nodes = ''.join(f'<details><summary>Behavior {_text(node.get("node_id") or node.get("behavior") or index)}</summary>'
                    f'<pre>{_json(node)}</pre></details>' for index, node in enumerate(provenance['node_judgments'], 1))
    turns = conversation.get('turns') or []
    messages = ''.join(f'<article class="message"><b>{_text(turn.get("speaker"))}</b>'
                      f'<p>{_text(turn.get("text"))}</p></article>' for turn in turns if isinstance(turn, dict))
    if not messages:
        messages = f'<pre>{_text(conversation.get("transcript"))}</pre>'
    tools = ''.join(f'<details><summary>Tool evidence {index}: {_text(action.get("tool_name") or action.get("action") or action.get("name"))}</summary>'
                    f'<pre>{_json(action)}</pre></details>' for index, action in enumerate(conversation.get('action_trace') or [], 1)
                    if isinstance(action, dict))
    citations = ''.join(f'<li>{_text(citation)} <small>— unresolved citation; no evidence anchor recorded</small></li>'
                       for citation in review.get('evidence_citations') or [])
    snapshot = review['deterministic_snapshot']
    adjudication = conversation.get('evaluation_adjudication') or {}
    applied = (adjudication.get('judge_result') or {}).get('proposed_evaluation') or {}
    metadata = _metadata({
        'Execution run': run.get('execution_run_id'), 'Conversation': conversation.get('conversation_id'),
        'Scenario': conversation.get('scenario_title') or conversation.get('scenario_id'),
        'Target': run.get('agent_name') or run.get('agent_id'), 'Review ID': review.get('review_id'),
        'Review status': review.get('status'), 'Recorded at': review.get('created_at'),
        'ASSERT version': provenance.get('assert_version'), 'Model': review.get('model'),
        'Evidence level': provenance.get('evidence_level'), 'Input fingerprint': provenance.get('input_fingerprint'),
        'Score SHA-256': provenance.get('score_sha256'), 'Output SHA-256': review.get('output_sha256'),
        'Evidence source': (run.get('provenance') or {}).get('honesty_label'),
    })
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>ASSERT report · {_text(conversation.get('scenario_title') or conversation.get('conversation_id'))}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f9;color:#202936;font:16px/1.55 system-ui,sans-serif}}main{{max-width:1440px;margin:auto;padding:32px}}h1,h2,h3{{line-height:1.2}}header,.card{{background:white;border:1px solid #dce2e9;border-radius:12px;padding:24px;margin-bottom:20px}}.layout{{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:20px}}.muted,small{{color:#566275}}dl{{display:grid;grid-template-columns:160px minmax(0,1fr);gap:6px 16px}}dt{{font-weight:600}}dd{{margin:0;overflow-wrap:anywhere}}.dimension{{border-bottom:1px solid #dce2e9;padding:12px 0}}.dimension h3{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f4f6f9;padding:12px;border-radius:6px}}p{{white-space:pre-wrap;overflow-wrap:anywhere}}details{{border:1px solid #dce2e9;border-radius:6px;padding:12px;margin:12px 0}}summary{{cursor:pointer;font-weight:600;overflow-wrap:anywhere}}.message{{border-left:3px solid #657cea;padding:4px 16px;margin:16px 0}}.message p{{margin:4px 0}}.pill{{display:inline-block;padding:3px 10px;background:#edf1ff;border-radius:20px}}@media(max-width:950px){{.layout{{display:block}}main{{padding:16px}}dl{{grid-template-columns:1fr}}}}@media print{{body{{background:white}}main{{padding:0}}.layout{{display:block}}details{{break-inside:avoid}}}}
</style></head><body><main>
<header><p class="muted">Conversation Agent Evals · Portable saved review</p><h1>ASSERT semantic report</h1>
<p>Rendered by CAE from a saved ASSERT assessment. This report was not generated by the upstream ASSERT viewer. Exporting does not run a judge or change a verdict.</p>{metadata}</header>
<div class="layout"><section aria-label="Judgment"><div class="card"><h2>Evaluation and adjudication</h2>
<p><b>CAE deterministic verdict:</b> <span class="pill">{_text(snapshot.get('verdict'))}</span> · Recorded score: {_text(snapshot.get('score'))}</p>
<p><b>ASSERT agrees:</b> {_text(result.get('agrees'))}</p><p><b>Semantic rationale:</b> {_text(result.get('rationale'))}</p>
<p><b>Selected review proposal:</b></p><pre>{_json(result.get('proposed_evaluation'))}</pre>
<p><b>Currently applied adjudication:</b> {_text(applied.get('verdict'))} · Review: {_text(adjudication.get('review_id'))}</p>
<p class="muted">Semantic proposals and applied adjudication are separate from the deterministic evidence.</p></div>
<div class="card"><h2>Recorded dimensions</h2>{rows}
<h3>Behavior judgments</h3>{nodes or '<p>Unavailable</p>'}<h3>Recorded citations</h3><ul>{citations or '<li>Unavailable</li>'}</ul></div></section>
<section aria-label="Conversation"><div class="card"><h2>Conversation</h2>{messages}</div><div class="card"><h2>Tool and state evidence</h2>{tools or '<p>No tool evidence recorded.</p>'}
<details><summary>Recorded final state</summary><pre>{_json(conversation.get('final_state'))}</pre></details></div>
<div class="card"><h2>Evidence notes</h2><p>Audio is not embedded. Recording evidence, when available, remains in CAE.</p><p>Known credential fields and internal artifact paths are omitted. Missing values are unavailable; tool success and citation anchors are never inferred.</p></div></section></div></main></body></html>'''
