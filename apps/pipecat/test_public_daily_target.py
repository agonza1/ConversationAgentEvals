import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import outbound_voice
import public_daily_target
import server
from outbound_voice import OutboundVoiceTargetAdapter
from public_daily_target import (
    PublicDailyTargetError,
    PublicDailyTargetRequest,
    _append_unique_message_text,
    _completed_bot_output_text,
    _current_target_text,
    _message_completes_bot_turn,
    _play_caller_turn,
    _wait_for_event_or_error,
    _wait_for_target_audio_drain,
    run_public_daily_target,
)


def test_public_target_timeout_matches_execution_api_limit():
    caller_text = 'Please update my billing address.'

    service_request = server.PublicPipecatRunRequest(
        caller_text=caller_text,
        timeout_seconds=300,
    )
    daily_request = PublicDailyTargetRequest(
        caller_text=caller_text,
        timeout_seconds=300,
    )

    assert service_request.timeout_seconds == 300
    assert daily_request.timeout_seconds == 300


def test_public_daily_target_implements_outbound_adapter_contract():
    adapter = public_daily_target.PublicDailyTargetAdapter('10-gradium')

    assert isinstance(adapter, OutboundVoiceTargetAdapter)
    assert adapter.descriptor.adapter_id == 'public_pipecat_daily'
    assert adapter.descriptor.target_kind == 'pipecat_public_demo'
    assert adapter.descriptor.transport == 'pipecat_daily_webrtc'


def test_caller_live_event_is_published_at_playback_boundary(monkeypatch):
    activity: list[str] = []

    class FakeTask:
        async def queue_frame(self, _frame):
            activity.append('target_playback')

    async def capture_event(event):
        if event['type'] == 'phase':
            activity.append(str(event['phase']))
        elif event['type'] == 'live_audio':
            activity.append(str(event['media_event']))

    async def no_sleep(_delay):
        pass

    monkeypatch.setattr(outbound_voice.asyncio, 'sleep', no_sleep)
    run = outbound_voice.OutboundVoiceRunContext(
        outbound_voice.OutboundVoiceTargetDescriptor(
            adapter_id='test',
            target_kind='test',
            transport='test',
            selected_target='test',
        ),
        event_callback=capture_event,
    )
    caller_pcm = bytes([1, 0]) * 320
    caller_wav = outbound_voice.pcm_to_wav(caller_pcm, 16_000, 1)

    asyncio.run(_play_caller_turn(
        run,
        FakeTask(),
        turn_pair=1,
        caller_text='Please help me.',
        caller_wav=caller_wav,
        caller_pcm=caller_pcm,
        sample_rate=16_000,
        channels=1,
        audio_frame_callback=None,
    ))

    assert activity == ['caller_speaking', 'tester_audio_ready', 'target_playback']
    assert run.evidence.capture_response_audio is True
    assert run.evidence.caller_audio_sent_at is not None
    assert run.evidence.caller_audio_ended_at is not None


def test_completed_bot_output_text_accepts_legacy_spoken_segment():
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {
            'text': 'The completed spoken sentence.',
            'aggregated_by': 'sentence',
            'spoken': True,
        },
    }) == 'The completed spoken sentence.'


def test_completed_bot_output_text_accepts_v2_completed_progress():
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {
            'text': 'The completed spoken sentence.',
            'will_be_spoken': True,
            'spoken_status': 'completed',
            'spoken_progress': {
                'accumulated_text': 'The completed spoken sentence.',
                'remaining_text': '',
            },
        },
    }) == 'The completed spoken sentence.'


def test_completed_bot_output_text_ignores_unspoken_or_in_progress_segments():
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {'text': 'LLM text before TTS.', 'spoken': False},
    }) == ''
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {
            'text': 'Still speaking.',
            'will_be_spoken': True,
            'spoken_status': 'in-progress',
        },
    }) == ''


def test_rtvi_v2_completed_events_finish_bot_turn_without_stopped_speaking():
    assert _message_completes_bot_turn('bot-output', {
        'type': 'bot-output',
        'data': {
            'text': 'Ready to help.',
            'will_be_spoken': True,
            'spoken_status': 'completed',
        },
    }) is True
    assert _message_completes_bot_turn('bot-transcription', {
        'type': 'bot-transcription',
        'data': {'text': 'Ready to help.', 'final': True},
    }) is True
    assert _message_completes_bot_turn('bot-output', {
        'type': 'bot-output',
        'data': {'text': 'Still speaking.', 'will_be_spoken': True, 'spoken_status': 'in-progress'},
    }) is False
    assert _message_completes_bot_turn('bot-transcription', {
        'type': 'bot-transcription',
        'data': {'text': 'A sentence without an explicit completion flag.'},
    }) is False
    assert _message_completes_bot_turn('bot-output', {
        'type': 'bot-output',
        'data': {'text': 'One spoken segment.', 'spoken': True},
    }) is False


def test_transcript_deduplication_resets_between_exchanges():
    run = outbound_voice.OutboundVoiceRunContext(
        outbound_voice.OutboundVoiceTargetDescriptor(
            adapter_id='test',
            target_kind='test',
            transport='test',
            selected_target='test',
        )
    )
    evidence = run.evidence
    repeated = {'type': 'user-transcription', 'data': {'text': 'Please repeat.', 'final': True}}

    assert _append_unique_message_text(
        evidence.caller_transcripts,
        evidence.caller_transcript_keys,
        repeated,
        'Please repeat.',
    ) is True
    assert _append_unique_message_text(
        evidence.caller_transcripts,
        evidence.caller_transcript_keys,
        repeated,
        'Please repeat.',
    ) is False

    caller_count = run.begin_turn(2)
    assert caller_count == 1
    assert _append_unique_message_text(
        evidence.caller_transcripts,
        evidence.caller_transcript_keys,
        repeated,
        'Please repeat.',
    ) is True
    assert evidence.caller_transcripts == ['Please repeat.', 'Please repeat.']


def test_completed_bot_output_is_preferred_over_fallback_transcript():
    evidence = outbound_voice.OutboundVoiceEvidence(
        target_transcripts=['ASR approximation.'],
        target_output_segments=['Authoritative spoken output.'],
    )

    assert _current_target_text(evidence) == 'Authoritative spoken output.'


def test_daily_transport_error_interrupts_active_wait():
    async def fail_soon():
        evidence = outbound_voice.OutboundVoiceEvidence()

        async def signal_error():
            await asyncio.sleep(0.01)
            evidence.errors.append('room secret should not be returned')
            evidence.transport_error.set()

        signal = asyncio.create_task(signal_error())
        with pytest.raises(PublicDailyTargetError, match='Daily transport failed'):
            await _wait_for_event_or_error(
                evidence.connected,
                evidence,
                timeout=2,
                timeout_message='connection timed out',
            )
        await signal

    asyncio.run(fail_soon())


def test_response_finalization_waits_for_trailing_daily_audio(monkeypatch):
    monkeypatch.setattr(public_daily_target, 'TARGET_AUDIO_IDLE_SECONDS', 0.03)
    monkeypatch.setattr(public_daily_target, 'TARGET_AUDIO_DRAIN_TIMEOUT_SECONDS', 0.2)

    async def drain():
        evidence = outbound_voice.OutboundVoiceEvidence(
            capture_response_audio=True,
            last_target_audio_at=public_daily_target.time.perf_counter(),
        )

        async def trailing_packet():
            await asyncio.sleep(0.02)
            evidence.target_audio.extend(b'trailing')
            evidence.last_target_audio_at = public_daily_target.time.perf_counter()

        started = public_daily_target.time.monotonic()
        packet = asyncio.create_task(trailing_packet())
        await _wait_for_target_audio_drain(evidence)
        await packet
        assert public_daily_target.time.monotonic() - started >= 0.045
        assert bytes(evidence.target_audio) == b'trailing'

    asyncio.run(drain())


def test_greeting_drain_waits_for_late_media_without_capturing_it(monkeypatch):
    class FakeVad:
        def __init__(self, *_args, **_kwargs):
            pass

        def set_sample_rate(self, _sample_rate):
            pass

        async def analyze_audio(self, _audio):
            return outbound_voice.VADState.QUIET

        async def cleanup(self):
            pass

    monkeypatch.setattr(outbound_voice, 'SileroVADAnalyzer', FakeVad)
    monkeypatch.setattr(public_daily_target, 'TARGET_AUDIO_IDLE_SECONDS', 0.03)
    monkeypatch.setattr(public_daily_target, 'TARGET_AUDIO_DRAIN_TIMEOUT_SECONDS', 0.2)

    async def drain():
        evidence = outbound_voice.OutboundVoiceEvidence(capture_response_audio=False)
        collector = outbound_voice.OutboundTargetAudioCollector(evidence)
        greeting_frame = outbound_voice.UserAudioRawFrame(
            bytes([1, 0]) * 320,
            sample_rate=16_000,
            num_channels=1,
            user_id='remote-bot',
        )
        completion_at = public_daily_target.time.perf_counter()

        async def delayed_greeting_packet():
            await asyncio.sleep(0.02)
            await collector.process_frame(
                greeting_frame,
                outbound_voice.FrameDirection.DOWNSTREAM,
            )

        started = public_daily_target.time.monotonic()
        packet = asyncio.create_task(delayed_greeting_packet())
        await _wait_for_target_audio_drain(evidence, completed_at=completion_at)
        await packet
        assert public_daily_target.time.monotonic() - started >= 0.045
        assert evidence.last_target_audio_at is not None
        assert bytes(evidence.target_audio) == b''
        assert evidence.target_audio_frames == 0
        await collector.cleanup()

    asyncio.run(drain())


def test_remote_audio_latency_waits_for_confirmed_speech(monkeypatch):
    class FakeVad:
        def __init__(self, *_args, **_kwargs):
                self.states = iter([
                    outbound_voice.VADState.QUIET,
                    outbound_voice.VADState.STARTING,
                    outbound_voice.VADState.SPEAKING,
            ])

        def set_sample_rate(self, _sample_rate):
            pass

        async def analyze_audio(self, _audio):
            return next(self.states)

        async def cleanup(self):
            pass

    monkeypatch.setattr(outbound_voice, 'SileroVADAnalyzer', FakeVad)

    async def collect():
        evidence = outbound_voice.OutboundVoiceEvidence(
            current_turn_pair=1,
            caller_audio_sent_at=public_daily_target.time.perf_counter() - 1,
            caller_audio_ended_at=public_daily_target.time.perf_counter(),
            capture_response_audio=True,
        )
        collector = outbound_voice.OutboundTargetAudioCollector(evidence)
        frame = outbound_voice.UserAudioRawFrame(
            bytes([1, 0]) * 320,
            sample_rate=16_000,
            num_channels=1,
            user_id='remote-bot',
        )
        await collector.process_frame(frame, outbound_voice.FrameDirection.DOWNSTREAM)
        first_media_at = evidence.first_target_audio_at
        assert first_media_at is not None
        assert evidence.first_target_speech_at is None

        await asyncio.sleep(0.025)
        await collector.process_frame(frame, outbound_voice.FrameDirection.DOWNSTREAM)
        assert evidence.first_target_speech_at is None

        await asyncio.sleep(0.025)
        await collector.process_frame(frame, outbound_voice.FrameDirection.DOWNSTREAM)
        assert evidence.first_target_speech_at is not None
        assert evidence.first_target_speech_at > first_media_at
        assert evidence.target_audio_frames == 3
        await collector.cleanup()

    asyncio.run(collect())


def test_public_duplex_reuses_rtvi_text_for_next_tester_turn(monkeypatch):
    observed: dict[str, object] = {}

    async def fake_graph(input_frame, _llm_processor, *, voice):
        observed['tester_input_type'] = type(input_frame).__name__
        observed['tester_input_text'] = input_frame.text
        observed['voice'] = voice
        observed['request'] = _llm_processor.payload
        return SimpleNamespace(transcript=''), SimpleNamespace(
            agent_text='Could you tell me when the cough started?',
            audio=bytes([1, 0]) * 320,
            sample_rate=16_000,
            channels=1,
        )

    async def fake_duplex(_request, **kwargs):
        await kwargs['audio_frame_callback'](
            'target_to_tester', bytes([3, 0]) * 160, 16_000, 1, 1,
        )
        caller_text, caller_wav = await kwargs['next_turn'](
            2,
            'I need a Sunday visit.',
            'I can provide information, but I cannot book appointments.',
            outbound_voice.pcm_to_wav(bytes([2, 0]) * 320, 16_000, 1),
        )
        observed['caller_text'] = caller_text
        observed['caller_wav'] = caller_wav
        await kwargs['next_turn'](
            3,
            'When did this cough start?',
            'What symptoms are you experiencing?',
            outbound_voice.pcm_to_wav(bytes([4, 0]) * 320, 16_000, 1),
        )
        return {'status': 'pass'}

    monkeypatch.setattr(server, '_run_reference_graph', fake_graph)
    monkeypatch.setattr(public_daily_target, 'run_public_daily_duplex', fake_duplex)

    async def collect_events():
        payload = server.PublicPipecatDuplexRequest(
            caller_text='I need a same-day visit.',
            scenario={
                'id': 'triage',
                'persona': 'a patient with a persistent cough',
                'goal': 'Request a same-day visit.',
                'required_actions': ['verify account using two identifiers'],
            },
            max_turn_pairs=3,
            execution_run_id='exec-public-listener',
            session_id='public-session',
        )
        return [json.loads(item) async for item in server._public_pipecat_duplex_events(payload)]

    events = asyncio.run(collect_events())

    assert observed['tester_input_type'] == 'TextFrame'
    assert observed['tester_input_text'] == 'What symptoms are you experiencing?'
    assert observed['caller_text'] == 'Could you tell me when the cough started?'
    assert bytes(observed['caller_wav']).startswith(b'RIFF')
    request = observed['request']
    assert 'verify account using two identifiers' not in request.act_objective
    assert 'patient with a persistent cough' in request.act_objective
    assert request.history == [
        {'speaker': 'Caller', 'text': 'I need a Sunday visit.'},
        {
            'speaker': 'Agent',
            'text': 'I can provide information, but I cannot book appointments.',
        },
        {'speaker': 'Caller', 'text': 'When did this cough start?'},
        {'speaker': 'Agent', 'text': 'What symptoms are you experiencing?'},
    ]
    assert all(
        item['text'] not in {
            'I need a same-day visit.',
            'Could you tell me when the cough started?',
        }
        for item in request.history
    )
    assert events == [{'type': 'complete', 'result': {'status': 'pass'}}]
    broadcast = server.REFERENCE_DUPLEX_RUNS.pop('exec-public-listener')
    assert broadcast.audio_publish_sequence == 1
    assert broadcast.started_listener_media_keys == {
        'public-session:1:target_to_tester',
    }
    assert broadcast.active is False


def test_public_duplex_does_not_require_unused_rtc_asr(monkeypatch):
    async def fake_duplex(_request, **_kwargs):
        return {'status': 'pass'}

    monkeypatch.setattr(server, 'PIPECAT_RUNTIME_AVAILABLE', True)
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', '')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(public_daily_target, 'run_public_daily_duplex', fake_duplex)

    response = TestClient(server.app).post(
        '/public-pipecat/duplex',
        headers={'x-cae-reference-token': 'test-token'},
        json={
            'caller_text': 'I need a same-day visit.',
            'scenario': {'id': 'triage', 'goal': 'Request a same-day visit.'},
            'max_turn_pairs': 1,
        },
    )

    assert response.status_code == 200, response.text
    assert json.loads(response.text.strip()) == {
        'type': 'complete',
        'result': {'status': 'pass'},
    }


def test_public_target_reports_tester_audio_synthesis_stage(monkeypatch):
    async def fail_synthesis(*_args, **_kwargs):
        raise OSError('connection refused')

    monkeypatch.setattr(public_daily_target, '_synthesize_caller', fail_synthesis)

    with pytest.raises(
        PublicDailyTargetError,
        match='tester audio synthesis failed; verify Kokoro is reachable',
    ):
        asyncio.run(run_public_daily_target(
            PublicDailyTargetRequest(caller_text='Please update my billing address.'),
            kokoro_base_url='http://kokoro.invalid',
            kokoro_model='kokoro',
            kokoro_voice='af_heart',
        ))
