from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


@dataclass(frozen=True, slots=True)
class WordErrorRate:
    substitutions: int
    deletions: int
    insertions: int
    reference_words: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def rate(self) -> float:
        return self.errors / self.reference_words

    def as_dict(self) -> dict[str, int | float]:
        return {
            'rate': round(self.rate, 6),
            'percent': round(self.rate * 100, 2),
            'errors': self.errors,
            'reference_words': self.reference_words,
            'substitutions': self.substitutions,
            'deletions': self.deletions,
            'insertions': self.insertions,
        }


def normalize_words(text: str) -> list[str]:
    normalized = unicodedata.normalize('NFKC', text).lower()
    normalized = normalized.replace('\u2018', "'").replace('\u2019', "'")
    return _WORD_RE.findall(normalized)


def calculate_word_error_rate(reference: str, hypothesis: str) -> WordErrorRate | None:
    reference_words = normalize_words(reference)
    if not reference_words:
        return None
    hypothesis_words = normalize_words(hypothesis)

    # Each cell stores (total errors, substitutions, deletions, insertions).
    previous = [(index, 0, 0, index) for index in range(len(hypothesis_words) + 1)]
    for reference_index, reference_word in enumerate(reference_words, start=1):
        current = [(reference_index, 0, reference_index, 0)]
        for hypothesis_index, hypothesis_word in enumerate(hypothesis_words, start=1):
            if reference_word == hypothesis_word:
                current.append(previous[hypothesis_index - 1])
                continue
            substitution = _add_operation(previous[hypothesis_index - 1], 'substitution')
            deletion = _add_operation(previous[hypothesis_index], 'deletion')
            insertion = _add_operation(current[hypothesis_index - 1], 'insertion')
            # Stable tie-breaking favors substitution, then deletion, then insertion.
            current.append(min((substitution, deletion, insertion), key=lambda value: value[0]))
        previous = current

    _, substitutions, deletions, insertions = previous[-1]
    return WordErrorRate(
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        reference_words=len(reference_words),
    )


def summarize_word_error_rates(values: Iterable[WordErrorRate]) -> dict[str, int | float] | None:
    collected = list(values)
    reference_words = sum(value.reference_words for value in collected)
    if not collected or not reference_words:
        return None
    substitutions = sum(value.substitutions for value in collected)
    deletions = sum(value.deletions for value in collected)
    insertions = sum(value.insertions for value in collected)
    summary = WordErrorRate(
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        reference_words=reference_words,
    ).as_dict()
    summary['turn_count'] = len(collected)
    return summary


def word_error_rate_for_turn(turn: Any) -> WordErrorRate | None:
    if isinstance(turn, dict):
        metadata = turn.get('frame_metadata')
    else:
        metadata = getattr(turn, 'frame_metadata', None)
    if not isinstance(metadata, dict):
        return None
    reference = metadata.get('source_text') or metadata.get('llm_output')
    hypothesis = metadata.get('asr_receipt')
    if not isinstance(reference, str) or not isinstance(hypothesis, str):
        return None
    return calculate_word_error_rate(reference, hypothesis)


def _add_operation(
    value: tuple[int, int, int, int],
    operation: str,
) -> tuple[int, int, int, int]:
    total, substitutions, deletions, insertions = value
    if operation == 'substitution':
        substitutions += 1
    elif operation == 'deletion':
        deletions += 1
    else:
        insertions += 1
    return total + 1, substitutions, deletions, insertions
