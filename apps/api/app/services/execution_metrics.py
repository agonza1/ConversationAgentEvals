from __future__ import annotations

from statistics import median
from math import isfinite
from typing import Any

from app.schemas.execution import ConversationMetricsSummary, LatencyStats, TimelineEvent
from app.services.word_error_rate import summarize_word_error_rates, word_error_rate_for_turn


def build_metrics_and_timeline(
    *,
    turns: list[Any],
    latency_marks: list[dict[str, Any]],
    verdict: str | None,
    score: float | None,
) -> tuple[ConversationMetricsSummary, list[TimelineEvent]]:
    latencies = [_mark_latency_ms(mark) for mark in latency_marks]
    latencies = [value for value in latencies if value is not None]
    latency_stats = _latency_stats(latencies)
    interruption_count = _interruption_count(turns=turns, latency_marks=latency_marks)
    word_error_rates = [
        value
        for turn in turns or []
        if (value := word_error_rate_for_turn(turn)) is not None
    ]
    timeline = _build_timeline(turns=turns, latency_marks=latency_marks)
    return (
        ConversationMetricsSummary(
            verdict=verdict,
            score=score,
            turn_count=len(turns or []),
            latency=latency_stats,
            interruption_count=interruption_count,
            call_resolution_success=100.0 if verdict == 'pass' else 0.0,
            word_error_rate=summarize_word_error_rates(word_error_rates),
        ),
        timeline,
    )


def _latency_stats(values: list[float]) -> LatencyStats:
    if not values:
        return LatencyStats()
    ordered = sorted(values)
    med = float(median(ordered))
    outlier_count = sum(1 for value in ordered if med > 0 and value > med * 1.5)
    p90_index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.9))))
    return LatencyStats(
        count=len(ordered),
        avg_ms=round(sum(ordered) / len(ordered), 2),
        median_ms=round(med, 2),
        p90_ms=round(float(ordered[p90_index]), 2),
        min_ms=round(float(ordered[0]), 2),
        max_ms=round(float(ordered[-1]), 2),
        outlier_count=outlier_count,
    )


def _mark_latency_ms(mark: dict[str, Any]) -> float | None:
    for key in ('latency_ms', 'duration_ms', 'ms', 'elapsed_ms'):
        value = mark.get(key)
        if type(value) in {int, float} and isfinite(value) and value >= 0:
            return float(value)
    return None


def _interruption_count(*, turns: list[Any], latency_marks: list[dict[str, Any]]) -> int:
    count = 0
    for mark in latency_marks:
        blob = ' '.join(str(mark.get(key) or '') for key in ('label', 'kind', 'type', 'event', 'name')).lower()
        if 'interrupt' in blob:
            count += 1
    for turn in turns or []:
        if isinstance(turn, dict):
            events = turn.get('event_types') or []
            text = str(turn.get('text') or '').lower()
        else:
            events = getattr(turn, 'event_types', None) or []
            text = str(getattr(turn, 'text', '') or '').lower()
        joined = ' '.join(str(item) for item in events).lower()
        if 'interrupt' in joined or 'interrupt' in text:
            count += 1
    return count


def _build_timeline(*, turns: list[Any], latency_marks: list[dict[str, Any]]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for index, mark in enumerate(latency_marks or []):
        latency_ms = _mark_latency_ms(mark)
        label = str(mark.get('label') or mark.get('name') or mark.get('type') or f'mark-{index + 1}')
        kind = str(mark.get('kind') or mark.get('type') or 'mark')
        t_ms = mark.get('t_ms')
        if type(t_ms) not in {int, float} or not isfinite(t_ms) or t_ms < 0:
            t_ms = None
        events.append(
            TimelineEvent(
                t_ms=float(t_ms) if t_ms is not None else None,
                label=label,
                latency_ms=latency_ms,
                kind=kind,
            )
        )

    if events:
        return events

    for index, turn in enumerate(turns or []):
        if isinstance(turn, dict):
            text = str(turn.get('text') or turn.get('speaker') or f'turn-{index + 1}')
            latency_ms = turn.get('latency_ms')
            speaker = turn.get('speaker') or 'turn'
        else:
            text = str(getattr(turn, 'text', None) or getattr(turn, 'speaker', None) or f'turn-{index + 1}')
            latency_ms = getattr(turn, 'latency_ms', None)
            speaker = getattr(turn, 'speaker', None) or 'turn'
        span = float(latency_ms) if type(latency_ms) in {int, float} and isfinite(latency_ms) and latency_ms >= 0 else None
        events.append(
            TimelineEvent(
                t_ms=None,  # Turn order is known; elapsed clock time is not.
                label=f'{speaker}: {text[:48]}',
                latency_ms=span,
                kind='turn',
            )
        )
    return events
