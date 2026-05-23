from __future__ import annotations

from typing import Any

from app.config import settings
from app.models.entities import PresentationSession, Slide


class PipecatService:
    def build_tool_manifest(self, *, session_id: str) -> list[dict[str, Any]]:
        return [
            {
                'name': 'get_current_slide',
                'description': 'Get the current presentation slide and its grounded content.',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/current-slide',
                'method': 'GET',
            },
            {
                'name': 'search_slides',
                'description': 'Search deck slides for relevant content by query.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string', 'description': 'Search query for slide content.'},
                    },
                    'required': ['query'],
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/search-slides?query={{query}}',
                'method': 'GET',
            },
            {
                'name': 'get_slide_content',
                'description': 'Fetch the full content for a specific slide index.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'slide_index': {'type': 'number', 'description': '0-based slide index.'},
                    },
                    'required': ['slide_index'],
                    'additionalProperties': False,
                },
                'endpoint': f'/api/realtime/sessions/{session_id}/slide-content/{{slide_index}}',
                'method': 'GET',
            },
            {
                'name': 'next_slide',
                'description': 'Advance the real presentation to the next slide. Call this before speaking about, presenting, or transitioning into the next slide.',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/next-slide',
                'method': 'POST',
            },
            {
                'name': 'prev_slide',
                'description': 'Go back to the previous slide.',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/prev-slide',
                'method': 'POST',
            },
            {
                'name': 'goto_slide',
                'description': 'Jump to a specific 0-based slide index.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'slide_index': {'type': 'number', 'description': '0-based slide index.'},
                    },
                    'required': ['slide_index'],
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/goto-slide',
                'method': 'POST',
            },
            {
                'name': 'restart_current_slide',
                'description': 'Repeat the current slide from the top.',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/restart-current-slide',
                'method': 'POST',
            },
            {
                'name': 'pause_presentation',
                'description': 'Pause the presentation.',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/pause',
                'method': 'POST',
            },
            {
                'name': 'resume_presentation',
                'description': 'Resume the presentation.',
                'parameters': {
                    'type': 'object',
                    'properties': {},
                    'additionalProperties': False,
                },
                'endpoint': f'/api/sessions/{session_id}/resume',
                'method': 'POST',
            },
        ]

    def build_session_plan(self, *, session_id: str, public_token: str, contract: dict[str, Any]) -> dict[str, Any]:
        return {
            'orchestrator': 'pipecat',
            'session_id': session_id,
            'public_token': public_token,
            'transport': {
                'provider': 'openai-realtime',
                'model': settings.openai_realtime_model,
                'requires_api_key': True,
            },
            'backend_tools': contract.get('tools', {}),
            'tool_manifest': contract.get('tool_manifest', self.build_tool_manifest(session_id=session_id)),
            'state_authority': 'fastapi',
            'loop': [
                'bootstrap realtime session',
                'hydrate presentation context',
                'present current slide',
                'accept interruption via voice',
                'ground answer on current/retrieved slide context',
                'resume deterministic presentation state',
            ],
        }

    def build_realtime_instructions(self, *, session: PresentationSession, current_slide: Slide | None, manifest: dict[str, Any] | list[Any] | str | None) -> str:
        slide_lines: list[str] = []
        ordered_slides = sorted(session.deck.slides, key=lambda item: item.index)[:8]
        for slide in ordered_slides:
            slide_lines.append(
                f"- Slide {slide.index + 1}: {slide.title}. Summary: {slide.summary}. Talk track: {slide.talk_track}"
            )

        current_slide_block = 'No current slide is active yet.'
        if current_slide:
            current_slide_block = (
                f"Current slide is {current_slide.index + 1}: {current_slide.title}. "
                f"Summary: {current_slide.summary}. Talk track: {current_slide.talk_track}."
            )

        manifest_hint = ''
        if isinstance(manifest, dict):
            manifest_hint = f"Deck manifest title: {manifest.get('title') or session.deck.title}."

        return (
            '# Role & Objective\n'
            'You are the live voice presenter for a slide-based sales presentation. Your goal is to deliver the deck clearly, answer audience questions, and keep the real presentation session synchronized.\n\n'
            '# Personality & Tone\n'
            '- Speak naturally, confidently, and concisely.\n'
            '- Sound like a capable sales engineer, not a narrator reading notes.\n'
            '- Use conversational variety; do not repeat the same transition phrases.\n'
            '- Keep answers short enough for spoken delivery unless the operator explicitly asks for more detail.\n\n'
            '# Context\n'
            f'- Deck title: {session.deck.title}.\n'
            f'- Presentation status: {session.status}.\n'
            f'- {manifest_hint}\n'
            f'- {current_slide_block}\n\n'
            'Known slide outline:\n'
            + '\n'.join(slide_lines)
            + '\n\n'
            + '# Grounding Rules\n'
            + '- Stay grounded in the presentation content.\n'
            + '- When answering, prioritize the current slide first, then nearby slide context.\n'
            + '- If the answer is not in the deck, say so briefly; do not invent facts.\n'
            + '- If useful, bridge back to the current slide or sales narrative.\n\n'
            + '# Tools\n'
            + '- Use available presentation tools whenever needed to stay synchronized with the real session state.\n'
            + '- Use the matching presentation tool when the audience asks to continue, move on, go back, pause, resume, or repeat a slide.\n'
            + '- Critical sync rule: never talk through or present the next slide while the UI is still showing the current slide. Before discussing the next slide, call next_slide or goto_slide first, wait for the tool result, then present that visible slide.\n'
            + '- When you decide on your own that the current slide is complete, advance exactly one slide with next_slide before starting the next slide talk track.\n'
            + '- Do not mention internal tools, APIs, system wiring, or implementation details.\n\n'
            + '# Conversation Flow\n'
            + '- Present exactly one visible slide at a time: say the current slide title, give its concise talk track, then pause for questions or transition.\n'
            + '- Accept interruptions and answer audience questions directly.\n'
            + '- After answering a question about the current material, prefer to resume the presentation flow instead of stalling.\n'
            + '- Use a clear transition like "I’ll move to the next slide" only immediately before calling next_slide; after the tool result, continue with the new slide.\n'
            + '- If the audience asks an off-deck question, answer carefully and tie back to the presentation when possible.\n\n'
            + '# Safety & Recovery\n'
            + '- If audio is unintelligible, ask for a brief repeat instead of guessing.\n'
            + '- If a tool action fails or the session state seems inconsistent, continue conversationally and ask the operator to retry the action.'
        )


pipecat_service = PipecatService()
