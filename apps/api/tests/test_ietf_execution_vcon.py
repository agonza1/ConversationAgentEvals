from __future__ import annotations

import base64
import uuid

from app.services.execution_vcon import (
    build_ietf_execution_vcon,
    ietf_vcon_summary,
    validate_ietf_vcon,
)


SHA512_BASE64URL = base64.urlsafe_b64encode(bytes(64)).decode('ascii').rstrip('=')


def test_ietf_execution_vcon_uses_spoken_text_and_keeps_asr_as_analysis():
    exported = build_ietf_execution_vcon(
        conversation_id='conversation-1',
        execution_run_id='exec-1',
        suite_id='voice',
        scenario_id='appointment',
        scenario_title='New patient triage',
        mode='pipecat_webrtc',
        turns=[
            {
                'turn_index': 1,
                'speaker': 'caller',
                'text': 'Hi, I am a new patient.',
                'direction': 'tester_to_target',
                'frame_metadata': {
                    'source_text': 'Hi, I am a new patient and need care today.',
                    'asr_receipt': 'Hi, I am a new patient.',
                    'word_error_rate': {'percent': 25.0},
                },
            },
            {
                'turn_index': 2,
                'speaker': 'agent',
                'text': 'What is your date of birth?',
                'direction': 'target_to_tester',
                'frame_metadata': {
                    'source_text': 'What is your full name and date of birth?',
                    'asr_receipt': 'What is your date of birth?',
                },
            },
        ],
        recording={
            'recording_url': 'https://evidence.example.test/runs/exec-1.wav',
            'recording_sha512': SHA512_BASE64URL,
            'mime_type': 'audio/wav',
            'duration_ms': 2500,
        },
        created_at='2026-09-26T10:00:00+00:00',
        updated_at='2026-09-26T10:00:03+00:00',
        final_state={
            'complete': False,
            'outcome': 'reference_conversation_captured',
            'runtime_provenance': {'internal_token': 'must-not-export'},
        },
        verdict='needs_review',
        score=33,
    )

    assert exported['vcon'] == '0.4.0'
    assert uuid.UUID(exported['uuid']).version == 4
    assert exported['created_at'] == '2026-09-26T10:00:00Z'
    assert exported['parties'] == [
        {'name': 'Caller', 'type': 'bot', 'validation': 'none'},
        {'name': 'Agent', 'type': 'bot', 'validation': 'none'},
        {'name': 'ConVoice QA', 'type': 'bot', 'validation': 'none'},
    ]
    assert exported['dialog'][0] == {
        'type': 'text',
        'parties': [0],
        'mediatype': 'text/plain',
        'body': 'Hi, I am a new patient and need care today.',
        'encoding': 'none',
    }
    assert exported['dialog'][1]['body'] == 'What is your full name and date of birth?'
    assert exported['dialog'][2] == {
        'type': 'recording',
        'parties': [0, 1],
        'mediatype': 'audio/wav',
        'url': 'https://evidence.example.test/runs/exec-1.wav',
        'content_hash': SHA512_BASE64URL,
        'duration': 2.5,
    }
    transcript_turns = exported['analysis'][0]['body']['turns']
    assert transcript_turns[0]['peer_asr_receipt'] == 'Hi, I am a new patient.'
    assert transcript_turns[0]['word_error_rate'] == {'percent': 25.0}
    assert exported['analysis'][1]['body']['final_state'] == {
        'complete': False,
        'outcome': 'reference_conversation_captured',
    }
    assert exported['analysis'][1]['body']['vcon_core_draft'] == 'draft-ietf-vcon-vcon-core-04'
    assert validate_ietf_vcon(exported) == {'valid': True, 'errors': []}
    assert ietf_vcon_summary(exported)['recording_portable'] is True


def test_ietf_execution_vcon_omits_local_recording_from_portable_media():
    exported = build_ietf_execution_vcon(
        conversation_id='conversation-2',
        execution_run_id='exec-2',
        suite_id='voice',
        scenario_id='appointment',
        scenario_title=None,
        mode='pipecat_webrtc',
        turns=[{'turn_index': 1, 'speaker': 'caller', 'text': 'Please help me.'}],
        recording={
            'recording_url': '/api/execution/runs/exec-2/conversations/conversation-2/recording',
            'recording_sha512': 'still-not-portable',
        },
        created_at='2026-09-26T10:00:00Z',
        updated_at='2026-09-26T10:00:03Z',
    )

    assert [item['type'] for item in exported['dialog']] == ['text']
    assert exported['analysis'][1]['body']['recording']['status'] == 'retained_locally'
    summary = ietf_vcon_summary(exported)
    assert summary['valid'] is True
    assert summary['recording_portable'] is False
    assert summary['signed'] is False
    assert summary['standard_draft'] == 'draft-ietf-vcon-vcon-core-04'


def test_ietf_execution_vcon_marks_a_target_only_recording_with_the_target_party():
    exported = build_ietf_execution_vcon(
        conversation_id='conversation-3',
        execution_run_id='exec-3',
        suite_id='voice',
        scenario_id='appointment',
        scenario_title=None,
        mode='pipecat_webrtc',
        turns=[{'turn_index': 1, 'speaker': 'agent', 'text': 'I can help.'}],
        recording={
            'recording_url': 'https://evidence.example.test/runs/exec-3-target.wav',
            'recording_sha512': SHA512_BASE64URL,
            'metadata': {'scope': 'target_response_only'},
        },
        created_at='2026-09-26T10:00:00Z',
        updated_at='2026-09-26T10:00:03Z',
    )

    assert exported['dialog'][-1]['type'] == 'recording'
    assert exported['dialog'][-1]['parties'] == [1]


def test_ietf_execution_vcon_omits_recording_with_an_invalid_content_hash():
    exported = build_ietf_execution_vcon(
        conversation_id='conversation-4',
        execution_run_id='exec-4',
        suite_id='voice',
        scenario_id='appointment',
        scenario_title=None,
        mode='pipecat_webrtc',
        turns=[{'turn_index': 1, 'speaker': 'agent', 'text': 'I can help.'}],
        recording={
            'recording_url': 'https://evidence.example.test/runs/exec-4.wav',
            'recording_sha512': 'sha512-not-a-standard-digest',
        },
        created_at='2026-09-26T10:00:00Z',
        updated_at='2026-09-26T10:00:03Z',
    )

    assert [item['type'] for item in exported['dialog']] == ['text']
    assert exported['analysis'][1]['body']['recording']['status'] == 'not_portable'


def test_ietf_execution_vcon_validator_rejects_incorrect_format_version():
    result = validate_ietf_vcon({'vcon': '0.0.1', 'dialog': []})

    assert result['valid'] is False
    assert 'vcon must be 0.4.0' in result['errors']
    assert 'uuid must be a UUID' in result['errors']
    assert 'parties must contain at least one participant' in result['errors']
