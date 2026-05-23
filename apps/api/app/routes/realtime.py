from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.heygen_service import heygen_service
from app.services.pipecat_service import pipecat_service
from app.services.realtime_service import realtime_service
from app.services.session_service import append_live_answer, append_live_transcript, get_session_or_404, search_slides, serialize_slide

router = APIRouter(prefix='/api/realtime', tags=['realtime'])


class LiveTranscriptRequest(BaseModel):
    role: str
    text: str


class LiveAnswerRequest(BaseModel):
    question: str
    answer: str



@router.get('/sessions/{session_id}/contract')
def get_realtime_contract(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    current_slide = next((slide for slide in session.deck.slides if slide.index == session.current_slide_index), None)
    tool_manifest = pipecat_service.build_tool_manifest(session_id=session.id)
    tools = {
        'get_session_state': f'/api/sessions/{session.id}',
        'get_current_slide': f'/api/sessions/{session.id}/current-slide',
        'search_slides': f'/api/sessions/{session.id}/search-slides?query={{query}}',
        'next_slide': f'/api/sessions/{session.id}/next-slide',
        'prev_slide': f'/api/sessions/{session.id}/prev-slide',
        'goto_slide': f'/api/sessions/{session.id}/goto-slide',
        'restart_current_slide': f'/api/sessions/{session.id}/restart-current-slide',
        'pause_presentation': f'/api/sessions/{session.id}/pause',
        'resume_presentation': f'/api/sessions/{session.id}/resume',
        'log_event': f'/api/sessions/{session.id}/events',
        'realtime_search': f'/api/realtime/sessions/{session.id}/search?query={{query}}',
        'get_slide_content': f'/api/realtime/sessions/{session.id}/slide-content/{{slide_index}}',
    }
    return {
        'session_id': session.id,
        'public_token': session.public_token,
        'session_status': session.status,
        'deck': {
            'id': session.deck.id,
            'title': session.deck.title,
            'manifest_json': _parse_manifest_json(session.deck.manifest_json),
        },
        'tools': tools,
        'tool_manifest': tool_manifest,
        'current_slide': serialize_slide(current_slide),
        'realtime': realtime_service.get_client_config(session_id=session.id, public_token=session.public_token),
        'avatar': heygen_service.get_client_config(),
        'pipecat_plan': pipecat_service.build_session_plan(
            session_id=session.id,
            public_token=session.public_token,
            contract={'tools': tools, 'tool_manifest': tool_manifest},
        ),
        'bootstrap': f'/api/bootstrap/sessions/{session.id}',
    }


@router.post('/sessions/{session_id}/pipecat-session')
def create_pipecat_session(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return realtime_service.create_pipecat_bootstrap(session=session)


@router.get('/sessions/{session_id}/instructions')
def get_pipecat_instructions(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    current_slide = next((slide for slide in session.deck.slides if slide.index == session.current_slide_index), None)
    manifest = _parse_manifest_json(session.deck.manifest_json)
    return {
        'session_id': session.id,
        'public_token': session.public_token,
        'instructions': pipecat_service.build_realtime_instructions(
            session=session,
            current_slide=current_slide,
            manifest=manifest,
        ),
    }


@router.get('/sessions/{session_id}/slide-content/{slide_index}')
def get_slide_content(session_id: str, slide_index: int, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    slide = next((item for item in session.deck.slides if item.index == slide_index), None)
    if not slide:
        raise HTTPException(status_code=404, detail='Slide not found')
    return serialize_slide(slide)


@router.get('/sessions/{session_id}/search')
def realtime_search(session_id: str, query: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    results = search_slides(session.deck, query)
    return {
        'session_id': session.id,
        'query': query,
        'results': [serialize_slide(item) for item in results[:5]],
    }


@router.post('/sessions/{session_id}/transcript')
def create_live_transcript(session_id: str, payload: LiveTranscriptRequest, db: Session = Depends(get_db)):
    event = append_live_transcript(db, session_id, payload.role, payload.text)
    return {
        'id': event.id,
        'session_id': event.session_id,
        'role': event.role,
        'text': event.text,
        'created_at': event.created_at,
    }


@router.post('/sessions/{session_id}/answer')
def create_live_answer(session_id: str, payload: LiveAnswerRequest, db: Session = Depends(get_db)):
    return append_live_answer(db, session_id, payload.question, payload.answer)


def _parse_manifest_json(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
