"""Diagnostic comparisons, NOT an automatic verdict or resolution percentage."""
from __future__ import annotations

from app.services.word_error_rate import calculate_word_error_rate, normalize_words


_NUMBERS = {0: 'zero', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five',
            10: 'ten', 15: 'fifteen', 50: 'fifty'}


def entities(text: str, expected: dict) -> dict:
    words = normalize_words(text)
    product = normalize_words(expected.get('product_name', ''))
    quantity = expected.get('quantity')
    unit = str(expected.get('unit', '')).lower().rstrip('s')
    return {
        'product': any(words[i:i + len(product)] == product for i in range(len(words))) if product else None,
        'quantity': str(quantity) in words or _NUMBERS.get(quantity) in words,
        'unit': bool(unit) and any(w.rstrip('s') == unit for w in words),
    }


def assess_speech(capture: dict, voice_events: list[dict], expected_by_turn: list[list[dict]] | None = None) -> dict:
    references = [e for e in voice_events if e.get('event_type') == 'tester.audio.sent']
    receipts = [t for t in capture['transcripts'] if t.get('role') == 'user']
    comparisons = []
    # No forced alignment when ASR splits/merges utterances. Order-aligned WER
    # is diagnostic only: independent clocks and lack of peer ACK prohibit a
    # causal attribution of recognition vs network loss from this alone.
    if not references or len(references) != len(receipts):
        return {'status': 'unknown', 'reason': 'ASR utterances do not uniquely align with sent reference turns.',
                'reference_turns': len(references), 'receipt_turns': len(receipts), 'comparisons': []}
    for index, (reference, receipt) in enumerate(zip(references, receipts, strict=True)):
        wer = calculate_word_error_rate(reference['reference_text'], receipt['text'])
        expected = (expected_by_turn or [])[index] if index < len(expected_by_turn or []) else []
        local_complete = reference.get('accepted_samples') == reference.get('intended_samples')
        comparisons.append({'turn_id': reference['turn_id'], 'transcript_id': receipt['id'],
            'reference_text': reference['reference_text'], 'target_asr_final': receipt['text'],
            'alignment': 'ordered_one_to_one_provisional', 'scoreable': False,
            'word_error_rate_diagnostic': wer.as_dict() if wer else None,
            'critical_entities': [{'expected': value, 'recognition': entities(receipt['text'], value)} for value in expected],
            'local_send': 'complete' if local_complete else 'clipped_or_incomplete',
            'receiver_drop': 'unknown', 'asr_error_vs_peer_loss': 'needs_review',
            'agent_interpretation': 'requires_tool_and_backend_comparison'})
    return {'status': 'needs_review', 'comparisons': comparisons}


def compare_notes(actual: list[dict] | None, expected: list[dict] | None) -> dict:
    if actual is None or expected is None:
        return {'status': 'unknown', 'reason': 'Backend request list or controlled expected requests unavailable.'}
    comparisons = []
    for index, reference in enumerate(expected):
        item = actual[index] if index < len(actual) else None
        comparisons.append({'expected': reference, 'actual': item,
            'catalog_name_in_raw_text': entities(item.get('rawText', ''), reference)['product'] if item else False,
            'quantity_agrees': bool(item and item.get('quantity') == reference.get('quantity')),
            'unit_agrees': bool(item and str(item.get('unit') or '').lower() == str(reference.get('unit') or '').lower())})
    agrees = len(actual) == len(expected) and all(
        row['catalog_name_in_raw_text'] and row['quantity_agrees'] and row['unit_agrees'] for row in comparisons)
    return {'status': 'observed_agreement' if agrees else 'observed_mismatch', 'comparisons': comparisons,
            'actual_count': len(actual), 'expected_count': len(expected),
            'canonical_product_id': 'not_recorded', 'catalog_lookup_support': 'needs_review',
            'spoken_confirmation_alignment': 'needs_review', 'resolution_verified': False}
