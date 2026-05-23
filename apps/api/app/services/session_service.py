from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.entities import Deck, PresentationEvent, PresentationSession, Slide, TranscriptEvent
from app.schemas.entities import AskResponse, SlideRead
from app.services.grounding_service import grounding_service

AUTOPLAY_MIN_INTERVAL_SECONDS = 4
AUTOPLAY_DEFAULT_INTERVAL_SECONDS = 8
AUTOPLAY_MAX_INTERVAL_SECONDS = 20


def get_deck_or_404(db: Session, deck_id: str) -> Deck:
    deck = db.query(Deck).options(joinedload(Deck.slides)).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail='Deck not found')
    return deck


def get_session_or_404(db: Session, session_id: str) -> PresentationSession:
    session = (
        db.query(PresentationSession)
        .options(joinedload(PresentationSession.deck).joinedload(Deck.slides), joinedload(PresentationSession.transcript_events))
        .filter(PresentationSession.id == session_id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail='Session not found')
    return session


def get_session_by_token_or_404(db: Session, token: str) -> PresentationSession:
    session = (
        db.query(PresentationSession)
        .options(joinedload(PresentationSession.deck).joinedload(Deck.slides), joinedload(PresentationSession.transcript_events))
        .filter(PresentationSession.public_token == token)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail='Public session not found')
    return session


def create_session(db: Session, deck_id: str) -> PresentationSession:
    deck = get_deck_or_404(db, deck_id)
    session = PresentationSession(deck_id=deck.id)
    db.add(session)
    db.flush()
    _log_event(db, session.id, 'session_created', {'deck_id': deck.id})
    _add_transcript(db, session.id, 'system', f'Session created for deck "{deck.title}".')
    db.commit()
    db.refresh(session)
    return session


def set_session_status(db: Session, session_id: str, status: str) -> PresentationSession:
    session = get_session_or_404(db, session_id)
    current_slide = _get_current_slide(session)
    first_start = status == 'presenting' and session.started_at is None
    session.status = status
    if first_start:
        session.started_at = datetime.now(UTC)
    if status == 'presenting' and session.autoplay_enabled:
        session.autoplay_started_at = datetime.now(UTC)
    elif status in {'paused', 'ended', 'answering', 'idle'}:
        session.autoplay_started_at = None
    db.add(session)
    _log_event(db, session.id, f'presentation_{status}', {'status': status})
    if status == 'presenting' and current_slide:
        _add_transcript(db, session.id, 'agent', _build_presenter_line(current_slide, opening=first_start))
    elif status == 'paused':
        _add_transcript(db, session.id, 'system', 'Presentation paused.')
    elif status == 'ended':
        _add_transcript(db, session.id, 'system', 'Presentation ended.')
    db.commit()
    db.refresh(session)
    return session


def move_slide(db: Session, session_id: str, target_index: int, reason: str = 'manual') -> PresentationSession:
    session = get_session_or_404(db, session_id)
    slides = sorted(session.deck.slides, key=lambda slide: slide.index)
    if not slides:
        raise HTTPException(status_code=400, detail='Deck has no slides')

    bounded_index = max(0, min(target_index, len(slides) - 1))
    session.current_slide_index = bounded_index
    current_slide = next((slide for slide in slides if slide.index == bounded_index), None)
    if session.autoplay_enabled and session.status == 'presenting':
        session.autoplay_started_at = datetime.now(UTC)
    db.add(session)
    _log_event(db, session.id, 'slide_changed', {'slide_index': bounded_index, 'reason': reason})
    _add_transcript(db, session.id, 'system', f"Moved to slide {bounded_index + 1}{' automatically' if reason == 'autoplay' else ''}.")
    if session.status in {'presenting', 'answering'} and current_slide:
        _add_transcript(db, session.id, 'agent', _build_presenter_line(current_slide, autoplay=(reason == 'autoplay')))
        if session.status == 'answering':
            session.status = 'presenting'
            if session.autoplay_enabled:
                session.autoplay_started_at = datetime.now(UTC)
            db.add(session)
            _log_event(db, session.id, 'presentation_presenting', {'status': 'presenting', 'reason': 'slide advanced'})
    db.commit()
    db.refresh(session)
    return session


def ask_question(db: Session, session_id: str, question: str) -> AskResponse:
    session = get_session_or_404(db, session_id)
    slide = _get_current_slide(session)
    return _answer_question(db, session, question, slide)


def append_live_transcript(db: Session, session_id: str, role: str, text: str) -> TranscriptEvent:
    session = get_session_or_404(db, session_id)
    event = TranscriptEvent(session_id=session.id, role=role, text=text)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def append_live_answer(db: Session, session_id: str, question: str, answer: str) -> AskResponse:
    session = get_session_or_404(db, session_id)
    slide = _get_current_slide(session)
    citations = _build_citations(slide)
    session.autoplay_started_at = None
    session.status = 'answering'
    db.add(session)
    _add_transcript(db, session.id, 'user', question)
    _add_transcript(db, session.id, 'agent', answer)
    _log_event(db, session.id, 'live_question_heard', {'question': question})
    _log_event(db, session.id, 'live_answer_generated', {'answer': answer, 'citations': citations})
    db.commit()
    return AskResponse(answer=answer, citations=citations, session_status='answering')


def search_slides(deck: Deck, query: str) -> list[Slide]:
    terms = [term.lower() for term in query.split() if term.strip()]
    scored: list[tuple[int, Slide]] = []
    for slide in deck.slides:
        haystack = ' '.join([slide.title, slide.summary, slide.raw_text]).lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, slide))
    scored.sort(key=lambda item: (-item[0], item[1].index))
    return [slide for _, slide in scored]


def serialize_slide(slide: Slide | None) -> SlideRead | None:
    if not slide:
        return None
    return SlideRead(
        id=slide.id,
        deck_id=slide.deck_id,
        index=slide.index,
        title=slide.title,
        image_url=_normalize_image_url(slide.image_path),
        raw_text=slide.raw_text,
        speaker_notes=slide.speaker_notes,
        summary=slide.summary,
        talk_track=slide.talk_track,
        faq_json=json.loads(slide.faq_json or '[]'),
    )


def _normalize_image_url(path: str | None) -> str | None:
    if not path:
        return None
    marker = '/storage/'
    normalized = path.replace('\\', '/')
    if marker in normalized:
        return normalized[normalized.index(marker):]
    return normalized


def _get_current_slide(session: PresentationSession) -> Slide | None:
    for slide in session.deck.slides:
        if slide.index == session.current_slide_index:
            return slide
    return None


def _answer_question(db: Session, session: PresentationSession, question: str, slide: Slide | None) -> AskResponse:
    session.autoplay_started_at = None
    related_slides = _find_related_slides(session.deck.slides, question, slide.index if slide else 0)
    grounded = grounding_service.answer_question(
        deck=session.deck,
        current_slide=slide,
        related_slides=related_slides,
        question=question,
    )

    session.status = 'answering'
    db.add(session)
    _add_transcript(db, session.id, 'user', question)
    _add_transcript(db, session.id, 'agent', grounded.answer)
    _log_event(db, session.id, 'question_asked', {'question': question})
    _log_event(db, session.id, 'answer_generated', {'answer': grounded.answer, 'citations': grounded.citations})
    db.commit()

    return AskResponse(answer=grounded.answer, citations=grounded.citations, session_status='answering')


def _build_citations(slide: Slide | None) -> list[dict[str, str | int]]:
    if not slide:
        return []
    return [
        {
            'slide_index': slide.index,
            'title': slide.title,
        }
    ]


def _find_related_slides(slides: list[Slide], query: str, current_index: int) -> list[Slide]:
    terms = [term.lower() for term in query.split() if len(term) > 2]
    ranked: list[tuple[int, int, Slide]] = []
    for slide in slides:
        haystack = ' '.join([slide.title, slide.summary, slide.raw_text]).lower()
        overlap = sum(1 for term in terms if term in haystack)
        distance_penalty = abs(slide.index - current_index)
        ranked.append((overlap, -distance_penalty, slide))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].index))

    related: list[Slide] = []
    for overlap, _, slide in ranked:
        if slide.index == current_index or overlap > 0:
            related.append(slide)
        if len(related) >= 3:
            break
    return related


def update_autoplay(db: Session, session_id: str, enabled: bool, interval_seconds: int | None = None) -> PresentationSession:
    session = get_session_or_404(db, session_id)
    interval = interval_seconds if interval_seconds is not None else session.autoplay_interval_seconds or AUTOPLAY_DEFAULT_INTERVAL_SECONDS
    interval = max(AUTOPLAY_MIN_INTERVAL_SECONDS, min(interval, AUTOPLAY_MAX_INTERVAL_SECONDS))
    session.autoplay_enabled = enabled
    session.autoplay_interval_seconds = interval
    session.autoplay_started_at = datetime.now(UTC) if enabled and session.status == 'presenting' else None
    db.add(session)
    _log_event(
        db,
        session.id,
        'autoplay_updated',
        {'enabled': enabled, 'interval_seconds': interval, 'status': session.status},
    )
    _add_transcript(
        db,
        session.id,
        'system',
        f"Autoplay {'started' if enabled else 'stopped'} at {interval} seconds per slide.",
    )
    db.commit()
    db.refresh(session)
    return session


def _build_presenter_line(slide: Slide, opening: bool = False, autoplay: bool = False) -> str:
    prefix = f"Let's start with slide {slide.index + 1}." if opening else f"Now on slide {slide.index + 1}."
    bridge = ' Keeping the live demo moving,' if autoplay and not opening else ''
    return f"{prefix}{bridge} {slide.talk_track}".strip()


def _add_transcript(db: Session, session_id: str, role: str, text: str) -> None:
    db.add(TranscriptEvent(session_id=session_id, role=role, text=text))


def _log_event(db: Session, session_id: str, event_type: str, payload: dict) -> None:
    db.add(PresentationEvent(session_id=session_id, type=event_type, payload_json=json.dumps(payload)))
