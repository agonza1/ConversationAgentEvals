"""Dual LiveKit receipts are one utterance, not a global text deduplication rule."""
import asyncio
from types import SimpleNamespace

from app.services.waylo_livekit import LiveKitVoicePeer, _record_transcription


LEGACY = 'livekit.transcription_received'
MODERN = 'livekit.lk.transcription'


def peer():
    return SimpleNamespace(events=[], segments={}, transcript_turn_id='greeting')


def receipt(p, segment, text, *, source=LEGACY, at=1000, final=True, speaker='remote', track='agent-track'):
    event = {'event_id': f'{source}/{speaker}/{segment}/{text}/{final}', 'source': source,
        'segment_id': segment, 'text': text, 'final': final, 'speaker': speaker,
        'received_at_ms': at, 'event_type': 'transcript.remote' if speaker == 'remote' else 'asr.final',
        'transcribed_track_id': track}
    _record_transcription(p, (speaker, segment), event)


def test_same_greeting_across_distinct_legacy_and_modern_ids_is_displayed_once():
    p = peer()
    text = 'Hi, I am Steven, an order agent. How can I help?'
    receipt(p, 'SG_legacy', 'Hi,', final=False, at=100)
    receipt(p, 'SG_legacy', text, at=1000)
    receipt(p, 'SG_modern', text, source=MODERN, at=1000.3)
    assert list(p.segments.values()) == [text]
    assert len(p.events) == 3  # Native partial and both finals remain reviewable.
    assert p.events[-1]['duplicate_of_segment_id'] == 'SG_legacy'


def test_pairing_is_one_to_one_and_legitimate_repeated_wording_survives():
    p = peer()
    for number, at in [(1, 1000), (2, 1200)]:
        receipt(p, f'legacy-{number}', 'Yes.', at=at)
        receipt(p, f'modern-{number}', 'Yes.', source=MODERN, at=at + .3)
    assert list(p.segments.values()) == ['Yes.', 'Yes.']
    assert len(p.events) == 4


def test_later_identical_turn_same_speaker_and_tracks_does_not_collapse():
    p = peer()
    receipt(p, 'legacy-greeting', 'Thanks.', at=1000)
    p.transcript_turn_id = 'caller-1'
    receipt(p, 'modern-reply', 'Thanks.', source=MODERN, at=1000.2)
    assert len(p.segments) == 2
    assert p.events[-1]['observation_turn_id'] == 'caller-1'


def test_distinct_tracks_speakers_and_late_receipts_remain_distinct():
    p = peer()
    receipt(p, 'first', 'Five bags.', at=1000)
    receipt(p, 'different-track', 'Five bags.', source=MODERN, at=1001, track='other-track')
    receipt(p, 'caller', 'Five bags.', source=MODERN, at=1001, speaker='caller')
    receipt(p, 'late', 'Five bags.', source=MODERN, at=2500)
    assert len(p.segments) == 4


def test_partial_updates_do_not_erase_final_and_corrected_final_replaces_exact_text():
    p = peer()
    receipt(p, 'legacy', 'Five bags.', at=1000)
    receipt(p, 'modern', 'Five bags.', source=MODERN, at=1001)
    receipt(p, 'modern', 'Five', source=MODERN, at=1002, final=False)
    assert list(p.segments.values()) == ['Five bags.']
    receipt(p, 'modern', 'Five boxes.', source=MODERN, at=1003)
    assert list(p.segments.values()) == ['Five boxes.']
    assert p.events[-1]['text'] == 'Five boxes.'


def test_actual_text_stream_handler_reuses_the_legacy_final_without_rewriting():
    async def exercise():
        p = peer()
        p.room = SimpleNamespace(local_participant=SimpleNamespace(track_publications={}))
        p.remote_identity = 'agent'
        p.now_ms = lambda: 1001
        class Reader:
            info = SimpleNamespace(attributes={'lk.segment_id': 'modern',
                'lk.transcribed_track_id': 'agent-track', 'lk.transcription_final': 'true'}, stream_id='stream')
            async def read_all(self):
                return 'I only need information.'
        receipt(p, 'legacy', 'I only need information.')
        await LiveKitVoicePeer._read_transcription(p, Reader(), 'agent')
        assert list(p.segments.values()) == ['I only need information.']
        assert len(p.events) == 2
    asyncio.run(exercise())
