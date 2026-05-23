from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.entities import Deck
from app.preprocessing.pdf_pipeline import preprocess_deck
from app.schemas.entities import DeckRead
from app.services.session_service import _normalize_image_url

router = APIRouter(prefix='/api/decks', tags=['decks'])

APP_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = APP_ROOT.parent
LEGACY_ROOT = REPO_ROOT / 'apps' / 'api'
STORAGE_ROOT = REPO_ROOT / 'storage' / 'decks'
DEFAULTS_ROOT = REPO_ROOT / 'storage' / 'defaults'
DEFAULT_DECK_NAME = 'ABC_Real_Estate_Investment_Demo.pdf'


def _resolve_default_deck_path() -> Path | None:
    candidates = [
        DEFAULTS_ROOT / DEFAULT_DECK_NAME,
        REPO_ROOT / 'storage' / DEFAULT_DECK_NAME,
        LEGACY_ROOT / 'storage' / 'defaults' / DEFAULT_DECK_NAME,
        LEGACY_ROOT / 'storage' / DEFAULT_DECK_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@router.post('', response_model=DeckRead)
def upload_deck(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename:
        raise HTTPException(status_code=400, detail='No file received. Choose a PDF deck and try again.')

    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='Only PDF decks are supported right now.')

    deck = Deck(title=Path(file.filename or 'Untitled Deck').stem, pdf_path='')
    db.add(deck)
    db.flush()

    deck_dir = STORAGE_ROOT / deck.id
    deck_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = deck_dir / 'source.pdf'
    with pdf_path.open('wb') as buffer:
        shutil.copyfileobj(file.file, buffer)

    deck.pdf_path = str(pdf_path)
    deck.status = 'uploaded'
    db.add(deck)
    db.commit()
    db.refresh(deck)

    return preprocess_deck(db, deck)


@router.post('/use-default', response_model=DeckRead)
def use_default_deck(db: Session = Depends(get_db)):
    pdf_path = _resolve_default_deck_path()
    if not pdf_path:
        raise HTTPException(status_code=404, detail='Default demo deck not found in storage/defaults.')

    deck = Deck(title=pdf_path.stem, pdf_path='')
    db.add(deck)
    db.flush()

    deck_dir = STORAGE_ROOT / deck.id
    deck_dir.mkdir(parents=True, exist_ok=True)
    copied_path = deck_dir / f'{uuid.uuid4().hex}.pdf'
    shutil.copy2(pdf_path, copied_path)

    deck.pdf_path = str(copied_path)
    deck.status = 'uploaded'
    db.add(deck)
    db.commit()
    db.refresh(deck)

    return preprocess_deck(db, deck)


@router.get('/default-meta')
def get_default_deck_meta():
    pdf_path = _resolve_default_deck_path()
    return {
        'available': bool(pdf_path),
        'name': DEFAULT_DECK_NAME,
    }


@router.get('/{deck_id}', response_model=DeckRead)
def get_deck(deck_id: str, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail='Deck not found.')
    return deck


@router.get('/{deck_id}/slides')
def get_deck_slides(deck_id: str, db: Session = Depends(get_db)):
    deck = db.query(Deck).filter(Deck.id == deck_id).first()
    if not deck:
        raise HTTPException(status_code=404, detail='Deck not found.')
    return {
        'deck_id': deck_id,
        'slides': [
            {
                'id': slide.id,
                'index': slide.index,
                'title': slide.title,
                'summary': slide.summary,
                'image_url': _normalize_image_url(slide.image_path),
            }
            for slide in sorted(deck.slides, key=lambda item: item.index)
        ],
    }
