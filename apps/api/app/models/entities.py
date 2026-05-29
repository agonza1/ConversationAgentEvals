from datetime import datetime
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint
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


class ProductProject(Base):
    __tablename__ = 'product_projects'
    __table_args__ = (UniqueConstraint('user_id', 'project_key', name='uq_product_projects_user_project_key'),)

    id = Column(String, primary_key=True, default=lambda: f'proj_{uuid.uuid4().hex[:12]}')
    user_id = Column(String, nullable=False, index=True)
    workspace_id = Column(String, ForeignKey('product_workspaces.id'), nullable=True, index=True)
    project_key = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False, default='Default Project')
    plan = Column(String, nullable=False, default='free')
    settings_json = Column(Text, nullable=False, default='{}')
    onboarding_json = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)

    workspace = relationship('ProductWorkspace', back_populates='projects')
    saved_runs = relationship('ProductSavedRun', back_populates='project', cascade='all, delete-orphan')


class ProductSavedRun(Base):
    __tablename__ = 'product_saved_runs'

    id = Column(String, primary_key=True, default=lambda: f'run_{uuid.uuid4().hex[:16]}')
    user_id = Column(String, nullable=False, index=True)
    project_id = Column(String, ForeignKey('product_projects.id'), nullable=False, index=True)
    plan = Column(String, nullable=False, default='free')
    report_json = Column(Text, nullable=False, default='{}')
    transcript = Column(Text, nullable=True)
    artifact_json = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    project = relationship('ProductProject', back_populates='saved_runs')


class BenchmarkRunRecord(Base):
    __tablename__ = 'benchmark_run_records'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    project_key = Column(String, nullable=False, index=True)
    suite_id = Column(String, nullable=False, index=True)
    scenario_id = Column(String, nullable=False, index=True)
    logical_run_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    report_json = Column(Text, nullable=False, default='{}')
    transcript = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    retained_until = Column(DateTime, nullable=True, index=True)


class ProductWorkspace(Base):
    __tablename__ = 'product_workspaces'
    __table_args__ = (UniqueConstraint('owner_user_id', 'workspace_key', name='uq_product_workspaces_owner_key'),)

    id = Column(String, primary_key=True, default=lambda: f'ws_{uuid.uuid4().hex[:12]}')
    owner_user_id = Column(String, nullable=False, index=True)
    workspace_key = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False, default='Default Workspace')
    plan = Column(String, nullable=False, default='free')
    settings_json = Column(Text, nullable=False, default='{}')
    onboarding_json = Column(Text, nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    projects = relationship('ProductProject', back_populates='workspace')
    members = relationship('ProductWorkspaceMember', back_populates='workspace', cascade='all, delete-orphan')
    invitations = relationship('ProductWorkspaceInvitation', back_populates='workspace', cascade='all, delete-orphan')


class ProductWorkspaceMember(Base):
    __tablename__ = 'product_workspace_members'
    __table_args__ = (UniqueConstraint('workspace_id', 'user_id', name='uq_workspace_members_workspace_user'),)

    id = Column(String, primary_key=True, default=lambda: f'wsm_{uuid.uuid4().hex[:12]}')
    workspace_id = Column(String, ForeignKey('product_workspaces.id'), nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False, default='viewer')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    workspace = relationship('ProductWorkspace', back_populates='members')


class ProductWorkspaceInvitation(Base):
    __tablename__ = 'product_workspace_invitations'

    id = Column(String, primary_key=True, default=lambda: f'inv_{uuid.uuid4().hex[:12]}')
    workspace_id = Column(String, ForeignKey('product_workspaces.id'), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False, default='viewer')
    status = Column(String, nullable=False, default='pending')
    invited_by_user_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    workspace = relationship('ProductWorkspace', back_populates='invitations')
