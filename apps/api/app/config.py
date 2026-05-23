from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / '.env')


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv('APP_ENV', 'development')
    api_base_url: str = os.getenv('API_BASE_URL', 'http://localhost:8000')
    openai_api_key: str | None = os.getenv('OPENAI_API_KEY')
    openai_realtime_model: str = os.getenv('OPENAI_REALTIME_MODEL', 'gpt-realtime-mini')
    openai_responses_model: str = os.getenv('OPENAI_RESPONSES_MODEL', 'gpt-4.1-mini')
    pipecat_service_url: str = os.getenv('PIPECAT_SERVICE_URL', 'http://localhost:8110')
    heygen_live_avatar_api_key: str | None = os.getenv('HEYGEN_LIVE_AVATAR_API_KEY') or os.getenv('HEYGEN_API_KEY')
    heygen_avatar_id: str = os.getenv('HEYGEN_AVATAR_ID', 'dd73ea75-1218-4ef3-92ce-606d5f7fbc0a')
    heygen_sandbox: bool = os.getenv('HEYGEN_SANDBOX', 'true').lower() == 'true'
    heygen_sandbox_avatar_id: str = os.getenv('HEYGEN_SANDBOX_AVATAR_ID', 'dd73ea75-1218-4ef3-92ce-606d5f7fbc0a')

    @property
    def heygen_effective_avatar_id(self) -> str:
        return self.heygen_sandbox_avatar_id if self.heygen_sandbox else self.heygen_avatar_id


settings = Settings()
