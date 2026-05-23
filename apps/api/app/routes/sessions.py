from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.entities import PresentationEvent, PresentationSession
from app.schemas.entities import (
    AutoplayUpdateRequest,
    AskRequest,
    CreateSessionRequest,
    GotoSlideRequest,
    SessionLiveState,
    SessionRead,
    SessionSnapshot,
)
from app.services.realtime_service import realtime_service
from app.services.session_service import (
    ask_question,
    create_session,
    get_session_by_token_or_404,
    get_session_or_404,
    move_slide,
    search_slides,
    serialize_slide,
    set_session_status,
    update_autoplay,
)

router = APIRouter(tags=['sessions'])


@router.post('/api/sessions')
def create_presentation_session(payload: CreateSessionRequest, db: Session = Depends(get_db)):
    session = create_session(db, payload.deck_id)
    return {
        'session_id': session.id,
        'public_token': session.public_token,
        'public_url': f'/present/{session.public_token}',
        'status': session.status,
        'api_base_url': '/api',
    }


@router.get('/api/sessions/{session_id}', response_model=SessionRead)
def get_session(session_id: str, db: Session = Depends(get_db)):
    return get_session_or_404(db, session_id)


@router.get('/api/sessions/{session_id}/live', response_model=SessionLiveState)
def get_session_live_state(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    ordered_slides = sorted(session.deck.slides, key=lambda item: item.index)
    current_slide = next((slide for slide in ordered_slides if slide.index == session.current_slide_index), None)
    transcript_items = sorted(session.transcript_events, key=lambda item: item.created_at)
    recent_events = (
        db.query(PresentationEvent)
        .filter(PresentationEvent.session_id == session_id)
        .order_by(PresentationEvent.created_at.desc())
        .limit(10)
        .all()
    )
    recent_events.reverse()
    upcoming_slides = [slide for slide in ordered_slides if slide.index > session.current_slide_index][:3]

    return SessionLiveState(
        session=session,
        current_slide=serialize_slide(current_slide),
        transcript=transcript_items[-12:],
        recent_events=recent_events,
        upcoming_slides=[serialize_slide(slide) for slide in upcoming_slides],
        progress={
            'current_slide_number': session.current_slide_index + 1,
            'slide_count': len(ordered_slides),
            'remaining_slides': max(len(ordered_slides) - session.current_slide_index - 1, 0),
            'has_started': session.started_at is not None,
        },
    )


@router.post('/api/sessions/{session_id}/start')
def start_session(session_id: str, db: Session = Depends(get_db)):
    return set_session_status(db, session_id, 'presenting')


@router.post('/api/sessions/{session_id}/pause')
def pause_session(session_id: str, db: Session = Depends(get_db)):
    return set_session_status(db, session_id, 'paused')


@router.post('/api/sessions/{session_id}/resume')
def resume_session(session_id: str, db: Session = Depends(get_db)):
    return set_session_status(db, session_id, 'presenting')


@router.post('/api/sessions/{session_id}/end')
def end_session(session_id: str, db: Session = Depends(get_db)):
    return set_session_status(db, session_id, 'ended')


@router.get('/api/sessions/{session_id}/current-slide')
def current_slide(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    slide = next((slide for slide in session.deck.slides if slide.index == session.current_slide_index), None)
    return serialize_slide(slide)


@router.post('/api/sessions/{session_id}/next-slide')
def next_slide(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return move_slide(db, session_id, session.current_slide_index + 1)


@router.post('/api/sessions/{session_id}/autoplay')
def set_autoplay(session_id: str, payload: AutoplayUpdateRequest, db: Session = Depends(get_db)):
    return update_autoplay(db, session_id, payload.enabled, payload.interval_seconds)


@router.post('/api/sessions/{session_id}/advance-autoplay')
def advance_autoplay(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return move_slide(db, session_id, session.current_slide_index + 1, reason='autoplay')


@router.post('/api/sessions/{session_id}/prev-slide')
def prev_slide(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return move_slide(db, session_id, session.current_slide_index - 1)


@router.post('/api/sessions/{session_id}/goto-slide')
def goto_slide(session_id: str, payload: GotoSlideRequest, db: Session = Depends(get_db)):
    return move_slide(db, session_id, payload.index)


@router.post('/api/sessions/{session_id}/restart-current-slide')
def restart_current_slide(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return move_slide(db, session_id, session.current_slide_index, reason='restart')


@router.post('/api/sessions/{session_id}/ask')
def ask(session_id: str, payload: AskRequest, db: Session = Depends(get_db)):
    return ask_question(db, session_id, payload.question)


@router.get('/api/sessions/{session_id}/transcript')
def transcript(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return sorted(session.transcript_events, key=lambda item: item.created_at)


@router.get('/api/sessions/{session_id}/events')
def events(session_id: str, db: Session = Depends(get_db)):
    return db.query(PresentationEvent).filter(PresentationEvent.session_id == session_id).all()


@router.get('/api/sessions/{session_id}/search-slides')
def search_session_slides(session_id: str, query: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    slides = search_slides(session.deck, query)
    return {
        'session_id': session.id,
        'query': query,
        'results': [serialize_slide(slide) for slide in slides[:5]],
    }


@router.get('/api/sessions/{session_id}/realtime-client')
def get_realtime_client(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return realtime_service.get_client_config(session_id=session.id, public_token=session.public_token)


@router.get('/api/public/{token}', response_model=SessionSnapshot)
def get_public_snapshot(token: str, db: Session = Depends(get_db)):
    session = get_session_by_token_or_404(db, token)
    return SessionSnapshot(
        session=session,
        deck=session.deck,
        slides=[serialize_slide(slide) for slide in sorted(session.deck.slides, key=lambda item: item.index)],
        transcript=sorted(session.transcript_events, key=lambda item: item.created_at),
        avatar=None,
        realtime=realtime_service.get_client_config(session_id=session.id, public_token=session.public_token),
    )
