"""Waylo's public API adapter. No dashboard/session credentials enter evidence.

Contract inspected at application a3848b558188f77523d99324c811ac1cb0a709b2.
Calls are captured, never automatically submitted for evaluation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.services.target_secrets import resolve_http_target_secret
from app.services.vcon_evidence import MAX_EVENTS, MAX_PROFILE_BYTES, attach_evidence, evidence_body, redact
from app.services.execution_vcon import build_ietf_execution_vcon


class WayloError(ValueError):
    """Safe boundary error: deliberately excludes response bodies and URLs."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Waylo API base must be an absolute HTTP(S) URL.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Waylo API base cannot contain credentials, query parameters or fragments.')
    if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}:
        raise ValueError('Remote Waylo APIs require HTTPS.')
    return value.rstrip('/')


def clean_native(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    """Additional provider-safe redaction, including strings and signed URLs.

Native payloads are untrusted and can contain arbitrary tool auth conventions.
Opaque secret values are removed even when placed under an innocuous key.
"""
    if isinstance(value, dict):
        return {k: clean_native(v, secret_values) for k, v in redact(value).items()
                if str(k).lower() not in {'headers', 'participantidentity', 'roomname', 'turn',
                                          'livekiturl', 'endpoint', 'endpointurl', 'url', 'uri'}}
    if isinstance(value, list):
        return [clean_native(v, secret_values) for v in value]
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, '[redacted]')
        if value.lstrip().startswith(('{', '[')):
            try:
                return json.dumps(clean_native(json.loads(value), secret_values), sort_keys=True)
            except ValueError:
                pass
        # Never export bearer strings or potentially authenticated links in opaque fields.
        if 'bearer ' in value.lower() or '://' in value:
            return '[redacted credential or URL]'
    return value


class WayloClient:
    def __init__(self, target: dict, *, client: httpx.AsyncClient | None = None, credential_lease=None):
        self.target = target
        connection = target['connection']
        self.base = api_url(connection['endpoint_url'])
        self.workspace = str(uuid.UUID(connection['workspace_id']))
        self.agent = str(uuid.UUID(connection['waylo_agent_id']))
        self.credential_lease = credential_lease
        if connection.get('auth_type') == 'waylo_browser_session':
            if credential_lease is None:
                raise WayloError('Connect Waylo in this browser before using this temporary target.')
            from app.services.waylo_connection import authorize_lease_target
            authorize_lease_target(credential_lease, target)
            credential_lease.check()
            self._secret = None  # Never cache an access token outside its revocable memory grant.
        else:
            if credential_lease is not None:
                raise WayloError('Temporary credentials cannot replace a server credential reference.')
            self._secret = resolve_http_target_secret(connection['secret_ref'])
        self.client = client or httpx.AsyncClient(timeout=connection.get('timeout_ms', 15000) / 1000,
                                                 follow_redirects=False, trust_env=credential_lease is None)
        self._owns_client = client is None

    @property
    def redaction_values(self) -> tuple[str, ...]:
        return (self.credential_lease.token(),) if self.credential_lease else (self._secret,)

    async def close(self):
        if self._owns_client:
            await self.client.aclose()

    async def request(self, method: str, path: str, **kwargs):
        extra_headers = kwargs.pop('headers', {})
        # The ONLY retried write has a provider-supported idempotency key.
        for attempt in range(3):
            # Resolve again for retries; disconnect/expiry must not reuse a cached token.
            secret = self.credential_lease.token() if self.credential_lease else self._secret
            headers = {**extra_headers, 'Authorization': f'Bearer {secret}', 'Accept': 'application/json'}
            if self.credential_lease:
                self.client.cookies.clear()
            try:
                response = await self.client.request(method, f'{self.base}/{path}', headers=headers, **kwargs)
            except httpx.TransportError:
                if attempt < 2 and (method == 'GET' or 'Idempotency-Key' in headers):
                    await asyncio.sleep(.2 * (attempt + 1))
                    continue
                raise WayloError('Waylo API transport failed; no response body or credential was retained.') from None
            finally:
                if self.credential_lease:
                    self.client.cookies.clear()
            if self.credential_lease:
                self.credential_lease.check()
                if response.status_code == 401:
                    self.credential_lease.revoke()
                    raise WayloError('Waylo connection expired or was revoked. Connect again.', 401)
            if response.status_code in {429, 502, 503, 504} and attempt < 2 and (method == 'GET' or 'Idempotency-Key' in headers):
                await asyncio.sleep(.2 * (attempt + 1))
                continue
            if not 200 <= response.status_code < 300:
                raise WayloError(f'Waylo API returned HTTP {response.status_code}.', response.status_code)
            if len(response.content) > MAX_PROFILE_BYTES:
                raise WayloError('Waylo response exceeds the capture limit; capture is incomplete.')
            try:
                return response.json()
            except ValueError:
                raise WayloError('Waylo API returned invalid JSON.') from None

    async def bootstrap(self, correlation_id: str):
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'cae-waylo/{self.target["id"]}/{correlation_id}'))
        result = await self.request('POST', 'sessions/web', headers={'Idempotency-Key': key}, json={
            'workspaceId': self.workspace, 'agentId': self.agent,
            'metadata': {'cae': {'origin': 'cae_ai_tester', 'target_id': self.target['id'],
                                 'correlation_id': correlation_id}},
        })
        if not isinstance(result, dict) or result.get('workspaceId') != self.workspace:
            raise WayloError('Waylo bootstrap returned an unexpected workspace.')
        uuid.UUID(str(result.get('sessionId')))
        for field in ('livekitUrl', 'participantToken', 'roomName'):
            if not isinstance(result.get(field), str) or not result[field]:
                raise WayloError('Waylo bootstrap omitted required LiveKit connection data.')
        parsed = urlsplit(result['livekitUrl'])
        if parsed.scheme not in {'ws', 'wss'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise WayloError('Waylo bootstrap returned an invalid LiveKit URL.')
        if parsed.scheme == 'ws' and parsed.hostname not in {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}:
            raise WayloError('Remote LiveKit transport requires WSS.')
        return result  # Memory-only. NEVER serialize this response.

    async def capture(self, session_id: str, *, origin: str = 'imported_human_call'):
        session_id = str(uuid.UUID(session_id))
        session = await self.request('GET', f'sessions/{session_id}')
        if not isinstance(session, dict) or session.get('id') != session_id or session.get('workspaceId') != self.workspace or session.get('agentId') != self.agent:
            raise WayloError('Session does not belong to this configured Waylo target/workspace.')
        transcripts, cursor, highest, seen = [], None, 0, set()
        while True:
            page = await self.request('GET', f'sessions/{session_id}/transcripts',
                                      params={'limit': 500, **({'cursor': cursor} if cursor else {})})
            if not isinstance(page, list) or len(page) > 500:
                raise WayloError('Invalid Waylo transcript page; complete capture cannot be established.')
            for line in page:
                if not isinstance(line, dict) or type(line.get('seq')) is not int or line['seq'] != highest + 1:
                    raise WayloError('Transcript sequence is incomplete or repeated.')
                identity = str(uuid.UUID(str(line.get('id'))))
                if identity in seen:
                    raise WayloError('Repeated transcript cursor; capture aborted.')
                seen.add(identity)
                highest, cursor = line['seq'], identity
                transcripts.append(line)
                if len(transcripts) > MAX_EVENTS:
                    raise WayloError('Transcript exceeds capture limit; refusing a truncated export.')
            if len(page) < 500:
                break
        optional, unavailable = {}, {}
        version = session.get('agentVersion')
        paths = {'events': f'sessions/{session_id}/events', 'items': f'sessions/{session_id}/items'}
        if type(version) is int and version > 0:
            paths['agent_version'] = f'agents/{self.agent}/versions/{version}'
        for name, path in paths.items():
            try:
                value = await self.request('GET', path)
                if name in {'events', 'items'} and not isinstance(value, list):
                    raise WayloError(f'Invalid Waylo {name} response.')
                if name == 'events' and (len(value) > MAX_EVENTS or len(value) != session.get('eventCount')):
                    raise WayloError('Native event count changed or exceeds capture limit; refresh required.')
                optional[name] = value
            except WayloError as exc:
                unavailable[name] = str(exc)
        # Discard detail's preview. Only the paginated endpoint is authoritative.
        session = {k: v for k, v in session.items() if k != 'transcript'}
        captured = {'target_id': self.target['id'], 'origin': origin, 'session': session,
                    'transcripts': transcripts, **optional, 'unavailable': unavailable}
        return clean_native(captured, self.redaction_values)


def project_capture(capture: dict, *, voice_events: list[dict] | None = None,
                    before_items: list[dict] | None = None, scenario_id: str = 'waylo-capture',
                    suite_id: str = 'waylo', media_dialogs: list[dict] | None = None,
                    test_expectations: dict | None = None, after_items: list[dict] | None = None) -> dict:
    """Map only typed observations. Free-form events remain ancillary evidence."""
    capture = clean_native(deepcopy(capture))
    session = capture['session']
    target_id, session_id = capture['target_id'], session['id']
    identity = f'{target_id}/{session_id}'
    tools, native_voice = [], []
    def invocation(event):
        payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        value = payload.get('tool_invocation_id')
        return value if isinstance(value, str) and value else None
    # Worker parent keys reference TRANSCRIPTS, not tool requests. Invocation
    # identity lives inside payload.tool_invocation_id (agent 615d2b78).
    calls = {invocation(e): e for e in capture.get('events', [])
             if e.get('category') == 'tool' and e.get('name') == 'call' and invocation(e)}
    transcript_by_key = {t.get('idempotencyKey'): t for t in capture['transcripts']}
    for e in capture.get('events', []):
        payload = e.get('payload') if isinstance(e.get('payload'), dict) else {}
        common = {'event_id': f'{identity}/event/{e["id"]}', 'source': 'waylo.native_event',
                  'timestamp': e.get('occurredAt'), 'clock_origin': 'waylo_worker_utc',
                  'session_id': session_id, 'target_id': target_id, 'native_sequence': e.get('seq')}
        turn = transcript_by_key.get(e.get('parentIdempotencyKey'))
        if turn:
            common.update(turn_id=f'{identity}/transcript/{turn["id"]}', turn_index=turn['seq'],
                          transcript_id=turn['id'])
        if e.get('category') == 'tool' and e.get('name') in {'call', 'result'}:
            call_key = invocation(e) or (e.get('idempotencyKey') if e['name'] == 'call' else None)
            parent = calls.get(call_key, {})
            name = e.get('subject') or parent.get('subject')
            if not name or not call_key:
                continue  # No invented invocation identity for orphan results.
            parent_payload = parent.get('payload') if isinstance(parent.get('payload'), dict) else {}
            arguments = payload.get('args', parent_payload.get('args'))
            code = e.get('code')
            # No code/severity is not proof of tool success. The native API does
            # not constrain payload schemas. Require an explicit result status.
            status = 'requested' if e['name'] == 'call' else (
                'error' if e.get('severity') == 'error' or code in {
                    'INVALID_ARGUMENTS', 'ARGUMENTS_TOO_LARGE', 'API_UNAVAILABLE',
                    'REQUEST_REJECTED', 'RESPONSE_TOO_LARGE', 'MALFORMED_RESPONSE'}
                else 'unknown' if code in {'DELIVERY_UNCONFIRMED', 'UPSTREAM_ACCEPTED'}
                else 'error' if payload.get('status') == 'error'
                else 'cancelled' if payload.get('status') == 'cancelled'
                else 'success' if payload.get('status') == 'success' and 'result' in payload else 'unknown')
            tool = {**common, 'sequence': len(tools) + 1, 'event_type': f'tool_call.{"requested" if e["name"] == "call" else "result"}',
                    'call_id': f'{identity}/tool/{call_key}', 'name': name,
                    'arguments': arguments if isinstance(arguments, dict) else {},
                    'result': payload.get('result'), 'status': status, 'code': code,
                    'arguments_captured': isinstance(arguments, dict) and '[omitted:' not in json.dumps(arguments),
                    'result_captured': 'result' in payload and '[truncated]' not in json.dumps(payload.get('result'))}
            duration = payload.get('duration_ms')
            if type(duration) in {float, int} and 0 <= duration < 1_000_000:
                tool.update(duration_ms=duration, duration_unit='ms', duration_clock='waylo_worker_wall_clock')
            for field in ('turn_id', 'retry_of', 'policy_decision'):
                if isinstance(payload.get(field), str):
                    tool[field] = payload[field]
            tools.append(tool)
        elif e.get('category') == 'turn' and e.get('name') == 'metrics':
            native_voice.append({**common, 'event_type': 'turn.metrics', 'attributes': payload,
                                 'scoreable': False, 'reason': 'Native metric units/clock semantics are not contracted.'})
            if e.get('subject') == 'agent' and type(payload.get('interrupted')) is bool:
                native_voice.append({**common, 'event_id': common['event_id'] + '/interruption',
                    'event_type': 'playback.interruption_observed', 'interrupted': payload['interrupted'],
                    'source': 'waylo.agent.turn_metrics', 'scope': 'Reported agent speech interruption; cause not independently proven.'})
            latency = payload.get('e2e_latency')
            if type(latency) in {int, float} and 0 <= latency < 1000:
                native_voice.append({**common, 'event_id': common['event_id'] + '/latency',
                    'event_type': 'latency.observation', 'value_ms': round(latency * 1000, 3), 'unit': 'ms',
                    'source': 'waylo.agent.turn_metrics', 'definition': 'Worker-reported LiveKit turn e2e_latency, not tester-observed network latency.'})
    voice = [*native_voice, *(voice_events or [])]
    from app.services.waylo_assessment import assess_speech, compare_notes
    expectations = test_expectations or {}
    diagnostics = {'speech_boundary': assess_speech(capture, voice, expectations.get('expected_entities_by_turn')),
                   'business_outcome': compare_notes(capture.get('items'), expectations.get('expected_requests'))}
    snapshots = []
    if before_items is not None:
        snapshots.append({'snapshot_id': f'{identity}/before', 'phase': 'before',
                          'source': 'waylo.sessions.items', 'state': {'request_list': before_items}})
    for index, observation in enumerate(after_items or []):
        snapshots.append({'snapshot_id': f'{identity}/after/{index}', 'phase': 'after',
                          'source': 'waylo.sessions.items', 'state': {'request_list': observation['items']},
                          'turn_id': observation['turn_id'], 'observed_at': observation['observed_at'],
                          'clock_origin': 'cae_client_utc_readback'})
    if isinstance(capture.get('items'), list):
        phase = 'final' if session.get('endedAt') else 'after'
        snapshots.append({'snapshot_id': f'{identity}/{phase}', 'phase': phase,
                          'source': 'waylo.sessions.items', 'state': {'request_list': capture['items']},
                          'canonical_product_association': 'not_recorded_by_waylo'})
    config = capture.get('agent_version', {}).get('config', {})
    config_status = 'needs_review'
    config_reason = 'Internal effective session snapshot is not public; versioned persona alone does not prove preset rules.'
    if config.get('presetId') == 'item_capture_assistant':
        config_reason = 'Item Capture preset forbids catalog lookup and overrides persona; Mike catalog contract conflicts.'
    observed = {
        'conversation_experience': (bool(media_dialogs), ['interruptions', 'silence', 'continuity', 'remote_playback_ack']),
        'speech_boundary': (any(e.get('event_type') == 'tester.audio.sent' for e in voice), ['peer_delivery_ack', 'target_asr_alignment']),
        'agent_execution': (bool(tools), ['complete_tool_payloads', 'policy_decisions', 'turn_associations']),
        'business_outcome': (bool(snapshots), ['expected_state_comparison', 'spoken_confirmation_alignment', 'catalog_resolution']),
    }
    coverage = {area: {'status': 'partial' if present else 'unknown', 'evaluation_status': 'needs_review',
                       'missing': missing, 'scoreable': False} for area, (present, missing) in observed.items()}
    turns = [{'speaker': 'Caller' if t.get('role') == 'user' else 'Agent', 'text': t.get('text', ''),
              'turn_index': t['seq']} for t in capture['transcripts'] if t.get('role') in {'user', 'agent'}]
    vcon = build_ietf_execution_vcon(conversation_id=session_id, execution_run_id=f'waylo-{session_id}',
        suite_id=suite_id, scenario_id=scenario_id, scenario_title='Waylo session capture', mode='waylo_livekit',
        turns=turns, recording=None, created_at=session['createdAt'], updated_at=session.get('endedAt') or session['createdAt'])
    vcon['uuid'] = str(uuid.uuid5(uuid.NAMESPACE_URL, f'cae-waylo/{identity}'))
    vcon['parties'][0]['type'] = 'human' if capture['origin'] == 'imported_human_call' else 'bot'
    vcon['analysis'][0]['body']['capture_method'] = 'waylo_final_transcript_not_tts_reference'
    vcon['dialog'].extend(media_dialogs or [])
    context = {'target_id': target_id, 'session_id': session_id, 'source_format': 'waylo-session-v1',
               'source_call_kind': capture['origin'], 'suite_id': suite_id, 'scenario_id': scenario_id,
               'evaluation_submission': 'manual', 'coverage': coverage,
               'diagnostics': diagnostics, 'test_expectations': expectations,
               'effective_configuration': {'status': config_status, 'reason': config_reason,
                                            'agent_version': session.get('agentVersion')}}
    body = evidence_body(state_snapshots=snapshots, voice_events=voice, context=context)
    body['tool_events'] = tools
    vcon = attach_evidence(vcon, body)
    exporter_party = next(i for i, party in enumerate(vcon['parties']) if party.get('name') == 'ConVoice QA')
    vcon['attachments'].append({'type': 'ancillary', 'purpose': 'Waylo native source evidence', 'party': exporter_party,
                                'start': vcon['updated_at'],
                                'mediatype': 'application/json', 'encoding': 'json', 'body': capture})
    # Revision describes native + mapped evidence, never wall-clock export time.
    revision = hashlib.sha256(json.dumps(vcon, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    if len(json.dumps(vcon).encode()) > MAX_PROFILE_BYTES:
        raise WayloError('Waylo evidence exceeds export limit; refusing a truncated export.')
    from app.services.execution_vcon import validate_ietf_vcon
    validation = validate_ietf_vcon(vcon)
    if not validation['valid']:
        raise WayloError('Waylo vCon failed portable validation: ' + ', '.join(validation['errors']))
    return {'vcon': vcon, 'coverage': coverage, 'revision': revision,
            'configuration_check': context['effective_configuration'], 'evaluation_submitted': False}
