from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.entities import Deck, PresentationSession
from app.services.openai_client import openai_client
from app.services.pipecat_service import pipecat_service


class RealtimeService:
    def get_client_config(self, *, session_id: str, public_token: str) -> dict[str, Any]:
        provider_enabled = bool(settings.openai_api_key)
        pipecat_configured = bool((settings.pipecat_service_url or '').strip())

        if provider_enabled and pipecat_configured:
            status = 'configured'
        elif provider_enabled:
            status = 'needs_pipecat'
        else:
            status = 'needs_config'

        return {
            'provider': 'pipecat',
            'enabled': provider_enabled,
            'session_id': session_id,
            'public_token': public_token,
            'realtime_service_url': settings.pipecat_service_url,
            'pipecat_service_url': settings.pipecat_service_url,
            'model': settings.openai_realtime_model,
            'status': status,
            'bridge_configured': pipecat_configured,
            'browser_direct_supported': False,
        }

    def create_local_or_live_bootstrap(self, *, session_id: str, public_token: str) -> dict[str, Any]:
        base = self.get_client_config(session_id=session_id, public_token=public_token)
        if not openai_client.enabled:
            base['session'] = None
            return base

        return {
            **base,
            'session': None,
            'next_step': (
                'Start the Pipecat transport so the orchestrated session can initialize.'
                if openai_client.enabled
                else 'Add OpenAI Realtime credentials so live voice can initialize.'
            ),
        }

    def create_pipecat_bootstrap(self, *, session: PresentationSession) -> dict[str, Any]:
        current_slide = next((slide for slide in session.deck.slides if slide.index == session.current_slide_index), None)
        instructions = pipecat_service.build_realtime_instructions(
            session=session,
            current_slide=current_slide,
            manifest=self._parse_manifest(session.deck.manifest_json),
        )
        base = self.get_client_config(session_id=session.id, public_token=session.public_token)
        contract = {
            'tools': {
                'get_current_slide': f'/api/sessions/{session.id}/current-slide',
                'search_slides': f'/api/sessions/{session.id}/search-slides?query={{query}}',
                'next_slide': f'/api/sessions/{session.id}/next-slide',
                'prev_slide': f'/api/sessions/{session.id}/prev-slide',
                'goto_slide': f'/api/sessions/{session.id}/goto-slide',
                'restart_current_slide': f'/api/sessions/{session.id}/restart-current-slide',
                'pause_presentation': f'/api/sessions/{session.id}/pause',
                'resume_presentation': f'/api/sessions/{session.id}/resume',
                'get_slide_content': f'/api/realtime/sessions/{session.id}/slide-content/{{slide_index}}',
            },
            'tool_manifest': pipecat_service.build_tool_manifest(session_id=session.id),
        }
        base['instructions'] = instructions
        base['tool_manifest'] = contract['tool_manifest']
        base['pipecat_plan'] = pipecat_service.build_session_plan(
            session_id=session.id,
            public_token=session.public_token,
            contract=contract,
        )
        base['current_slide_index'] = session.current_slide_index
        base['slide_count'] = len(session.deck.slides)

        if not openai_client.enabled:
            base['status'] = 'needs_config'
            base['session'] = None
            return base

        if not base.get('bridge_configured'):
            base['status'] = 'configured'
            base['session'] = None
            base['next_step'] = 'Start the Pipecat orchestration service and connect the presentation through that transport.'
            return base

        base['status'] = 'live_ready'
        base['session'] = None
        base['next_step'] = 'Use the Pipecat service bootstrap/connect flow for media transport.'
        return base

    def get_session_for_bootstrap(self, db: Session, session_id: str) -> PresentationSession | None:
        return (
            db.query(PresentationSession)
            .options(joinedload(PresentationSession.deck).joinedload(Deck.slides))
            .filter(PresentationSession.id == session_id)
            .first()
        )

    def _parse_manifest(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        return value


realtime_service = RealtimeService()
