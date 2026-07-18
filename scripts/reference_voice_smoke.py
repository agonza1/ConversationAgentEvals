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
provenance = (conversation.get('audio_session') or {}).get('runtime_provenance') or {}
assert provenance.get('evidence_source') == 'current_run', provenance
assert provenance.get('fixture_backed_scoring') is False, provenance
assert len(conversation.get('turns') or []) >= 2, conversation
assert conversation.get('vcon_export_summary', {}).get('recording_attached') is True, conversation
print(json.dumps({
    'execution_run_id': run_id,
    'status': run['status'],
    'turns': len(conversation['turns']),
    'score': conversation.get('score'),
    'recording': conversation.get('recording', {}).get('recording_url'),
    'provenance': provenance,
}, indent=2))
