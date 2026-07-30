from app.services.execution_metrics import build_metrics_and_timeline
from app.services.word_error_rate import calculate_word_error_rate


def test_word_error_rate_ignores_case_punctuation_and_smart_apostrophes():
    result = calculate_word_error_rate(
        "I don\u2019t have a fever.",
        "i don't have a fever",
    )

    assert result is not None
    assert result.as_dict() == {
        'rate': 0.0,
        'percent': 0.0,
        'errors': 0,
        'reference_words': 5,
        'substitutions': 0,
        'deletions': 0,
        'insertions': 0,
    }


def test_word_error_rate_keeps_non_english_words():
    result = calculate_word_error_rate(
        "Mi dirección es Bogotá.",
        "Mi dirección es Bogota.",
    )

    assert result is not None
    assert result.reference_words == 4
    assert result.substitutions == 1


def test_word_error_rate_reports_edit_breakdown():
    result = calculate_word_error_rate(
        "I don't have a fever.",
        "I dawn to have a fever.",
    )

    assert result is not None
    assert result.errors == 2
    assert result.reference_words == 5
    assert result.substitutions == 1
    assert result.deletions == 0
    assert result.insertions == 1
    assert result.as_dict()['percent'] == 40.0


def test_word_error_rate_counts_an_empty_asr_receipt_as_deletions():
    result = calculate_word_error_rate(
        "The recognizer received no usable speech.",
        "",
    )

    assert result is not None
    assert result.reference_words == 6
    assert result.deletions == 6
    assert result.as_dict()['percent'] == 100.0


def test_conversation_metrics_micro_average_voice_turn_word_errors():
    summary, _ = build_metrics_and_timeline(
        turns=[
            {
                'turn_index': 1,
                'frame_metadata': {
                    'source_text': 'One two three four.',
                    'asr_receipt': 'One two four.',
                },
            },
            {
                'turn_index': 2,
                'frame_metadata': {
                    'source_text': 'Five six.',
                    'asr_receipt': 'Five six.',
                },
            },
            {
                'turn_index': 3,
                'text': 'A text-only turn is not eligible.',
            },
        ],
        latency_marks=[],
        verdict='pass',
        score=100,
    )

    assert summary.word_error_rate == {
        'rate': 0.166667,
        'percent': 16.67,
        'errors': 1,
        'reference_words': 6,
        'substitutions': 0,
        'deletions': 1,
        'insertions': 0,
        'turn_count': 2,
    }
