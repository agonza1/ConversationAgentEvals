from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import fitz
from sqlalchemy.orm import Session

from app.models.entities import Deck, Slide


def preprocess_deck(db: Session, deck: Deck) -> Deck:
    pdf_path = Path(deck.pdf_path)
    deck_dir = pdf_path.parent
    slides_dir = deck_dir / 'slides'
    slides_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    deck.status = 'processing'
    db.add(deck)
    db.flush()

    existing_slides = list(deck.slides)
    for slide in existing_slides:
        db.delete(slide)
    db.flush()

    manifest_slides: list[dict[str, object]] = []

    for index, page in enumerate(doc):
        image_path = slides_dir / f'{index}.png'
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        pixmap.save(image_path)

        raw_text = page.get_text('text').strip()
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        title = lines[0][:120] if lines else f'Slide {index + 1}'
        summary = ' '.join(lines[:4])[:500] if lines else f'Auto-generated summary placeholder for slide {index + 1}.'
        talk_track = (
            f"On slide {index + 1}, emphasize: {summary}" if summary else f'Present slide {index + 1} clearly and concisely.'
        )
        faq_json = json.dumps([
            f'What matters most on slide {index + 1}?',
            f'How should we interpret the message on slide {index + 1}?',
        ])

        manifest_slides.append({
            'index': index,
            'title': title,
            'summary': summary,
            'image_url': str(image_path),
            'top_terms': [term for term, _ in Counter(word.strip('.,:;!?()[]{}').lower() for word in raw_text.split() if len(word.strip('.,:;!?()[]{}')) > 3).most_common(8)],
        })

        db.add(
            Slide(
                deck_id=deck.id,
                index=index,
                title=title,
                image_path=str(image_path),
                raw_text=raw_text,
                speaker_notes=None,
                summary=summary,
                talk_track=talk_track,
                faq_json=faq_json,
            )
        )

    deck.slide_count = len(doc)
    deck.manifest_json = json.dumps({
        'deck_id': deck.id,
        'title': deck.title,
        'slide_count': len(doc),
        'slides': manifest_slides,
    })
    deck.status = 'ready'
    db.add(deck)
    db.commit()
    db.refresh(deck)
    return deck
