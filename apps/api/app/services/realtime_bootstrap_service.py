from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.services.realtime_service import realtime_service


class RealtimeBootstrapService:
    def _get_realtime_service_url(self) -> str | None:
        return self._get_pipecat_service_url()

    def bootstrap(self, *, session_id: str, public_token: str) -> dict[str, Any]:
        realtime = realtime_service.create_local_or_live_bootstrap(session_id=session_id, public_token=public_token)

        local_plan = self._build_local_plan(session_id=session_id, public_token=public_token, realtime=realtime)
        realtime_service_url = self._get_pipecat_service_url()
        if not realtime_service_url:
            return {
                'status': 'scaffolded',
                'reason': 'Realtime bootstrap service not configured',
                'avatar': None,
                'realtime': realtime,
                'avatar_live_ready': False,
                'pipecatPlan': local_plan,
                'nextStep': local_plan['next_step'],
            }

        try:
            response = httpx.post(
                f"{realtime_service_url.rstrip('/')}/sessions/{session_id}/bootstrap",
                json={'publicToken': public_token},
                timeout=30.0,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {
                'status': 'partial',
                'reason': f'realtime bootstrap unavailable: {exc}',
                'avatar': None,
                'realtime': realtime,
                'avatar_live_ready': False,
                'pipecatPlan': local_plan,
                'nextStep': local_plan['next_step'],
            }

        bootstrap_realtime = payload.get('realtime') if isinstance(payload.get('realtime'), dict) else {}

        payload['avatar'] = None
        payload['realtime'] = {**bootstrap_realtime, **realtime}
        payload['avatar_live_ready'] = False
        if 'voice' not in payload:
            payload['voice'] = {
                'status': 'idle',
                'mode': 'pipecat-orchestrated',
                'start_endpoint': f'/sessions/{session_id}/connect',
                'ask_endpoint': f'/sessions/{session_id}/ask',
                'stop_endpoint': f'/sessions/{session_id}/disconnect',
            }

        existing_plan = payload.get('pipecatPlan') if isinstance(payload.get('pipecatPlan'), dict) else {}
        payload['pipecatPlan'] = {**local_plan, **existing_plan}
        if not payload['pipecatPlan'].get('steps'):
            payload['pipecatPlan']['steps'] = local_plan['steps']
        payload['nextStep'] = payload.get('nextStep') or local_plan['next_step']
        return payload

    def _build_local_plan(self, *, session_id: str, public_token: str, realtime: dict[str, Any]) -> dict[str, Any]:
        realtime_ready = bool(realtime.get('enabled'))
        return {
            'orchestrator': 'pipecat',
            'transport': 'pipecat',
            'session_id': session_id,
            'public_token': public_token,
            'realtime_service_url': self._get_pipecat_service_url(),
            'realtime_ready': realtime_ready,
            'steps': [
                'Load presentation contract from FastAPI source-of-truth state',
                'Connect frontend client to the server-side Pipecat transport',
                'Use backend slide/session tools for grounded answers and navigation',
                'Keep the demo audio-only: Pipecat/OpenAI Realtime drives voice while FastAPI owns deck state',
            ],
            'next_step': (
                'Connect the Pipecat bootstrap endpoint to a live transport session.'
                if realtime_ready
                else 'Add OpenAI Realtime credentials so the Pipecat transport can initialize.'
            ),
        }

    def _get_pipecat_service_url(self) -> str | None:
        value = (settings.pipecat_service_url or '').strip()
        if not value:
            return None
        default_local_urls = {
            'http://localhost:8110',
            'http://127.0.0.1:8110',
        }
        return None if value in default_local_urls else value


realtime_bootstrap_service = RealtimeBootstrapService()
