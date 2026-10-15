from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from app.schemas.agents import AgentCreateRequest
from app.schemas.execution import ExecutionRunCreateRequest
from app.main import app  # Bootstrap built-in catalog extensions as production does.
from app.services import agent_store, execution_runner
from app.services.execution_vcon import validate_ietf_vcon
from app.services.vcon_evidence import PURPOSE, decode_evidence, intake_vcon
from app.services.waylo_target import WayloClient, WayloError, api_url, clean_native, project_capture
from app.services.waylo_livekit import LiveKitVoicePeer, pcm_wav, read_wav, run_waylo_call
from app.services.waylo_mike_cases import WAYLO_MIKE_SUITE

W, A, S = [str(uuid.uuid5(uuid.NAMESPACE_DNS, key)) for key in ('workspace', 'agent', 'session')]
TARGET = {'id': 'mike', 'name': 'Mike', 'target': 'waylo', 'channel': 'voice', 'connection': {
    'endpoint_url': 'https://waylo.test/api', 'workspace_id': W, 'waylo_agent_id': A,
    'secret_ref': 'waylo-test', 'auth_type': 'bearer_secret'}}


def session(**overrides):
    return {'id': S, 'workspaceId': W, 'agentId': A, 'agentVersion': 7, 'status': 'ended',
            'createdAt': '2026-10-09T12:00:00Z', 'endedAt': '2026-10-09T12:01:00Z',
            'eventCount': 0, 'transcript': [{'text': 'PREVIEW MUST NOT BE USED'}], **overrides}


def transcript(index, role='user', text='five bags of white potatoes'):
    return {'id': str(uuid.uuid5(uuid.NAMESPACE_DNS, f'line-{index}')), 'seq': index,
            'idempotencyKey': str(uuid.uuid5(uuid.NAMESPACE_DNS, f'key-{index}')), 'role': role,
            'text': text, 'startedAt': '2026-10-09T12:00:01Z'}


def native_event(index, name, **overrides):
    return {'id': f'event-{index}', 'seq': index, 'idempotencyKey': f'key-{index}',
            'parentIdempotencyKey': None, 'category': 'tool', 'name': name,
            'subject': 'search_documents', 'severity': 'info', 'code': None,
            'occurredAt': '2026-10-09T12:00:02Z', 'createdAt': '2026-10-09T12:00:03Z',
            'payload': {'tool_invocation_id': 'invocation-1'}, **overrides}


def captured(**overrides):
    return {'target_id': 'mike', 'origin': 'imported_human_call', 'session': session(),
            'transcripts': [transcript(1), transcript(2, 'agent', 'I have noted five bags of White Potatoes.')],
            'events': [], 'items': [], 'unavailable': {}, **overrides}


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv('CAE_HTTP_TARGET_SECRET_WAYLO_TEST', 'top-secret-api-key')


def test_target_configuration_is_reusable_not_mike_specific():
    payload = AgentCreateRequest.model_validate(TARGET)
    assert payload.target == 'waylo'
    another = deepcopy(TARGET)
    another['id'] = 'other-agent'
    another['connection']['waylo_agent_id'] = str(uuid.uuid4())
    assert AgentCreateRequest.model_validate(another).target == 'waylo'


@pytest.mark.parametrize('field,value', [('workspace_id', 'not-uuid'), ('waylo_agent_id', None),
    ('auth_type', 'none'), ('secret_ref', None), ('endpoint_url', 'https://x.test/?token=secret'),
    ('endpoint_url', 'https://user:secret@x.test'), ('endpoint_url', 'http://remote.test')])
def test_invalid_connections_rejected(field, value):
    target = deepcopy(TARGET)
    target['connection'][field] = value
    with pytest.raises(ValueError):
        AgentCreateRequest.model_validate(target)


def test_other_target_cannot_store_waylo_configuration():
    target = deepcopy(TARGET)
    target.update(target='http_endpoint', channel='text')
    with pytest.raises(ValueError):
        AgentCreateRequest.model_validate(target)


@pytest.mark.parametrize('port', ['bad', '-1', '65536', '443:80'])
def test_invalid_url_ports_rejected_before_saving_target(port):
    endpoint = f'https://waylo.test:{port}/api'
    with pytest.raises(ValueError) as error:
        api_url(endpoint)
    assert endpoint not in str(error.value)
    assert port not in str(error.value)
    target = deepcopy(TARGET)
    target['connection']['endpoint_url'] = endpoint
    with pytest.raises(ValueError):
        AgentCreateRequest.model_validate(target)


@pytest.mark.parametrize('endpoint', [
    'https://waylo.test/api', 'https://waylo.test:443/api',
    'https://waylo.test:8443/api', 'http://localhost:8880/api',
    'http://127.0.0.1:8880/api', 'http://[::1]:8880/api',
])
def test_valid_url_ports_remain_supported(endpoint):
    target = deepcopy(TARGET)
    target['connection']['endpoint_url'] = endpoint
    assert AgentCreateRequest.model_validate(target).connection.endpoint_url == endpoint


def test_bootstrap_retry_reuses_idempotency_and_keeps_tokens_memory_only():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer top-secret-api-key'
        assert 'useDraft' not in json.loads(request.content)
        if len(requests) == 1:
            raise httpx.ReadTimeout('unknown write outcome', request=request)
        return httpx.Response(201, json={'sessionId': S, 'workspaceId': W, 'roomName': 'room',
            'livekitUrl': 'wss://rtc.test', 'participantToken': 'private-participant-token'})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WayloClient(TARGET, client=http)
            bootstrap = await client.bootstrap('same-run/same-conversation')
            assert bootstrap['participantToken'] == 'private-participant-token'
            assert requests[0].headers['Idempotency-Key'] == requests[1].headers['Idempotency-Key']
            await client.bootstrap('same-run/same-conversation')
            assert requests[1].headers['Idempotency-Key'] == requests[2].headers['Idempotency-Key']
    asyncio.run(exercise())


def test_transcript_pagination_beyond_embedded_500_and_optional_scopes():
    requests = []
    def handler(request):
        requests.append(request.url)
        path = request.url.path
        if path.endswith('/transcripts'):
            if request.url.params.get('cursor'):
                assert request.url.params['cursor'] == transcript(500)['id']
                return httpx.Response(200, json=[transcript(501, 'agent', 'final late line')])
            return httpx.Response(200, json=[transcript(i) for i in range(1, 501)])
        if path.endswith('/items'):
            return httpx.Response(200, json=[])
        if path.endswith('/events') or '/versions/' in path:
            return httpx.Response(403, json={'unsafe': 'top-secret-api-key'})
        return httpx.Response(200, json=session())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            capture = await WayloClient(TARGET, client=http).capture(S)
            assert len(capture['transcripts']) == 501
            assert 'transcript' not in capture['session']
            assert set(capture['unavailable']) == {'events', 'agent_version'}
            result = project_capture(capture)
            assert len(result['vcon']['dialog']) == 501
            assert result['evaluation_submitted'] is False
            assert result['coverage']['agent_execution']['status'] == 'unknown'
            assert 'top-secret-api-key' not in json.dumps(result)
    asyncio.run(exercise())


@pytest.mark.parametrize('source,value', [
    ('agent_version', None), ('agent_version', []), ('agent_version', {'config': None}),
    ('agent_version', {'config': []}), ('items', [None]), ('events', [None]), ('events', [{'id': None}]),
])
def test_malformed_optional_sources_are_unavailable_not_projection_crashes(source, value):
    def handler(request):
        path = request.url.path
        if path.endswith('/transcripts'):
            return httpx.Response(200, json=[transcript(1), transcript(2, 'agent')])
        current = 'events' if path.endswith('/events') else 'items' if path.endswith('/items') else 'agent_version' if '/versions/' in path else None
        if current:
            return httpx.Response(200, json=value if current == source else {'config': {}} if current == 'agent_version' else [])
        return httpx.Response(200, json=session())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            capture = await WayloClient(TARGET, client=http).capture(S)
            assert source in capture['unavailable'] and source not in capture
            result = project_capture(capture)
            assert result['evaluation_submitted'] is False
    asyncio.run(exercise())


@pytest.mark.parametrize('mode', ['repeat', 'gap', 'wrong-workspace', 'wrong-agent'])
def test_incomplete_or_wrong_target_capture_fails_closed(mode):
    def handler(request):
        if request.url.path.endswith('/transcripts'):
            page = [transcript(1), transcript(1 if mode == 'repeat' else 3)]
            return httpx.Response(200, json=page)
        return httpx.Response(200, json=session(**({'workspaceId': A} if mode == 'wrong-workspace'
            else {'agentId': W} if mode == 'wrong-agent' else {})))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(WayloError):
                await WayloClient(TARGET, client=http).capture(S)
    asyncio.run(exercise())


@pytest.mark.parametrize('field,value', [
    ('role', []), ('role', None), ('text', {}), ('text', None),
    ('idempotencyKey', []), ('idempotencyKey', {}), ('startedAt', []), ('endedAt', {}),
])
def test_malformed_transcript_projection_fields_fail_with_safe_capture_error(field, value):
    line = transcript(1)
    line[field] = value
    def handler(request):
        return httpx.Response(200, json=[line] if request.url.path.endswith('/transcripts') else session())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(WayloError, match='Invalid Waylo transcript fields'):
                await WayloClient(TARGET, client=http).capture(S)
    asyncio.run(exercise())


@pytest.mark.parametrize('field,value', [
    ('createdAt', '__missing__'), ('createdAt', None), ('createdAt', ''), ('createdAt', 'bad'),
    ('createdAt', []), ('createdAt', {}), ('createdAt', '2026-10-09T12:00:00'),
    ('endedAt', ''), ('endedAt', 'bad'), ('endedAt', []), ('endedAt', {}),
])
def test_session_timestamps_rejected_before_transcript_reads_or_projection(field, value):
    detail = session()
    if value == '__missing__':
        detail.pop(field)
    else:
        detail[field] = value
    requests = []
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=detail)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(WayloError, match='session timestamps'):
                await WayloClient(TARGET, client=http).capture(S)
    asyncio.run(exercise())
    assert requests == [f'/api/sessions/{S}']
    with pytest.raises(WayloError, match='session timestamps'):
        project_capture(captured(session=detail))


@pytest.mark.parametrize('created,ended', [
    ('2026-10-09T12:00:00Z', None),
    ('2026-10-09T08:00:00-04:00', '2026-10-09T08:01:00-04:00'),
])
def test_valid_session_clocks_and_unended_sessions_remain_supported(created, ended):
    value = captured(session=session(createdAt=created, endedAt=ended))
    assert validate_ietf_vcon(project_capture(value)['vcon'])['valid']


@pytest.mark.parametrize('field,value', [
    ('parentIdempotencyKey', []), ('parentIdempotencyKey', {}),
    ('idempotencyKey', []), ('idempotencyKey', {}), ('category', []),
    ('name', []), ('subject', {}), ('code', []), ('occurredAt', {}),
    ('createdAt', []), ('seq', []), ('seq', True),
])
def test_malformed_optional_event_fields_stay_unavailable_without_losing_transcript(field, value):
    event = native_event(1, 'result', **{field: value}) if field != 'name' else native_event(1, value)
    def handler(request):
        if request.url.path.endswith('/transcripts'):
            return httpx.Response(200, json=[transcript(1)])
        if request.url.path.endswith('/events'):
            return httpx.Response(200, json=[event])
        if request.url.path.endswith('/items'):
            return httpx.Response(200, json=[])
        if '/versions/' in request.url.path:
            return httpx.Response(200, json={'config': {}})
        return httpx.Response(200, json=session(eventCount=1))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            capture = await WayloClient(TARGET, client=http).capture(S)
            assert 'events' not in capture and 'events' in capture['unavailable']
            assert capture['transcripts'] == [transcript(1)] and capture['items'] == []
            exported = project_capture(capture)
            assert validate_ietf_vcon(exported['vcon'])['valid']
            assert decode_evidence(exported['vcon'])['tool_events'] == []
            assert exported['coverage']['agent_execution']['status'] == 'unknown'
    asyncio.run(exercise())


def test_revision_idempotence_and_late_append_same_vcon_identity():
    source = captured()
    one, two = project_capture(source), project_capture(source)
    assert one == two
    source['transcripts'].append(transcript(3, 'agent', 'late transcript'))
    late = project_capture(source)
    assert late['vcon']['uuid'] == one['vcon']['uuid']
    assert late['revision'] != one['revision']
    assert len(late['vcon']['dialog']) == 3


def test_native_credentials_and_opaque_encoded_credentials_redacted():
    value = {'participantToken': 'p', 'turn': {'username': 'u', 'credential': 'c'},
             'safe_key': 'top-secret-api-key', 'opaque': '{"Authorization":"Bearer hidden","product":"White Potatoes"}',
             'signed_link': 'https://example.test/path?X-Amz-Signature=secret', 'headers': {'custom': 'hidden'}}
    rendered = json.dumps(clean_native(value, ('top-secret-api-key',)))
    for forbidden in ('top-secret-api-key', 'participantToken', 'Authorization', 'X-Amz-Signature', 'hidden', '"credential"', 'username'):
        assert forbidden not in rendered
    assert 'White Potatoes' in rendered


@pytest.mark.parametrize('encoded', [False, True])
def test_native_secret_object_keys_dropped_recursively_without_collisions(encoded):
    secrets = ('opaque-access-value', 'opaque-api-value', '')
    native = {
        secrets[0]: 'private-entry',
        'nested': [{f'prefix-{secrets[1]}-suffix': 'private-nested-entry',
                    'product': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}],
        '[redacted]': 'legitimate-entry',
        'message': f'echo {secrets[0]}',
        'password': 'generic-password',
    }
    original = deepcopy(native)
    cleaned = clean_native(json.dumps(native) if encoded else native, secrets)
    if encoded:
        cleaned = json.loads(cleaned)
    assert cleaned == {
        'nested': [{'product': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}],
        '[redacted]': 'legitimate-entry', 'message': 'echo [redacted]',
    }
    assert native == original
    # The encoded form can also occur inside a native tool payload.
    export = project_capture(clean_native(captured(events=[native_event(
        1, 'result', payload={'result': json.dumps(native) if encoded else native})]), secrets))
    rendered = json.dumps(export)
    for forbidden in (*secrets[:2], 'private-entry', 'private-nested-entry', 'generic-password'):
        assert forbidden not in rendered
    assert 'White Potatoes' in rendered


@pytest.mark.parametrize('code', ['UPSTREAM_ACCEPTED', 'DELIVERY_UNCONFIRMED', None])
def test_tool_mapping_does_not_invent_success_and_preserves_parent_correlation(code):
    events = [native_event(1, 'call', parentIdempotencyKey=transcript(1)['idempotencyKey'],
                           payload={'args': {'query': 'white potatoes'}, 'tool_invocation_id': 'invocation-1'}),
              native_event(2, 'result', parentIdempotencyKey='key-1', code=code,
                           payload={'tool_invocation_id': 'invocation-1', 'result': {'name': 'White Potatoes', 'packSize': '10 kg'}})]
    export = project_capture(captured(events=events))
    tools = decode_evidence(export['vcon'])['tool_events']
    assert tools[0]['status'] == 'requested'
    assert tools[1]['status'] == 'unknown'
    assert tools[0]['call_id'] == tools[1]['call_id']
    assert tools[0]['transcript_id'] == transcript(1)['id']
    assert tools[1]['arguments']['query'] == 'white potatoes'
    assert tools[1]['result']['packSize'] == '10 kg'
    assert tools[1]['clock_origin'] == 'waylo_worker_utc'


def test_explicit_tool_results_error_retries_and_orphans():
    events = [native_event(1, 'call'), native_event(2, 'result', parentIdempotencyKey='key-1',
        payload={'tool_invocation_id': 'invocation-1', 'status': 'success', 'result': {'products': ['White Potatoes']}, 'turn_id': 'native-turn'}),
        native_event(3, 'call', payload={'tool_invocation_id': 'invocation-2', 'retry_of': 'key-1'}),
        native_event(4, 'result', parentIdempotencyKey='key-3', code='API_UNAVAILABLE', payload={'tool_invocation_id': 'invocation-2'}),
        native_event(5, 'result', parentIdempotencyKey=None, payload={})]
    tools = decode_evidence(project_capture(captured(events=events))['vcon'])['tool_events']
    assert len(tools) == 4
    assert tools[1]['status'] == 'success' and tools[1]['turn_id'] == 'native-turn'
    assert tools[-1]['status'] == 'error'
    assert tools[2]['retry_of'] == 'key-1'


def test_request_list_round_trip_not_order_or_canonical_product_id():
    items = [{'position': 1, 'rawText': 'five bags of White Potatoes', 'quantity': 5, 'unit': 'bags'}]
    exported = project_capture(captured(items=items), before_items=[])
    assert validate_ietf_vcon(exported['vcon'])['valid']
    normalized, _ = intake_vcon({'vcon': exported['vcon']})
    assert normalized['final_state'] == {'request_list': items}
    assert 'complete' not in normalized['final_state']
    assert 'catalog_product_id' not in json.dumps(exported)
    profile = decode_evidence(exported['vcon'])
    assert [s['phase'] for s in profile['state_snapshots']] == ['before', 'final']
    assert profile['context']['source_call_kind'] == 'imported_human_call'


def test_mike_effective_preset_conflict_stays_needs_review():
    export = project_capture(captured(agent_version={'config': {'presetId': 'item_capture_assistant'}}))
    assert export['configuration_check']['status'] == 'needs_review'
    assert 'conflicts' in export['configuration_check']['reason']
    assert all(area['evaluation_status'] == 'needs_review' for area in export['coverage'].values())


def test_mike_cases_are_transcript_only_not_fake_tool_evidence():
    assert len(WAYLO_MIKE_SUITE['scenarios']) == 10
    for case in WAYLO_MIKE_SUITE['scenarios']:
        assert case['sample_action_trace'] == [] and case['sample_final_state'] == {}
        assert case['waylo_test']['contract']['durable_canonical_product_id'] is False
    first = WAYLO_MIKE_SUITE['scenarios'][0]
    assert first['waylo_test']['expected_requests'][0]['quantity'] == 5
    assert first['waylo_test']['expected_requests'][0]['unit'] == 'bags'
    for case in WAYLO_MIKE_SUITE['scenarios'][-2:]:
        assert case['waylo_test']['condition'] == case['waylo_test']['audio_condition']['kind']


def test_waylo_execution_cannot_enable_automatic_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_store, 'AGENTS_DIR', tmp_path / 'agents')
    agent_store.reset_agents_for_tests()
    agent_store.create_agent(AgentCreateRequest.model_validate(TARGET))
    resolved = execution_runner._resolve_agent_payload(ExecutionRunCreateRequest(agent_id='mike', evaluate=True))
    assert resolved.executor_id == 'waylo_livekit' and resolved.evaluate is False
    assert resolved.model_name == 'waylo-agent-version'
    explicit = ExecutionRunCreateRequest(mode='pipecat_webrtc', executor_id='waylo_livekit', evaluate=True)
    assert explicit.evaluate is False and explicit.audio_transport == 'waylo_livekit'
    with pytest.raises(ValueError, match='saved target'):
        execution_runner._resolve_agent_payload(explicit)


def test_real_sdk_turn_configuration_and_audio_source_shape():
    from livekit import rtc
    config = rtc.RtcConfiguration(ice_transport_type=rtc.IceTransportType.TRANSPORT_RELAY,
        ice_servers=[rtc.IceServer(urls=['turn:localhost:3478'], username='ephemeral', password='private')])
    assert rtc.RoomOptions(rtc_config=config).rtc_config is config
    audio = pcm_wav(b'\x01\x00' * 480, 24000)
    rate, pcm = read_wav(audio)
    frame = rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2)
    assert frame.samples_per_channel == 480


def test_text_stream_transcripts_attribute_caller_to_transcribed_track():
    async def exercise():
        class Reader:
            info = SimpleNamespace(attributes={'lk.segment_id': 'segment-1',
                'lk.transcribed_track_id': 'caller-track', 'lk.transcription_final': 'true'}, stream_id='stream-1')
            async def read_all(self):
                return 'five bags'
        peer = SimpleNamespace(room=SimpleNamespace(local_participant=SimpleNamespace(
            track_publications={'caller-track': object()})), remote_identity='agent', events=[], segments={}, now_ms=lambda: 12)
        await LiveKitVoicePeer._read_transcription(peer, Reader(), 'agent')
        await LiveKitVoicePeer._read_transcription(peer, Reader(), 'agent')
        assert peer.segments == {('caller', 'segment-1'): 'five bags'}
        assert len(peer.events) == 1 and peer.events[0]['event_type'] == 'asr.final'
    asyncio.run(exercise())


def test_worker_tool_and_metric_projection_preserves_incomplete_payloads():
    line = transcript(1)
    events = [native_event(1, 'call', parentIdempotencyKey=line['idempotencyKey'],
        payload={'tool_invocation_id': 'tool-1', 'args': {'arguments': '[omitted: size limit]'}}),
        native_event(2, 'result', parentIdempotencyKey=line['idempotencyKey'],
            payload={'tool_invocation_id': 'tool-1', 'status': 'success', 'result': 'preview… [truncated]', 'duration_ms': 25}),
        native_event(3, 'metrics', category='turn', subject='agent',
            payload={'interrupted': True, 'e2e_latency': 1.25})]
    profile = decode_evidence(project_capture(captured(events=events))['vcon'])
    call, result = profile['tool_events']
    assert call['call_id'] == result['call_id']
    assert call['transcript_id'] == line['id'] and result['duration_ms'] == 25
    assert call['arguments_captured'] is False and result['result_captured'] is False
    assert any(e.get('interrupted') is True for e in profile['voice_events'])
    assert any(e.get('value_ms') == 1250 for e in profile['voice_events'])


def test_partial_audio_send_preserves_accepted_samples_not_intended_wav(tmp_path):
    class Source:
        sample_rate = 24000
        calls = 0
        async def capture_frame(self, frame):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError('private sdk details')
        async def wait_for_playout(self):
            pass
    peer = SimpleNamespace(source=Source(), rtc=SimpleNamespace(AudioFrame=lambda *args: args), events=[],
                           now_ms=lambda: 10, origin_utc='2026-10-09T12:00:00Z')
    async def exercise():
        with pytest.raises(WayloError, match='retained'):
            await LiveKitVoicePeer.send(peer, pcm_wav(b'\x01\x00' * 960),
                                       turn_id='caller-1', reference='five bags', artifact_dir=tmp_path)
    asyncio.run(exercise())
    assert peer.events[0]['accepted_samples'] == 480
    assert peer.events[0]['intended_samples'] == 960
    assert peer.events[0]['peer_receipt'] == 'unknown'
    assert len(read_wav((tmp_path / 'caller-1-sent.wav').read_bytes())[1]) == 960


def test_noise_is_deterministic_actual_media_not_just_a_label():
    from app.services.waylo_audio_conditions import add_noise
    audio = pcm_wav(b'\x00\x10' * 4800)
    first, meta = add_noise(audio, snr_db=20, seed=42)
    second, _ = add_noise(audio, snr_db=20, seed=42)
    third, _ = add_noise(audio, snr_db=20, seed=43)
    assert first == second and first != third and first != audio
    assert len(read_wav(first)[1]) == len(read_wav(audio)[1])
    assert meta['snr_db_requested'] == 20 and meta['scope'].startswith('synthetic_condition_only')


def test_entity_diagnostics_separate_recognition_and_local_send_clipping():
    from app.services.waylo_assessment import assess_speech, compare_notes
    expected = {'product_name': 'Red Potatoes', 'quantity': 15, 'unit': 'bags'}
    source = captured(transcripts=[transcript(1, text='fifty boxes of white potatoes')])
    refs = [{'event_type': 'tester.audio.sent', 'turn_id': 'caller-1', 'reference_text': 'fifteen bags of red potatoes',
             'intended_samples': 100, 'accepted_samples': 80}]
    diagnostics = assess_speech(source, refs, [[expected]])
    comparison = diagnostics['comparisons'][0]
    assert comparison['critical_entities'][0]['recognition'] == {'product': False, 'quantity': False, 'unit': False}
    assert comparison['local_send'] == 'clipped_or_incomplete'
    assert comparison['receiver_drop'] == 'unknown' and comparison['scoreable'] is False
    assert compare_notes([{'rawText': 'Red Potatoes', 'quantity': 15, 'unit': 'bags'}], [expected])['status'] == 'observed_agreement'
    assert compare_notes([{'rawText': 'Red Potatoes', 'quantity': 50, 'unit': 'boxes'}], [expected])['status'] == 'observed_mismatch'
    source['transcripts'].append(transcript(2))
    assert assess_speech(source, refs)['status'] == 'unknown'


def test_interrupted_list_checks_initial_quantity_and_later_correction_separately():
    from app.services.waylo_assessment import assess_speech
    from app.services.waylo_mike_cases import WAYLO_MIKE_SUITE
    case = next(case for case in WAYLO_MIKE_SUITE['scenarios'] if case['id'] == 'mike-interrupted-list')
    expected = case['waylo_test']['expected_entities_by_turn']
    assert expected[0][1]['quantity'] == 2
    assert expected[1][0]['quantity'] == 3
    assert expected[2] == []
    assert case['waylo_test']['expected_requests'][1]['quantity'] == 3
    refs = [{'event_type': 'tester.audio.sent', 'turn_id': f'caller-{index}',
             'reference_text': text, 'intended_samples': 100, 'accepted_samples': 100}
            for index, text in enumerate(case['caller_steps'], 1)]
    source = captured(transcripts=[transcript(index, text=text)
                                  for index, text in enumerate(case['caller_steps'], 1)])
    comparisons = assess_speech(source, refs, expected)['comparisons']
    for comparison in comparisons[:2]:
        assert all(entity['recognition'] == {'product': True, 'quantity': True, 'unit': True}
                   for entity in comparison['critical_entities'])
    assert comparisons[2]['critical_entities'] == []
    source['transcripts'][1] = transcript(2, text='Wait, make the carrots two boxes.')
    correction = assess_speech(source, refs, expected)['comparisons'][1]
    assert correction['critical_entities'][0]['recognition']['quantity'] is False
    assert correction['scoreable'] is False


@pytest.mark.parametrize('scripted_exchanges', [1, 2])
@pytest.mark.parametrize('capture_failure', [None, 'session', 'transcripts', 'late', 'revoked', 'revoked_during_send', 'missing_created', 'bad_tool_name'])
def test_real_call_orchestration_captures_manual_evidence_and_playable_turns(tmp_path, monkeypatch, scripted_exchanges, capture_failure):
    revoked = False
    class Client:
        _secret = 'provider-secret'
        @property
        def redaction_values(self):
            assert not revoked, 'Never retrieve an expired/revoked provider token.'
            return ('provider-secret',)
        agent = A
        calls = []
        async def bootstrap(self, correlation_id):
            self.calls.append('bootstrap')
            return {'sessionId': S, 'participantToken': 'private', 'roomName': 'room',
                    'turn': {'credential': 'turn-credential', 'username': 'turn-username'}}
        async def request(self, method, path):
            self.calls.append(path)
            return [{'rawText': 'opaque provider-secret private turn-credential turn-username', 'quantity': 1}]
        async def capture(self, session_id, origin):
            assert not revoked, 'Never read the provider after authorization ends.'
            self.calls.append('capture')
            if capture_failure in {'session', 'transcripts'} or (capture_failure == 'late' and self.calls.count('capture') > 1):
                if capture_failure == 'transcripts':
                    raise RuntimeError('unsafe upstream private provider-secret turn-credential turn-username')
                raise WayloError('Waylo API unavailable.', status=503)
            if capture_failure == 'missing_created':
                detail = session()
                detail.pop('createdAt')
                return captured(session=detail)
            if capture_failure == 'bad_tool_name':
                return captured(events=[native_event(1, ['invalid'])])
            return captured(origin=origin, opaque='private turn-credential')
        async def close(self):
            self.calls.append('close')
    class Peer:
        events = []
        segments = {}
        remote_pcm = bytearray()
        remote_chunks = []
        speech_count = 0
        origin_utc = '2026-10-09T12:00:00Z'
        error = None
        async def connect(self, bootstrap):
            assert bootstrap['participantToken'] == 'private'
        async def close(self):
            pass
        def now_ms(self):
            return 1000
        async def wait_response(self, previous, timeout=25):
            nonlocal revoked
            if capture_failure == 'revoked' and previous:
                self.remote_pcm.extend(b'\x02\x10' * 120)
                revoked = True
                self.events.append({'event_type': 'transcription.final', 'text': 'provider-secret private turn-credential'})
                await asyncio.sleep(10)
            self.speech_count += 1
            self.remote_pcm.extend(b'\x00\x10' * 480)
            self.segments[('remote', 'agent-text')] = 'I have noted five bags of White Potatoes. provider-secret private turn-credential turn-username'
        async def send(self, audio, *, turn_id, reference, artifact_dir):
            nonlocal revoked
            event = {'event_id': f'{turn_id}/sent', 'event_type': 'tester.audio.sent', 'source': 'livekit.AudioSource',
                     'turn_id': turn_id, 'reference_text': reference, 'duration_ms': 20,
                     'intended_samples': 480, 'accepted_samples': 480, 'audio_sha256': 'digest',
                     'ended_at_ms': 100, 'overlap_with_observed_target_speech': False}
            self.events.append(event)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / f'{turn_id}-sent.wav').write_bytes(audio)
            if capture_failure == 'revoked_during_send':
                revoked = True
                await asyncio.sleep(10)
            return event, audio
    if capture_failure:
        from app.services import waylo_livekit
        # Exercise the production continuous-recording branch without an SDK,
        # remote session or live monitor service.
        Peer = type('MonitoredPeer', (Peer, LiveKitVoicePeer), {'__init__': lambda self: None, 'origin': 0})
        class Monitor:
            recording = SimpleNamespace(origin=0)
            async def start(self):
                pass
            async def finish(self, *, partial):
                return pcm_wav(b'\x00\x10' * 480), {'duration_ms': 20, 'partial': partial}
            def __init__(self, **kwargs):
                pass
        monkeypatch.setattr(waylo_livekit, 'LiveAudioMonitor', Monitor)
    async def wording(turns, index):
        assert index <= scripted_exchanges, 'Never add an undeclared scripted turn.'
        return 'five bags of white potatoes'
    async def synthesize(text, index):
        return pcm_wav(b'\x00\x10' * 480)
    observer_events = []
    client = Client()
    class Lease:
        def check(self):
            from app.services.waylo_connection import WayloConnectionError
            if revoked:
                raise WayloConnectionError('Waylo authorization ended.')
    result = asyncio.run(run_waylo_call(target=TARGET, correlation_id='run/conversation',
        scenario={'id': 'case', 'caller_steps': ['five bags of white potatoes'] * scripted_exchanges},
        suite_id='waylo-mike-notes', artifact_dir=tmp_path, max_exchanges=3, timeout_seconds=30,
        next_utterance=wording, synthesize=synthesize, client=client, peer=Peer(), event_observer=observer_events.append,
        credential_lease=Lease() if capture_failure and capture_failure.startswith('revoked') else None))
    assert result['verdict'] == ('failed' if capture_failure else 'needs_review')
    assert result['score'] is None and result['evaluation_report'] == {}
    expected_speakers = (['Agent', 'Conversation'] if capture_failure == 'revoked_during_send' else
        ['Agent', 'Caller', 'Conversation'] if capture_failure == 'revoked' else
        ['Agent'] + ['Caller', 'Agent'] * scripted_exchanges + (['Conversation'] if capture_failure else []))
    assert [e['speaker'] for e in observer_events] == expected_speakers
    assert all(isinstance(e['audio'], bytes) for e in observer_events)
    assert 'provider-secret' not in repr(observer_events)
    assert 'private' not in repr(observer_events)
    assert 'turn-credential' not in repr(observer_events) and 'turn-username' not in repr(observer_events)
    profile = decode_evidence(result['ietf_vcon_export'])
    assert profile['context']['source_call_kind'] == 'cae_ai_tester'
    assert profile['context']['evaluation_submission'] == 'manual'
    assert 'private' not in json.dumps(result)
    assert 'provider-secret' not in json.dumps(result)
    assert 'turn-credential' not in json.dumps(result) and 'turn-username' not in json.dumps(result)
    assert (tmp_path / 'agent-received.wav').exists()
    assert validate_ietf_vcon(result['ietf_vcon_export'])['valid']
    assert client.calls.count('bootstrap') == 1
    assert client.calls[-1] == 'close'
    if capture_failure:
        expected_turns = (1 if capture_failure == 'revoked_during_send' else
                          2 if capture_failure == 'revoked' else 1 + scripted_exchanges * 2)
        assert len(result['turns']) == expected_turns
        assert len(result['local_audio_tracks']) == (2 if capture_failure == 'revoked_during_send' else expected_turns)
        assert result['audio_session']['session_id'] == S
        assert 'local RTC evidence retained' in result['final_state']['tester_error']
        assert profile['context']['capture_completeness'] == 'partial'
        assert profile['context']['target_id'] == TARGET['id'] and profile['context']['session_id'] == S
        assert profile['tool_events'] == []
        assert result['recording']['transport'] == 'waylo_livekit'
        assert (tmp_path / 'conversation-continuous.wav').read_bytes() == pcm_wav(b'\x00\x10' * 480)
        assert result['recording']['metadata']['audio_tracks'] == result['local_audio_tracks']
        if capture_failure.startswith('revoked'):
            assert result['local_audio_tracks'][-1]['track_id'] == 'target-aggregate-partial'
            if capture_failure == 'revoked':
                assert read_wav((tmp_path / 'agent-received.wav').read_bytes())[1].endswith(b'\x02\x10' * 120)
            assert not any(call == 'capture' for call in client.calls)
        assert profile['context']['coverage']['agent_execution']['status'] == 'unknown'
        native = next(a['body'] for a in result['ietf_vcon_export']['attachments'] if a.get('purpose') == 'Waylo native source evidence')
        if capture_failure != 'late':
            assert not any(snapshot['phase'] == 'final' for snapshot in profile['state_snapshots'])
            assert native['transcripts'] == [] and 'items' not in native and 'events' not in native
            assert set(native['unavailable']) == {'session', 'transcripts', 'items', 'events', 'agent_version'}
            assert 'local_rtc' in result['ietf_vcon_export']['analysis'][0]['body']['capture_method']
            if capture_failure != 'revoked_during_send':
                assert result['ietf_vcon_export']['dialog'][1]['body'] == 'five bags of white potatoes'
        else:
            assert native['transcripts'] == captured()['transcripts']
            assert native['items'] == []


def test_native_attachment_references_actual_exporter_party(monkeypatch):
    from app.services import waylo_target
    builder = waylo_target.build_ietf_execution_vcon
    def with_observer(**kwargs):
        value = builder(**kwargs)
        value['parties'].append({'name': 'Independent observer', 'type': 'bot', 'validation': 'none'})
        return value
    monkeypatch.setattr(waylo_target, 'build_ietf_execution_vcon', with_observer)
    exported = project_capture(captured())['vcon']
    native = next(a for a in exported['attachments'] if a['purpose'] == 'Waylo native source evidence')
    assert native['party'] < len(exported['parties'])
    profile = next(a for a in exported['attachments'] if a.get('purpose') == PURPOSE)
    assert native['party'] == profile['party']
    assert exported['parties'][native['party']]['name'] == 'VoiceAI QA'


def test_native_attachment_does_not_depend_on_exporter_display_name(monkeypatch):
    from app.services import waylo_target
    attach = waylo_target.attach_evidence
    def renamed_exporter(vcon, body):
        value = attach(vcon, body)
        profile = next(a for a in value['attachments'] if a.get('purpose') == PURPOSE)
        value['parties'][profile['party']]['name'] = 'Renamed evidence recorder'
        return value
    monkeypatch.setattr(waylo_target, 'attach_evidence', renamed_exporter)
    exported = project_capture(captured())['vcon']
    native = next(a for a in exported['attachments'] if a['purpose'] == 'Waylo native source evidence')
    profile = next(a for a in exported['attachments'] if a.get('purpose') == PURPOSE)
    assert native['party'] == profile['party']
    assert exported['parties'][native['party']]['name'] == 'Renamed evidence recorder'
    assert validate_ietf_vcon(exported)['valid']


@pytest.mark.parametrize('preset', ['item_capture_assistant', 'catalog_order_assistant'])
def test_mike_conflicting_config_does_not_start_a_call(tmp_path, preset):
    class Client:
        agent = A
        async def request(self, method, path):
            return {'presetId': preset}
        async def bootstrap(self, correlation):
            pytest.fail('Must not create a call under conflicting Mike rules')
        async def close(self):
            pass
    class Peer:
        async def close(self):
            pass
    async def wording(turns, index):
        return 'five bags'
    async def synthesis(text, index):
        return pcm_wav(b'\x00\x10' * 480)
    with pytest.raises(WayloError, match='conflicts'):
        asyncio.run(run_waylo_call(target=TARGET, correlation_id='same-run', scenario=WAYLO_MIKE_SUITE['scenarios'][0],
            suite_id='waylo-mike-notes', artifact_dir=tmp_path, max_exchanges=1, timeout_seconds=30,
            next_utterance=wording, synthesize=synthesis, client=Client(), peer=Peer()))
