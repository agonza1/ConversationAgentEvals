import asyncio

from outbound_voice import (
    OutboundVoiceRunContext,
    OutboundVoiceTargetDescriptor,
    pcm_to_wav,
)


def test_outbound_run_context_preserves_events_media_and_latency_contract():
    events: list[dict] = []

    async def capture_event(event: dict):
        events.append(event)

    descriptor = OutboundVoiceTargetDescriptor(
        adapter_id='fixture_adapter',
        target_kind='fixture_voice_target',
        transport='fixture_pcm',
        selected_target='fixture-agent',
    )
    run = OutboundVoiceRunContext(descriptor, event_callback=capture_event)
    caller_wav = pcm_to_wav(bytes([1, 0]) * 320, 16_000, 1)

    async def exercise():
        await run.report_phase('target_joined', 'Target joined.')
        await run.report_phase('target_joined', 'Target joined.')
        await run.publish_caller_audio(1, 'Hello target.', caller_wav)
        run.begin_turn(1)
        evidence = run.evidence
        evidence.caller_audio_sent_at = 10.0
        evidence.caller_audio_ended_at = 11.0
        evidence.first_target_audio_at = 11.01
        evidence.first_target_speech_at = 13.5
        evidence.response_complete_at = 15.0
        evidence.target_audio.extend(bytes([2, 0]) * 640)
        evidence.target_audio_frames = 2
        exchange, target_wav = run.complete_exchange(
            turn_pair=1,
            caller_text='Hello target.',
            target_text='Hello tester.',
            caller_wav=caller_wav,
            caller_audio_frames=1,
        )
        await run.publish_exchange(exchange, target_wav)

    asyncio.run(exercise())

    result = run.build_result(
        connection={'connected': True, 'target_joined': True},
        app_messages=[],
        provenance={'live_external_connection': True},
    )

    assert [event['type'] for event in events] == [
        'phase',
        'live_audio',
        'live_audio',
        'exchange',
    ]
    assert result['target'] == {
        'kind': 'fixture_voice_target',
        'selected_agent': 'fixture-agent',
        'transport': 'fixture_pcm',
        'adapter_id': 'fixture_adapter',
    }
    latency = result['exchanges'][0]['latency']
    assert latency['tester_speech_end_to_first_target_speech_received_ms'] == 2500.0
    assert latency['first_target_media_frame_latency_ms'] == 10.0
    assert latency['response_complete_latency_ms'] == 4000.0
    assert latency['signal_boundary'] == 'silero_vad_speech_onset'
    assert result['media']['caller_audio_frames'] == 1
    assert result['media']['target_audio_frames'] == 2
