from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class EvalRunRequest(BaseModel):
    conversation: str | dict[str, Any] | list[Any] = Field(...)
    criteria: str = Field(..., min_length=1)
    title: str | None = None

    @field_validator('conversation')
    @classmethod
    def conversation_must_not_be_blank(cls, value: str | dict[str, Any] | list[Any]) -> str | dict[str, Any] | list[Any]:
        if isinstance(value, str) and not value.strip():
            raise ValueError('Conversation cannot be empty')
        if isinstance(value, (dict, list)) and not value:
            raise ValueError('Conversation cannot be empty')
        return value

    @field_validator('criteria')
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('Value cannot be empty')
        return cleaned


class EvalCheck(BaseModel):
    name: str
    status: str
    score: int
    layer: str
    root_cause_tag: str
    evidence: list[str]
    reason: str


class EvalRunResponse(BaseModel):
    title: str
    source_format: str
    overall_score: int
    verdict: str
    checks: list[EvalCheck]
    risk_flags: list[str]
    suggested_fixes: list[str]
    transcript_preview: str
    vcon_analysis: dict[str, Any]
    vcon_export: dict[str, Any]
