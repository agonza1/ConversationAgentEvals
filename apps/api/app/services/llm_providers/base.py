from __future__ import annotations

from typing import Any, Protocol


class LlmAuthProvider(Protocol):
    """Auth + completion contract for local LLM judge providers.

    OpenAI Codex OAuth ships first. Claude Code OAuth can implement the same
    surface later without changing product routes.
    """

    provider_id: str

    def status(self) -> dict[str, Any]:
        """Return connection status payload for UI/API."""

    def start_oauth(self) -> dict[str, Any]:
        """Begin OAuth (PKCE). Return authorize_url and redirect_uri."""

    def disconnect(self) -> dict[str, Any]:
        """Clear stored credentials."""

    def ensure_access_token(self) -> str:
        """Refresh if needed and return a usable access token."""

    def complete(self, prompt: str) -> str:
        """Run a one-shot completion against the provider backend."""
