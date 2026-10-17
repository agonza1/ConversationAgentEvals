"""Bounded conversational closure; never an assertion of business success."""
import re


CLOSING_TEXT = "That's all for this call. Thank you for your help. Goodbye."


def closing_reserve_seconds(timeout_seconds: float) -> float:
    return min(35.0, max(10.0, timeout_seconds * .2))


def closing_due(index: int, max_exchanges: int, remaining_seconds: float, reserve_seconds: float) -> bool:
    return index > 1 and (index >= max_exchanges or remaining_seconds <= reserve_seconds)


def caller_has_closed(text: str) -> bool:
    # A thank-you during clarification is not a goodbye. Only an explicit,
    # polite closing suffix ends an adaptive call early.
    return bool(re.search(r'\b(?:thank you|thanks)[^.!?]{0,80}[.!]?\s+(?:goodbye|bye for now)[.!?]*\s*$', text, re.I))
