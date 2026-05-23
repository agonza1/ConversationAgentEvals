from datetime import datetime
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql.sqltypes import String

from app.db.database import Base


class Deck(Base):
    __tablename__ = 'decks'

    id = Column(String, primary_key=True, default=lambda: f'deck_{uuid.uuid4().hex[:12]}')
    title = Column(String, nullable=False)
    pdf_path = Column(String, nullable=False)
    status = Column(String, nullable=False, default='uploaded')
    slide_count = Column(Integer, nullable=False, default=0)
    manifest_json = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    slides = relationship('Slide', back_populates='deck', cascade='all, delete-orphan')
    sessions = relationship('PresentationSession', back_populates='deck', cascade='all, delete-orphan')


class Slide(Base):
    __tablename__ = 'slides'

    id = Column(String, primary_key=True, default=lambda: f'slide_{uuid.uuid4().hex[:12]}')
    deck_id = Column(String, ForeignKey('decks.id'), nullable=False)
    index = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    image_path = Column(String, nullable=True)
    raw_text = Column(Text, nullable=False, default='')
    speaker_notes = Column(Text, nullable=True)
    summary = Column(Text, nullable=False, default='')
    talk_track = Column(Text, nullable=False, default='')
    faq_json = Column(Text, nullable=False, default='[]')
    embedding_ref = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    deck = relationship('Deck', back_populates='slides')


class PresentationSession(Base):
    __tablename__ = 'presentation_sessions'

    id = Column(String, primary_key=True, default=lambda: f'sess_{uuid.uuid4().hex[:12]}')
    deck_id = Column(String, ForeignKey('decks.id'), nullable=False)
    public_token = Column(String, nullable=False, unique=True, default=lambda: f'public_{uuid.uuid4().hex[:16]}')
    status = Column(String, nullable=False, default='idle')
    current_slide_index = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=True)
    autoplay_enabled = Column(Boolean, nullable=False, default=False)
    autoplay_interval_seconds = Column(Integer, nullable=False, default=8)
    autoplay_started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    deck = relationship('Deck', back_populates='sessions')
    transcript_events = relationship('TranscriptEvent', back_populates='session', cascade='all, delete-orphan')
    presentation_events = relationship('PresentationEvent', back_populates='session', cascade='all, delete-orphan')


class TranscriptEvent(Base):
    __tablename__ = 'transcript_events'

    id = Column(String, primary_key=True, default=lambda: f'trn_{uuid.uuid4().hex[:12]}')
    session_id = Column(String, ForeignKey('presentation_sessions.id'), nullable=False)
    role = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship('PresentationSession', back_populates='transcript_events')


class PresentationEvent(Base):
    __tablename__ = 'presentation_events'

    id = Column(String, primary_key=True, default=lambda: f'evt_{uuid.uuid4().hex[:12]}')
    session_id = Column(String, ForeignKey('presentation_sessions.id'), nullable=False)
    type = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship('PresentationSession', back_populates='presentation_events')
