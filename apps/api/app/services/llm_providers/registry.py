from __future__ import annotations

from typing import Any

from app.services.llm_providers.openai_codex import OpenAICodexProvider


_PROVIDERS: dict[str, Any] = {
    'openai': OpenAICodexProvider(),
}


def get_provider(provider_id: str = 'openai') -> Any:
    key = (provider_id or 'openai').strip().lower()
    if key in {'openai', 'openai_codex', 'codex'}:
        key = 'openai'
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise ValueError(f'Unknown LLM auth provider: {provider_id}')
    return provider


def set_provider_for_tests(provider_id: str, provider: Any | None) -> None:
    key = (provider_id or 'openai').strip().lower()
    if key in {'openai', 'openai_codex', 'codex'}:
        key = 'openai'
    if provider is None:
        _PROVIDERS[key] = OpenAICodexProvider()
    else:
        _PROVIDERS[key] = provider


def list_providers() -> list[dict[str, Any]]:
    return [get_provider('openai').status()]
