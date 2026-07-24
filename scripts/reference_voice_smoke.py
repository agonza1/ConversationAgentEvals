#!/usr/bin/env python3
"""Opt-in proof for the real built-in tester-to-agent reference pipeline."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


API = os.getenv('API_BASE_URL', 'http://127.0.0.1:8025').rstrip('/')
USER = f'reference-smoke-{int(time.time())}'


def request(path: str, *, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f'{API}{path}',
        data=data,
        headers={'content-type': 'application/json'} if data else {},
        method='POST' if data else 'GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'{path} failed ({exc.code}): {exc.read().decode()}') from exc


queued = request('/api/execution/runs', payload={
    'suite_id': 'call-center-voice-ai',
    'scenario_ids': ['cancellation-rescue'],
    'agent_id': 'generalist-voice-agent',
    'user_id': USER,
    'project_id': 'reference-voice-smoke',
    'iterations': 1,
    'model_name': os.getenv('REFERENCE_LLM_MODEL', 'gpt-5.4-mini'),
})
run_id = queued['execution_run_id']
deadline = time.time() + 300
while time.time() < deadline:
    run = request(f'/api/execution/runs/{run_id}?user_id={USER}')
    if run['status'] in {'completed', 'needs_review', 'failed'}:
        break
    time.sleep(1)
else:
    raise SystemExit('Reference voice smoke timed out.')

if run['status'] == 'failed':
    raise SystemExit(f'Reference voice smoke failed: {run.get("error") or run["conversations"][0].get("error")}')
conversation = run['conversations'][0]
audio_session = conversation.get('audio_session') or {}
provenance = audio_session.get('runtime_provenance') or {}
assert provenance.get('evidence_source') == 'current_run', provenance
assert provenance.get('fixture_backed_scoring') is False, provenance
assert audio_session.get('architecture') == 'two_independent_pipecat_graphs_in_process_duplex_frames', audio_session
duplex = audio_session.get('duplex') or {}
assert duplex.get('transport') == 'in_process_pipecat_frame_bus', duplex
assert duplex.get('frame_count', 0) >= 2, duplex
assert {frame.get('direction') for frame in duplex.get('frames') or []} == {
    'tester_to_target',
    'target_to_tester',
}, duplex
graphs = audio_session.get('graphs') or {}
for participant in ('tester', 'target'):
    graph = graphs.get(participant) or {}
    assert [processor.get('name') for processor in graph.get('processors') or []] == [
        'rtc-asr',
        'llm',
        'kokoro',
    ], graph
    assert graph.get('llm_mode') == 'real', graph
assert len(conversation.get('turns') or []) >= 2, conversation
assert conversation.get('vcon_export_summary', {}).get('recording_attached') is True, conversation
print(json.dumps({
    'execution_run_id': run_id,
    'status': run['status'],
    'turns': len(conversation['turns']),
    'score': conversation.get('score'),
    'recording': conversation.get('recording', {}).get('recording_url'),
    'duplex': duplex,
    'graphs': graphs,
    'provenance': provenance,
}, indent=2))
