import asyncio
from types import SimpleNamespace

import pytest

from app.services import waylo_livekit
from app.services.waylo_call_policy import CLOSING_TEXT, caller_has_closed, closing_due, closing_reserve_seconds
from app.services.waylo_livekit import LiveKitVoicePeer, pcm_wav, run_waylo_call


def test_voice_safety_prompt_does_not_leak_into_other_testers():
    from app.services.execution_runner import _openai_tester_prompt
    from app.services.waylo_consent import NEGATION_SAFE_CALLER_GUIDANCE
    options = dict(next_exchange=2, max_exchanges=8)
    assert NEGATION_SAFE_CALLER_GUIDANCE not in _openai_tester_prompt({}, [], **options)
    assert NEGATION_SAFE_CALLER_GUIDANCE in _openai_tester_prompt({}, [], waylo_voice=True, **options)


def test_budget_reserves_a_nontransactional_goodbye():
    assert closing_reserve_seconds(180) == 35
    assert closing_due(8, 8, 100, 35)
    assert closing_due(4, 8, 34, 35)
    assert not closing_due(1, 1, 2, 10)
    assert not closing_due(4, 8, 100, 35)
    assert 'order' not in CLOSING_TEXT.lower()
    assert caller_has_closed(CLOSING_TEXT)
    assert caller_has_closed('No purchase today. Thanks, goodbye!')
    assert not caller_has_closed('Thanks for clarifying. What is the price?')


@pytest.mark.parametrize('state', ['speaking', 'thinking', None])
def test_phrase_gap_is_not_a_turn_boundary(monkeypatch, state):
    async def exercise():
        now = [1.0]
        peer = object.__new__(LiveKitVoicePeer)
        peer.events, peer.error, peer.speech_count = [], None, 1
        peer.last_speech, peer.last_remote_transcript = 1.0, 1.0
        peer.agent_state, peer.agent_state_updated = state, 1.0
        peer.pending_remote_transcripts = 0
        actual_sleep = asyncio.sleep

        async def advance(delay):
            now[0] += delay
            # The target resumes after a 1.5s TTS phrase gap, then yields.
            if 2.5 <= now[0] < 3.0:
                peer.last_speech = now[0]
                peer.speech_count += 1
            if now[0] >= 3.0 and state is not None:
                peer.agent_state, peer.agent_state_updated = 'listening', now[0]
            await actual_sleep(0)

        # Change the module's clock reference, not the event loop's time module.
        monkeypatch.setattr(waylo_livekit, 'time', SimpleNamespace(monotonic=lambda: now[0]))
        monkeypatch.setattr(waylo_livekit.asyncio, 'sleep', advance)
        await peer.wait_response(0, timeout=10)
        assert now[0] >= (4.1 if state is not None else 5.4)

    asyncio.run(exercise())


def test_only_selected_target_can_change_observed_agent_state():
    peer = object.__new__(LiveKitVoicePeer)
    peer.remote_identity, peer.events, peer.origin = 'target', [], 0
    peer._observe_agent_state({'lk.agent.state': 'listening'}, SimpleNamespace(identity='other'))
    assert not peer.events
    peer._observe_agent_state({'lk.agent.state': 'speaking'}, SimpleNamespace(identity='target'))
    assert peer.agent_state == 'speaking'
    assert peer.events[-1]['source'] == 'livekit.participant_attributes'


def test_initializing_agent_can_yield_without_a_greeting(monkeypatch):
    async def exercise():
        peer = object.__new__(LiveKitVoicePeer)
        peer.agent_state, peer.speech_count, peer.error = 'initializing', 0, None
        actual_sleep = asyncio.sleep
        async def ready(delay):
            peer.agent_state = 'listening'
            await actual_sleep(0)
        monkeypatch.setattr(waylo_livekit.asyncio, 'sleep', ready)
        await peer.wait_caller_slot(timeout=1)
        assert peer.speech_count == 0
    asyncio.run(exercise())


def test_late_safety_alert_during_cleanup_is_not_a_clean_goodbye(tmp_path):
    from test_waylo_review_regressions import Client, Peer
    from test_waylo_target import TARGET
    class LateAlertPeer(Peer):
        async def close(self):
            self.error = 'Critical purchase-consent ASR mismatch; call stopped for review.'
    async def wording(turns, index):
        return 'Hello.'
    async def synthesis(text, index):
        return pcm_wav(b'\x01\x00' * 100)
    peer = LateAlertPeer()
    result = asyncio.run(run_waylo_call(target=TARGET, correlation_id='test/late',
        scenario={'id': 'adaptive'}, suite_id='suite', artifact_dir=tmp_path,
        max_exchanges=2, timeout_seconds=60, next_utterance=wording,
        synthesize=synthesis, client=Client(), peer=peer))
    assert result['verdict'] == 'failed'
    assert 'purchase-consent' in result['final_state']['tester_error']
    ended = next(event for event in peer.events if event.get('event_type') == 'tester.call_ended')
    assert ended['reason'] == 'execution_error'


@pytest.mark.parametrize('provider_timeout', [False, True])
def test_generation_budget_closes_but_does_not_mask_provider_timeout(tmp_path, monkeypatch, provider_timeout):
    from test_waylo_review_regressions import Client, Peer
    from test_waylo_target import TARGET
    actual_timeout = asyncio.timeout
    # Keep the whole-call deadline; shorten only the inner generation budget.
    monkeypatch.setattr(waylo_livekit.asyncio, 'timeout',
        lambda seconds: actual_timeout(.01 if seconds == 30 else seconds))
    async def wording(turns, index):
        if index == 1:
            return 'Hello, what is the price?'
        if provider_timeout:
            raise TimeoutError('provider timeout')
        await asyncio.Event().wait()
    async def synthesis(text, index):
        return pcm_wav(b'\x01\x00' * 100)
    peer = Peer()
    result = asyncio.run(run_waylo_call(target=TARGET, correlation_id='test/budget',
        scenario={'id': 'adaptive'}, suite_id='suite', artifact_dir=tmp_path,
        max_exchanges=4, timeout_seconds=60, next_utterance=wording,
        synthesize=synthesis, client=Client(), peer=peer))
    callers = [turn['text'] for turn in result['turns'] if turn['speaker'] == 'Caller']
    if provider_timeout:
        assert result['verdict'] == 'failed' and len(callers) == 1
    else:
        assert callers[-1] == CLOSING_TEXT
        assert result['verdict'] == 'needs_review'


def test_approval_polarity_flip_stops_continuation_without_rewriting_receipt():
    peer = SimpleNamespace(events=[], segments={}, transcript_turn_id='caller-1',
        sent_references={'caller-1': {'turn_id': 'caller-1',
            'reference_text': 'No. Do not place any order. I do not authorize a purchase.'}})
    receipt = {'event_id': 'native/asr/1', 'speaker': 'caller', 'segment_id': '1',
        'source': 'livekit.transcription_received', 'received_at_ms': 1000,
        'text': 'I approve the purchase.', 'final': True}
    waylo_livekit._record_transcription(peer, ('caller', '1'), receipt)
    assert peer.segments[('caller', '1')] == 'I approve the purchase.'
    assert peer.consent_alert and 'stopped for review' in peer.error
    assert peer.events[-1]['remote_action_prevention'] == 'not_proven'


@pytest.mark.parametrize('reason', ['exchange_budget_goodbye', 'time_budget_goodbye'])
def test_adaptive_call_sends_goodbye_and_stops_without_claiming_order_success(tmp_path, monkeypatch, reason):
    from test_waylo_review_regressions import Client, Peer
    from test_waylo_target import TARGET
    generated = []

    async def wording(turns, index):
        generated.append(index)
        return 'Hi, how much are five packs of Russet Potatoes?'

    async def synthesis(text, index):
        return pcm_wav(b'\x01\x00' * 100)

    if reason == 'time_budget_goodbye':
        monkeypatch.setattr(waylo_livekit, 'closing_due', lambda index, *args: index > 1)
    peer = Peer()
    result = asyncio.run(run_waylo_call(target=TARGET, correlation_id='test/call',
        scenario={'id': 'adaptive'}, suite_id='suite', artifact_dir=tmp_path,
        max_exchanges=2 if reason == 'exchange_budget_goodbye' else 5, timeout_seconds=60,
        next_utterance=wording, synthesize=synthesis, client=Client(), peer=peer))
    caller = [turn for turn in result['turns'] if turn['speaker'] == 'Caller']
    assert len(caller) == 2 and generated == [1]
    assert caller[-1]['text'] == CLOSING_TEXT
    assert caller[-1]['frame_metadata']['caller_closing'] == reason
    ended = next(event for event in peer.events if event.get('event_type') == 'tester.call_ended')
    assert ended['reason'] == reason and ended['business_completion'] == 'not_inferred'
    assert result['verdict'] == 'needs_review'
