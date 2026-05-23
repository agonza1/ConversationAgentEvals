from __future__ import annotations

from app.config import settings


class HeyGenService:
    @property
    def enabled(self) -> bool:
        return bool(settings.heygen_live_avatar_api_key)

    def get_client_config(self) -> dict[str, object]:
        return {
            'provider': 'pipecat-heygen-transport',
            'enabled': self.enabled,
            'avatar_id': settings.heygen_effective_avatar_id,
            'sandbox': settings.heygen_sandbox,
        }


heygen_service = HeyGenService()
