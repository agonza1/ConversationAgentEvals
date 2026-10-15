from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    title: str | None = None
    simulated_user_prompt: str | None = None
    simulatedUserPrompt: str | None = None
    prompt: str | None = None
    spoken_opener: str | None = Field(default=None, max_length=4000)
    expected_output: str | None = None
    expectedOutput: str | None = None
    description: str | None = None
    required_actions: list[str] | None = Field(default=None, max_length=100)
    forbidden_actions: list[str] | None = Field(default=None, max_length=100)
    evidence_requirements: list[Literal['transcript', 'action_trace', 'final_state', 'vcon', 'final_state_or_action_trace']] | None = Field(default=None, max_length=5)
    requirements: str | None = Field(default=None, max_length=30000)
    permissible_behavior: str | None = Field(default=None, max_length=10000)
    caller_steps: list[str] | None = Field(default=None, min_length=1, max_length=8)

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
        if self.spoken_opener is not None:
            self.spoken_opener = self.spoken_opener.strip()
            if not self.spoken_opener:
                raise ValueError('spoken_opener must be non-blank when supplied')
        if self.title is not None:
            self.title = self.title.strip() or None
        if self.caller_steps is not None:
            if any(not step.strip() or len(step) > 2000 for step in self.caller_steps):
                raise ValueError('Caller steps must be non-blank and at most 2000 characters.')
            self.caller_steps = [step.strip() for step in self.caller_steps]
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
    spoken_opener: str | None = None
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
