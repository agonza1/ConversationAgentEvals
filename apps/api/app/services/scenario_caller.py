"""Choose spoken caller text without reading evaluator/persona instructions aloud."""
from __future__ import annotations

import re
from typing import Any


DEFAULT_SPOKEN_OPENER = 'Hi, I have a question and need some help.'
_CALLER_START = re.compile(
    r"^(?:hi\b|hello\b|hey\b|i\b|i['’](?:m|d|ve|ll)\b|my\b|we\b|our\b|please\b|"
    r"can (?:you|i)\b|could (?:you|i)\b|do you\b|what\b|how\b)", re.IGNORECASE)
_INTERNAL_DIRECTIONS = re.compile(
    r'\b(?:synthetic caller|simulated user|tester instructions|caller instructions|'
    r'evaluation rubric|expected outcome|forbidden behaviors)\b', re.IGNORECASE)
_LEGACY_PERSONA_GREETING = re.compile(
    r'^(?:hi|hello|hey)\s*[,—–-]\s*(?:the (?:user|caller|customer)|'
    r'a(?:n)? (?:user|caller|customer|patient|learner))\b', re.IGNORECASE)


def _caller_speech(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ''
    # Conservative legacy compatibility, not a paraphrasing/extraction engine.
    return text if (text and _CALLER_START.match(text) and not _INTERNAL_DIRECTIONS.search(text)
                    and not _LEGACY_PERSONA_GREETING.match(text)) else ''


def scenario_spoken_opener(scenario: dict[str, Any], *, include_sample: bool = True) -> str:
    """Explicit/fixed speech is authoritative; persona/goal text is never rewritten.

    Legacy direct first-person requests still work. Descriptive legacy prompts
    without speech use a neutral deterministic opener, never an unapproved LLM
    completion. Authors should supply spoken_opener to exercise a specific goal.
    """
    steps = scenario.get('caller_steps')
    if isinstance(steps, list) and steps and isinstance(steps[0], str) and steps[0].strip():
        return steps[0].strip()
    explicit = scenario.get('spoken_opener')
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if include_sample:
        for line in str(scenario.get('sample_transcript') or '').splitlines():
            speaker, separator, text = line.strip().partition(':')
            if separator and speaker.strip().lower() in {'user', 'caller', 'customer', 'patient', 'learner'}:
                if speech := _caller_speech(text):
                    return speech
                break
    for field in ('simulated_user_prompt', 'prompt'):
        if speech := _caller_speech(scenario.get(field)):
            return speech
    # Keep the existing title-only smoke opener for programmatic scenarios that
    # never supplied persona instructions. Do not derive speech from descriptions.
    title = scenario.get('title')
    if (not any(scenario.get(field) for field in ('simulated_user_prompt', 'prompt', 'persona'))
            and isinstance(title, str) and title.strip() and not _INTERNAL_DIRECTIONS.search(title)):
        return f'Hi, I need help with {title.strip().lower()}.'
    return DEFAULT_SPOKEN_OPENER
