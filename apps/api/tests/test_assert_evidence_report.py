"""Exact evidence display links and speaker labels; no provider/model calls."""
from copy import deepcopy

import pytest

from app.integrations.assert_runtime import Transcript, TranscriptEvent, TranscriptMetadata
from app.services import assert_evidence_report as report
from app.services.assert_transcript_adapter import _parse_transcript, build_assert_inference_row
from app.services.benchmark_service import get_suite


def _conversation():
    return {
        'conversation_id': 'evidence', 'scenario_id': 'correction',
        'turns': [{'speaker': 'caller', 'text': 'I meant afternoon, not morning.'},
                  {'speaker': 'agent', 'text': 'Your afternoon booking is confirmed.'}],
    }


def _metadata(citation=None, *, conversation=None, verdict=None):
    conversation = conversation or _conversation()
    inference = build_assert_inference_row(run={}, conversation=conversation)
    return report.build_evidence_report(inference, {'verdict': verdict or {
        'citations': [citation or {'index': 1, 'description': 'Correction', 'parts': [{
            'quoted_text': 'I meant afternoon, not morning.', 'claimed_message_index': '1',
            'matched_message_index': '1', 'message_id': 'event:0', 'position': [0, 31],
            'resolution': {'status': 'resolved', 'method': 'exact'},
        }]}],
    }}, conversation)


def test_actual_eleven_line_starter_sample_keeps_six_assistant_turns():
    scenario = next(row for row in get_suite('call-center-voice-ai')['scenarios']
                    if row['id'] == 'interruption-correction-handling')
    sample = scenario['sample_transcript']
    assert len(sample.splitlines()) == 11
    parsed = _parse_transcript(sample)
    assert [role for role, _ in parsed].count('user') == 5
    assert [role for role, _ in parsed].count('assistant') == 6
    assert parsed[1] == ('assistant', 'Go ahead. I heard the correction and I will use the updated request.')
    assert parsed[2] == ('user', 'Sorry to interrupt — I meant afternoon, not morning.')
    assert parsed[-1][1].startswith('All set — The corrected afternoon appointment is confirmed')
    inference = build_assert_inference_row(run={}, conversation={'transcript': sample})
    assert len(inference['events']) == 11
    assert [event['edit']['message']['role'] for event in inference['events']] == [role for role, _ in parsed]


@pytest.mark.parametrize('label,role', [
    ('Agent (starter sample agent)', 'assistant'), ('aGeNt Taylor', 'assistant'),
    ('Taylor (assistant)', 'assistant'), ('[00:01.250] Caller Jane', 'user'),
    ('00:01:22 — ASSISTANT (Support)', 'assistant'), ('Tester_A', 'user'),
    ('Customer-Pat', 'user'), ('Patient (new patient)', 'user'),
])
def test_bounded_named_and_timestamped_speaker_labels(label, role):
    assert _parse_transcript(f'{label}: Exact words.\ncontinued line') == [(role, 'Exact words.\ncontinued line')]


def test_unknown_labels_and_preamble_are_retained_without_inventing_tools():
    value = 'Imported note\nLocation: lobby\nCaller: First line\nAgency: unknown label\nTool: not a receipt\nAgent: Reply\nUsername: unknown label'
    assert _parse_transcript(value) == [
        ('system', '[Unstructured CAE transcript evidence]\nImported note\nLocation: lobby'),
        ('user', 'First line\nAgency: unknown label\nTool: not a receipt'),
        ('assistant', 'Reply\nUsername: unknown label'),
    ]
    overlong = 'Agent (' + 'x' * 100 + '): not a bounded role label'
    assert _parse_transcript(overlong) == [('system', '[Unstructured CAE transcript evidence]\n' + overlong)]


def test_exact_native_quote_links_only_to_canonical_event_snapshot():
    metadata = _metadata()
    assert metadata['evidence_details_status'] == 'complete'
    assert metadata['transcript_adapter_version'] == 'cae-assert-evidence-v4'
    assert metadata['evidence_messages'][0] == {
        'id': 'event:0', 'role': 'user', 'text': 'I meant afternoon, not morning.',
    }
    assert metadata['evidence_references'][0]['parts'] == [{
        'quote': 'I meant afternoon, not morning.', 'status': 'matched', 'message_id': 'event:0',
    }]
    assert metadata['evidence_coverage'] == {'transcript': True, 'tool_trace': False, 'final_state': False}


def test_tool_indices_are_native_and_not_reassigned_to_conversation_messages():
    conversation = _conversation()
    conversation['action_trace'] = [{'action': 'lookup', 'before_turn_index': 2,
                                     'tool_result': 'Tool-only exact quote'}]
    inference = build_assert_inference_row(run={}, conversation=conversation)
    native = Transcript(metadata=TranscriptMetadata(kind='scenario', test_case_id='native',
                                                    behavior='test', target='test', tester_model='test'),
                        events=[TranscriptEvent.model_validate(event) for event in inference['events']])
    _, indices = native.format_transcript_xml('target', skip_system=False)
    assert indices == {'1': 'event:0', '2': 'event:1', '3': 'event:2'}
    metadata = report.build_evidence_report(inference, {'verdict': {'highlights':
        '1. <cite id="3" description="Agent">Your afternoon booking is confirmed.</cite>\n'
        '2. <cite id="2" description="Tool">Tool-only exact quote</cite>'}}, conversation)
    assert [message['id'] for message in metadata['evidence_messages']] == ['event:0', 'event:2']
    assert metadata['evidence_references'][0]['parts'][0]['message_id'] == 'event:2'
    tool_part = metadata['evidence_references'][1]['parts'][0]
    assert tool_part['status'] == 'unresolved' and 'message_id' not in tool_part
    assert 'tool evidence' in tool_part['detail']
    assert metadata['evidence_coverage']['tool_trace'] is True


@pytest.mark.parametrize('mutation,expected', [
    ({'resolution': {'status': 'ambiguous', 'detail': 'Native ambiguity'}}, 'ambiguous'),
    ({'resolution': {'status': 'unresolved', 'detail': 'Native quote mismatch'}}, 'unresolved'),
    ({'resolution': {'status': 'resolved', 'method': 'conservative_fuzzy'}}, 'unresolved'),
    ({'position': [1, 31]}, 'unresolved'), ({'message_id': 'event:1'}, 'unresolved'),
    ({'message_id': 'https://example.invalid/private'}, 'unresolved'),
    ({'message_id': '../../private.json'}, 'unresolved'),
    ({'matched_message_index': '2'}, 'unresolved'), ({'claimed_message_index': '999'}, 'unresolved'),
    ({'quoted_text': 'afternoonish'}, 'unresolved'), ({'quoted_text': ''}, 'unresolved'),
])
def test_native_warnings_invalid_indices_and_approximate_quotes_do_not_link(mutation, expected):
    citation = {'index': 8, 'description': 'Native', 'parts': [{
        'quoted_text': 'I meant afternoon, not morning.', 'claimed_message_index': '1',
        'matched_message_index': '1', 'message_id': 'event:0', 'position': [0, 31],
        'resolution': {'status': 'resolved', 'method': 'exact'}, **mutation,
    }]}
    part = _metadata(citation)['evidence_references'][0]['parts'][0]
    assert part['status'] == expected and 'message_id' not in part and part['detail']


@pytest.mark.parametrize('texts', [['That works.', 'That works.'], ['That works. That works.', 'Reply'], ['aaaa', 'Reply']])
def test_repeated_or_overlapping_quotes_are_ambiguous_not_arbitrarily_linked(texts):
    conversation = _conversation()
    for turn, text in zip(conversation['turns'], texts):
        turn['text'] = text
    quote = 'aa' if texts[0] == 'aaaa' else 'That works.'
    metadata = _metadata(conversation=conversation, verdict={'highlights': f'1. <cite id="1" description="Repeated">{quote}</cite>'})
    part = metadata['evidence_references'][0]['parts'][0]
    assert part['status'] == 'ambiguous' and 'message_id' not in part


def test_long_full_quotes_and_parts_are_lossless_not_compatibility_truncated():
    quote = ('Full exact evidence. ' * 60).strip()
    conversation = _conversation()
    conversation['turns'][0]['text'] = quote
    metadata = _metadata(conversation=conversation, verdict={'highlights':
        f'1. <cite id="1" description="Long quote">{quote}</cite>'})
    assert metadata['evidence_messages'][0]['text'] == quote
    assert metadata['evidence_references'][0]['parts'][0]['quote'] == quote.strip()
    assert metadata['evidence_references'][0]['parts'][0]['status'] == 'matched'


def test_native_warnings_and_unstructured_legacy_text_remain_visible_unlinked():
    metadata = _metadata(verdict={'highlights': 'Unstructured legacy quote',
                                 'citation_warnings': ['missing_referenced_citation:3']})
    assert 'missing_referenced_citation:3' in metadata['evidence_details_message']
    part = metadata['evidence_references'][0]['parts'][0]
    assert part['quote'] == 'Unstructured legacy quote' and part['status'] == 'unresolved'
    assert 'message_id' not in part


@pytest.mark.parametrize('limit', ['MAX_EVENTS', 'MAX_SNAPSHOT_CHARS', 'MAX_REFERENCE_CHARS', 'MAX_REFERENCES', 'MAX_PARTS'])
def test_metadata_limits_withhold_partial_snapshots_without_clipping_or_mutation(monkeypatch, limit):
    monkeypatch.setattr(report, limit, 0)
    conversation = _conversation()
    original = deepcopy(conversation)
    metadata = _metadata(conversation=conversation)
    assert metadata['evidence_details_status'] == 'limited'
    assert metadata['evidence_messages'] == [] and metadata['evidence_references'] == []
    assert 'unchanged' in metadata['evidence_details_message']
    assert conversation == original


def test_coverage_tracks_recorded_state_not_a_model_claim_or_bookkeeping_state():
    conversation = _conversation()
    conversation['final_state'] = {'outcome': 'conversation_only_evidence_recorded'}
    assert _metadata(conversation=conversation)['evidence_coverage']['final_state'] is False
    conversation['final_state'] = {'refill_request': 'queued'}
    assert _metadata(conversation=conversation)['evidence_coverage']['final_state'] is True
    conversation['final_state'] = {}
    conversation['unstructured_final_state_evidence'] = 'Reported state, not an execution receipt'
    assert _metadata(conversation=conversation)['evidence_coverage']['final_state'] is True
