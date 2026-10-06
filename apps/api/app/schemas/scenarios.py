from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    title: str | None = None
    simulated_user_prompt: str | None = None
    simulatedUserPrompt: str | None = None
    prompt: str | None = None
    expected_output: str | None = None
    expectedOutput: str | None = None
    description: str | None = None
    required_actions: list[str] | None = Field(default=None, max_length=100)
    forbidden_actions: list[str] | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def require_core_fields(self) -> 'ScenarioCreateRequest':
        prompt = (self.simulated_user_prompt or self.simulatedUserPrompt or self.prompt or '').strip()
        expected = (self.expected_output or self.expectedOutput or '').strip()
        description = (self.description or '').strip()
        if not prompt:
            raise ValueError('simulated_user_prompt is required')
        if not expected:
            raise ValueError('expected_output is required')
        if not description:
            raise ValueError('description is required')
        self.simulated_user_prompt = prompt
        self.expected_output = expected
        self.description = description
        if self.title is not None:
            self.title = self.title.strip() or None
        for key in ('required_actions', 'forbidden_actions'):
            values = getattr(self, key)
            if values is not None:
                if any(not item.strip() or len(item) > 2000 for item in values):
                    raise ValueError('Behavior labels must be non-blank and at most 2000 characters.')
                setattr(self, key, list(dict.fromkeys(item.strip() for item in values)))
        if self.required_actions == []:
            raise ValueError('Add at least one explicit required behavior.')
        return self


class ScenarioRecord(BaseModel):
    id: str
    suite_id: str
    title: str
    type: Literal['scenario'] = 'scenario'
    description: str
    simulated_user_prompt: str
    expected_output: str
    persona: str | None = None
    goal: str | None = None
    required_actions: list[Any] = Field(default_factory=list)
    forbidden_actions: list[Any] = Field(default_factory=list)
    expected_final_state: Any = None
    rubric: list[Any] = Field(default_factory=list)
    source: str = 'user_created'
    created_at: str | None = None
    updated_at: str | None = None
