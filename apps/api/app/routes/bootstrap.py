from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.realtime_bootstrap_service import realtime_bootstrap_service
from app.services.session_service import get_session_or_404

router = APIRouter(prefix='/api/bootstrap', tags=['bootstrap'])


@router.post('/sessions/{session_id}')
def bootstrap_session(session_id: str, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_id)
    return realtime_bootstrap_service.bootstrap(session_id=session.id, public_token=session.public_token)
