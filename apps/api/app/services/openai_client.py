from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class OpenAIClient:
    def __init__(self) -> None:
        self.base_url = 'https://api.openai.com/v1'
        self.realtime_base_url = 'https://api.openai.com/v1/realtime'

    @property
    def enabled(self) -> bool:
        return bool(settings.openai_api_key)

    def _headers(self) -> dict[str, str]:
        if not settings.openai_api_key:
            raise RuntimeError('OPENAI_API_KEY is missing')
        return {
            'Authorization': f'Bearer {settings.openai_api_key}',
            'Content-Type': 'application/json',
        }

    def create_embedding(self, text: str) -> list[float]:
        response = httpx.post(
            f'{self.base_url}/embeddings',
            headers=self._headers(),
            json={
                'model': 'text-embedding-3-small',
                'input': text,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        return payload['data'][0]['embedding']

    def create_response(self, payload: list[dict[str, Any]]) -> dict[str, Any]:
        response = httpx.post(
            f'{self.base_url}/responses',
            headers=self._headers(),
            json={
                'model': settings.openai_responses_model,
                'input': payload,
                'temperature': 0.2,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def create_realtime_session(self, *, instructions: str, voice: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            'model': settings.openai_realtime_model,
            'modalities': ['audio', 'text'],
            'instructions': instructions,
            'voice': voice or 'alloy',
        }

        response = httpx.post(
            f'{self.base_url}/realtime/sessions',
            headers=self._headers(),
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


openai_client = OpenAIClient()
