"""Database-backed deduplication. Claim only AFTER source/project authorization.

A running request is never automatically stolen: an interrupted provider call
may still be billable. Operators can inspect it and explicitly request a new
sample. Failed requests are retryable; successful provider outputs are retained
before file-backed review/audit finalization, so retries do not pay twice.
"""
from __future__ import annotations

import json
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.entities import AssertJudgeRequestRecord
from app.services.evaluation_contract import content_hash


class JudgeRequestConflict(ValueError):
    pass


def claim(db: Session, *, scope: dict, fingerprint: str, request_id: str | None) -> tuple[str, dict | None]:
    key = content_hash({'scope': scope, 'request': request_id or f'canonical:{fingerprint}'})
    try:
        db.add(AssertJudgeRequestRecord(id=key, input_fingerprint=fingerprint, status='running'))
        db.commit()
        return key, None
    except IntegrityError:
        db.rollback()
    record = db.get(AssertJudgeRequestRecord, key)
    if record is None:
        raise JudgeRequestConflict('The judge request changed concurrently; retry.')
    if record.input_fingerprint != fingerprint:
        raise JudgeRequestConflict('This request ID belongs to different evidence or grader settings. Use a new ID for an explicit re-evaluation.')
    if record.status in {'judged', 'completed'} and record.response_json:
        return key, json.loads(record.response_json)
    if record.status == 'failed':
        updated = db.query(AssertJudgeRequestRecord).filter_by(id=key, status='failed').update({'status': 'running'})
        db.commit()
        if updated:
            return key, None
    raise JudgeRequestConflict('This semantic review is already running. Retry the same request; no duplicate model call was started.')


def retain(db: Session, key: str, response: dict, *, completed: bool = False) -> None:
    record = db.get(AssertJudgeRequestRecord, key)
    if record is None:
        raise JudgeRequestConflict('The claimed semantic review is unavailable.')
    record.response_json = json.dumps(response, ensure_ascii=False, allow_nan=False)
    record.status = 'completed' if completed else 'judged'
    db.commit()


def failed(db: Session, key: str) -> None:
    db.rollback()
    db.query(AssertJudgeRequestRecord).filter_by(id=key, status='running').update({'status': 'failed'})
    db.commit()
