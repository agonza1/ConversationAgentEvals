import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, field_serializer, field_validator


SessionStatus = Literal['idle', 'presenting', 'paused', 'answering', 'ended']


class DeckRead(BaseModel):
    id: str
    title: str
    pdf_path: str
    status: str
    slide_count: int
    manifest_json: dict[str, Any] | list[Any] | str
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}

    @field_serializer('manifest_json', when_used='json')
    def serialize_manifest_json(self, value: dict[str, Any] | list[Any] | str):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value


class SlideRead(BaseModel):
    id: str
    deck_id: str
    index: int
    title: str
    image_url: str | None
    raw_text: str
    speaker_notes: str | None
    summary: str
    talk_track: str
    faq_json: list[str]


class SessionRead(BaseModel):
    id: str
    deck_id: str
    public_token: str
    status: SessionStatus
    current_slide_index: int
    started_at: datetime | None
    autoplay_enabled: bool
    autoplay_interval_seconds: int
    autoplay_started_at: datetime | None
    updated_at: datetime

    model_config = {'from_attributes': True}


class TranscriptEventRead(BaseModel):
    id: str
    session_id: str
    role: Literal['user', 'agent', 'system']
    text: str
    created_at: datetime

    model_config = {'from_attributes': True}


class PresentationEventRead(BaseModel):
    id: str
    session_id: str
    type: str
    payload_json: str
    created_at: datetime

    model_config = {'from_attributes': True}


class CreateSessionRequest(BaseModel):
    deck_id: str


class GotoSlideRequest(BaseModel):
    index: int


class AskRequest(BaseModel):
    question: str

    @field_validator('question')
    @classmethod
    def validate_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError('Question cannot be empty')
        return question


class AutoplayUpdateRequest(BaseModel):
    enabled: bool
    interval_seconds: int | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[dict[str, str | int]]
    session_status: SessionStatus


class AvatarSessionRead(BaseModel):
    provider: str
    enabled: bool
    session_id: str
    avatar_id: str | None
    voice_id: str | None
    public_token: str
    status: str
    note: str | None = None
    stream_url: str | None = None
    access_token: str | None = None
    ice_servers: list[dict] | None = None
    live_session: dict | None = None


class RealtimeClientRead(BaseModel):
    provider: str
    enabled: bool
    session_id: str
    public_token: str
    realtime_service_url: str
    model: str
    status: str


class SessionSnapshot(BaseModel):
    session: SessionRead
    deck: DeckRead
    slides: list[SlideRead]
    transcript: list[TranscriptEventRead]
    avatar: AvatarSessionRead | None = None
    realtime: RealtimeClientRead | None = None


class SessionLiveState(BaseModel):
    session: SessionRead
    current_slide: SlideRead | None
    transcript: list[TranscriptEventRead]
    recent_events: list[PresentationEventRead]
    upcoming_slides: list[SlideRead]
    progress: dict[str, int | bool]
