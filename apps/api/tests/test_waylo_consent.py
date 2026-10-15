import copy

import pytest

from app.services.waylo_assessment import assess_speech
from app.services.waylo_consent import approval_integrity


def source(text, turn='caller-1'):
    return {'turn_id': turn, 'reference_text': text, 'event_type': 'tester.audio.sent'}


def receipt(text, identity='asr-1'):
    return {'id': identity, 'text': text, 'role': 'user'}


@pytest.mark.parametrize('denial', [
    "I can't approve based on an assumed price.", 'I cannot approve the purchase.',
    'I can not approve the order.', 'I do not authorize a purchase.',
    'No. Do not place any order.', "Don't place the order yet.",
    'I’m not ready to approve.', 'I approve no order.', 'I am not approving an order.',
    'I can approve once the total is confirmed.',
])
def test_denial_to_approval_is_critical_and_never_verified(denial):
    report = approval_integrity([source(denial)], [receipt('I can approve based on your statement.')])
    assert report['status'] == 'critical_mismatch'
    assert report['requires_review'] is True
    assert report['approval_verified'] is False
    assert report['findings'][0]['reference_quotes'] == [denial]


@pytest.mark.parametrize('text', [
    'I cannot approve until the complete total is clear.',
    'No. I do not authorize a purchase.', 'I can approve once the terms are confirmed.',
    'Could I approve once we know the total?', "I did not say 'I approve'.",
    'Please repeat the price.',
])
def test_denial_conditional_quote_or_question_is_not_a_positive_receipt(text):
    report = approval_integrity([source('No. Do not place the order.')], [receipt(text)])
    assert report['status'] == 'no_mismatch_detected'
    assert report['approval_verified'] is False


def test_split_receipts_still_flag_reproduced_approval_flip_without_forcing_wer():
    refs = [source('Please confirm the total.', 'caller-1'),
            source('I can’t approve based on an assumed price.', 'caller-2')]
    capture = {'transcripts': [receipt('Please confirm.'), receipt('The total.', 'asr-2'),
                              receipt('I can approve based on your statement.', 'asr-3')]}
    before = copy.deepcopy((capture, refs))
    report = assess_speech(capture, refs)
    assert report['status'] == 'unknown' and report['comparisons'] == []
    consent = report['approval_integrity']
    assert consent['status'] == 'critical_mismatch'
    assert consent['findings'][0]['alignment'] == 'whole_call_no_sent_approval'
    assert (capture, refs) == before  # Do not "correct" the ASR or the source.


def test_later_real_approval_does_not_hide_an_earlier_aligned_flip():
    report = approval_integrity([source('I cannot approve.', 'one'), source('I approve the sandbox order.', 'two')],
                                [receipt('I can approve.', 'one'), receipt('I approve the sandbox order.', 'two')])
    assert len(report['findings']) == 1
    assert report['findings'][0]['reference_turn_ids'] == ['one']


def test_split_mixed_history_is_not_falsely_aligned_or_treated_as_verified():
    report = approval_integrity([source('I cannot approve.', 'one'), source('I approve the sandbox order.', 'two')],
                                [receipt('I approve.'), receipt('The sandbox order.'), receipt('Thanks.')])
    assert report['status'] == 'unknown'
    assert report['requires_review'] is True
    assert report['approval_verified'] is False


@pytest.mark.parametrize('refs,receipts', [([], []), ([source('I approve.')], []), ([], [receipt('I approve.')])])
def test_missing_evidence_is_unknown(refs, receipts):
    report = approval_integrity(refs, receipts)
    assert report['status'] == 'unknown'
    assert report['approval_verified'] is False


def test_matching_explicit_approval_is_not_itself_a_verified_order():
    report = approval_integrity([source('I approve this sandbox order.')], [receipt('I approve this sandbox order.')])
    assert report['status'] == 'no_mismatch_detected'
    assert report['approval_verified'] is False
