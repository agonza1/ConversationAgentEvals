"""Verify voice provenance survives the pinned ASSERT judge's actual XML view."""
from copy import deepcopy
from html import unescape
import json

import pytest
from assert_ai.core.judge_citations import extract_xml_citations

from app.integrations.assert_runtime import Transcript, TranscriptEvent, TranscriptMetadata
from app.services import assert_transcript_adapter, upstream_assert_judge
from app.services.assert_transcript_adapter import build_assert_inference_row
from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.assert_review_status import saved_assert_review_freshness
from assert_test_helpers import freeze, refresh_review


def _voice_conversation():
    return {
        'conversation_id': 'voice-correction', 'scenario_id': 'correct-address',
        'mode': 'pipecat_webrtc',
        'turns': [
            {
                'turn_index': 1, 'speaker': 'caller', 'text': 'Use 482 Willow Street.',
                'direction': 'tester_to_target', 'evidence_role': 'target_asr_receipt',
                'frame_metadata': {
                    'source': 'tester_kokoro_audio',
                    'source_text': 'Use 482 Willow Street.',
                    'asr_receipt': 'Use 482 Willow Street.',
                },
            },
            {
                'turn_index': 2, 'speaker': 'agent',
                'text': 'The corrected address is 400 and 82 Willow Street.',
                'direction': 'target_to_tester', 'evidence_role': 'tester_asr_receipt',
                'frame_metadata': {
                    'source': 'target_kokoro_audio',
                    'source_text': 'The corrected address is 482 Willow Street.  ',
                    'asr_receipt': 'The corrected address is 400 and 82 Willow Street.',
                },
            },
        ],
    }


def _row(conversation):
    return build_assert_inference_row(run={'agent_name': 'Voice agent'}, conversation=conversation)


def _transcript(row):
    return Transcript(
        metadata=TranscriptMetadata(
            kind=row['type'], test_case_id=row['test_case_id'], behavior=row['behavior'],
            target=row['target'], tester_model=row['tester_model'], dimensions=row['dimensions'],
        ),
        events=[TranscriptEvent.model_validate(event) for event in row['events']],
        stop_reason=row['stop_reason'],
    )


def _xml(row):
    return _transcript(row).format_transcript_xml('target', skip_system=False)


def test_voice_observations_reach_actual_assert_prompt_without_replacing_asr():
    row = _row(_voice_conversation())
    assert row['dimensions']['evidence_level'] == 'black_box'
    assert row['events'][1]['edit']['message']['content'] == (
        'The corrected address is 400 and 82 Willow Street.'
    )
    observations = [json.loads(event['edit']['tool_result']) for event in row['events'][2:]]
    assert [(item['direction'], item['evidence_role']) for item in observations] == [
        ('tester_to_target', 'target_asr_receipt'), ('target_to_tester', 'tester_asr_receipt'),
    ]
    assert observations[1]['pre_tts_source_text'] == 'The corrected address is 482 Willow Street.  '
    assert observations[1]['receiver_asr_receipt_message_index'] == 2
    assert observations[1]['pre_tts_equals_receiver_asr_exact'] is False
    assert observations[0]['pre_tts_equals_receiver_asr_exact'] is True
    assert observations[0]['pre_tts_source_text_message_index'] == 1
    assert 'pre_tts_source_text' not in observations[0]
    assert 'receiver_asr_receipt' not in observations[1]
    raw = row['events'][3]['raw']['cae_voice_text_observation']
    assert raw['receiver_asr_receipt'] == row['events'][1]['edit']['message']['content']
    assert raw['pre_tts_source_text'] == 'The corrected address is 482 Willow Street.  '
    assert observations[1]['spoken_audio_verified'] is False
    assert 'Neither the pre-TTS source text nor the receiver ASR verifies' in observations[1]['evidence_boundary']
    xml, indices = _xml(row)
    assert '<assistant index="2">\nThe corrected address is 400 and 82 Willow Street.' in xml
    visible = unescape(xml)
    assert 'Recorded text submitted to TTS, before synthesis' in visible
    assert '"pre_tts_source_text": "The corrected address is 482 Willow Street.  "' in visible
    assert '"receiver_asr_receipt_message_index": 2' in visible
    assert visible.count('The corrected address is 400 and 82 Willow Street.') == 1
    assert 'Preserve uncertainty and require audio review' in visible
    assert indices['1'] == 'event:0' and indices['2'] == 'event:1'


def test_annotations_follow_existing_actions_state_and_errors_without_shifting_citations(monkeypatch):
    conversation = _voice_conversation()
    conversation['action_trace'] = [
        {'action': 'lookup', 'before_turn_index': 2, 'tool_result': {'found': True}},
        {'action': 'record', 'after_turn_index': 2, 'tool_result': {'recorded': True}},
    ]
    conversation['final_state'] = {'status': 'observed'}
    conversation['error'] = 'Recorded execution error'
    with monkeypatch.context() as patch:
        patch.setattr(assert_transcript_adapter, '_voice_observation_event', lambda **kwargs: None)
        before = _row(conversation)
    after = _row(conversation)
    assert after['events'][:len(before['events'])] == before['events']
    assert before['dimensions'] == after['dimensions']
    _, before_indices = _xml(before)
    _, after_indices = _xml(after)
    assert all(after_indices[index] == event_id for index, event_id in before_indices.items())
    added = after['events'][len(before['events']):]
    assert len(added) == 2
    assert added[1]['edit']['tool_args'] == {
        'transcript_message_index': 3, 'turn_index': 2,
        'post_run_annotation': True, 'agent_tool_execution': False,
    }


@pytest.mark.parametrize('change', [
    'source_missing', 'receipt_missing', 'unknown_source', 'source_not_text', 'source_kind_not_text',
    'receipt_differs_from_primary',
])
def test_incomplete_or_unclassified_voice_metadata_creates_no_annotation(change):
    conversation = _voice_conversation()
    conversation['turns'] = [conversation['turns'][1]]
    metadata = conversation['turns'][0]['frame_metadata']
    if change == 'source_missing':
        metadata.pop('source_text')
    elif change == 'receipt_missing':
        metadata.pop('asr_receipt')
    elif change == 'unknown_source':
        metadata['source'] = 'imported_unverified_audio'
    elif change == 'source_kind_not_text':
        metadata['source'] = ['target_kokoro_audio']
    elif change == 'receipt_differs_from_primary':
        metadata['asr_receipt'] = 'A different recorded receipt.'
    else:
        metadata['source_text'] = {'text': 'Do not infer a string.'}
    row = _row(conversation)
    assert len(row['events']) == 1
    assert row['events'][0]['edit']['type'] == 'add_message'
    assert row['events'][0]['edit']['message']['content'] == conversation['turns'][0]['text']


def test_duplicate_turn_indices_are_linked_by_list_position_and_transcript_index():
    conversation = _voice_conversation()
    conversation['turns'][1]['turn_index'] = 1
    row = _row(conversation)
    observations = [json.loads(event['edit']['tool_result']) for event in row['events'][2:]]
    assert [item['turn_index'] for item in observations] == [1, 1]
    assert [item['turn_list_position'] for item in observations] == [1, 2]
    assert [item['transcript_message_index'] for item in observations] == [1, 2]


def test_native_citations_resolve_actual_clarification_and_equal_source_readback():
    conversation = _voice_conversation()
    pairs = [
        ('Which address is correct?', 'Which address is correct?'),
        ('I have both 62 Alder Street and 26 Alder Street noted; which street number is correct?',
         'both 62 Alder Street and 26 Alder Street noted. which Street number is correct.'),
        ('26 Alder Street is correct. Could you read that back to me?',
         '26 Alder Street is correct. Could you read that back to me?'),
        ('I have 26 Alder Street. Is that correct?', 'I have 26 Alder Street. Is that correct?'),
    ]
    turns = []
    for index, (source, receipt) in enumerate(pairs, start=1):
        template = deepcopy(conversation['turns'][(index - 1) % 2])
        template.update(turn_index=index, text=receipt)
        template['frame_metadata'].update(source_text=source, asr_receipt=receipt)
        turns.append(template)
    conversation['turns'] = turns
    row = _row(conversation)
    transcript = _transcript(row)
    _, indices = transcript.format_transcript_xml('target', skip_system=False)
    citations = extract_xml_citations(
        '1. <cite id="2" description="Focused clarification">'
        'both 62 Alder Street and 26 Alder Street noted. which Street number is correct.</cite>\n'
        '2. <cite id="4" description="Corrected readback">I have 26 Alder Street. Is that correct?</cite>',
        indices, transcript,
    )
    parts = [citation['parts'][0] for citation in citations]
    assert [part['resolution']['status'] for part in parts] == ['resolved', 'resolved']
    assert [part['message_id'] for part in parts] == ['event:1', 'event:3']
    assert [part['source_kind'] for part in parts] == ['message', 'message']


def test_distinct_source_text_remains_citable_as_annotation_not_assistant_speech():
    row = _row(_voice_conversation())
    transcript = _transcript(row)
    _, indices = transcript.format_transcript_xml('target', skip_system=False)
    citations = extract_xml_citations(
        '1. <cite id="4" description="Recorded synthesis input">'
        'The corrected address is 482 Willow Street.</cite>', indices, transcript,
    )
    part = citations[0]['parts'][0]
    assert part['resolution']['status'] == 'resolved'
    assert part['message_id'] == 'event:3'
    assert part['source_kind'] == 'tool_result'


def test_distinct_source_with_shared_quote_keeps_native_ambiguity():
    conversation = _voice_conversation()
    conversation['turns'] = [conversation['turns'][1]]
    turn = conversation['turns'][0]
    turn['text'] = '482 Willow Street.'
    turn['frame_metadata'].update(
        source_text='The corrected address is 482 Willow Street.',
        asr_receipt='482 Willow Street.',
    )
    row = _row(conversation)
    transcript = _transcript(row)
    _, indices = transcript.format_transcript_xml('target', skip_system=False)
    citations = extract_xml_citations(
        '1. <cite id="1" description="Shared fragment">482 Willow Street.</cite>',
        indices, transcript,
    )
    assert citations[0]['parts'][0]['resolution']['status'] == 'ambiguous'


def test_deduplicated_annotation_changes_full_input_identity_and_stales_old_v3_review():
    conversation = _voice_conversation()
    conversation.update(status='needs_review', verdict='needs_review', score=0)
    run = {'status': 'needs_review', 'agent_name': 'Voice agent'}
    contract = freeze(conversation, {'goal': 'Read back the corrected address.'})
    row = build_assert_inference_row(run=run, conversation=conversation)
    old_row = deepcopy(row)
    for event in old_row['events']:
        if event['edit'].get('tool_name') != 'cae_voice_turn_evidence_observation':
            continue
        old_observation = deepcopy(event['raw']['cae_voice_text_observation'])
        for key in ('receiver_asr_receipt_message_index', 'pre_tts_source_text_message_index',
                    'pre_tts_equals_receiver_asr_exact'):
            old_observation.pop(key, None)
        event['edit']['tool_result'] = json.dumps(old_observation, ensure_ascii=False, sort_keys=True)
        event['raw']['cae_voice_text_observation'] = old_observation
    review = {'model': 'openai/gpt-4.1-mini', 'judge_result': {}}
    refresh_review(run, conversation, review)
    provenance = review['judge_result']['provenance']
    assert provenance['configuration']['adapter_version'] == 'cae-assert-evidence-v3'
    previous_fingerprint = upstream_assert_judge._input_fingerprint(
        review['model'], 1,
        build_assert_taxonomy(scenario_contract=contract, conversation=conversation), old_row,
        configuration=provenance['configuration'],
    )
    assert previous_fingerprint != provenance['input_fingerprint']
    provenance['input_fingerprint'] = previous_fingerprint
    freshness = saved_assert_review_freshness(run, conversation, review)
    assert freshness['status'] == 'stale'
    assert freshness['reason_code'] == 'judging_inputs_changed'
