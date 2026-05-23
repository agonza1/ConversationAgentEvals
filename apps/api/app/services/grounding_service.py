from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.models.entities import Deck, Slide
from app.services.openai_client import openai_client

SYSTEM_PROMPT = (
    'You are a slide-aware AI sales presenter. Answer using the provided deck context first. '
    'Prefer the current slide, then nearby slides, then overall deck context. '
    'If the deck does not support a claim, say so briefly and avoid hallucinating.'
)


@dataclass
class GroundedAnswer:
    answer: str
    citations: list[dict[str, Any]]


class GroundingService:
    def answer_question(self, *, deck: Deck, current_slide: Slide | None, related_slides: list[Slide], question: str) -> GroundedAnswer:
        if not settings.openai_api_key:
            return self._fallback_answer(current_slide=current_slide, related_slides=related_slides, question=question)

        payload = self._build_payload(
            deck=deck,
            current_slide=current_slide,
            related_slides=related_slides,
            question=question,
        )
        try:
            data = openai_client.create_response(payload)
            answer = self._extract_output_text(data).strip()
            if not answer:
                return self._fallback_answer(current_slide=current_slide, related_slides=related_slides, question=question)
            return GroundedAnswer(answer=answer, citations=self._build_citations(current_slide, related_slides))
        except Exception:
            return self._fallback_answer(current_slide=current_slide, related_slides=related_slides, question=question)

    def _build_payload(
        self,
        *,
        deck: Deck,
        current_slide: Slide | None,
        related_slides: list[Slide],
        question: str,
    ) -> list[dict[str, str]]:
        deck_summary = '\n'.join(
            f"Slide {slide.index + 1}: {slide.title} — {slide.summary}" for slide in sorted(deck.slides, key=lambda item: item.index)
        )
        current_block = self._slide_block('current', current_slide) if current_slide else 'No current slide context available.'
        nearby_block = '\n\n'.join(self._slide_block('related', slide) for slide in related_slides[:3]) or 'No related slides found.'
        return [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {
                'role': 'user',
                'content': (
                    f'Deck title: {deck.title}\n\n'
                    f'Current slide context:\n{current_block}\n\n'
                    f'Nearby slide context:\n{nearby_block}\n\n'
                    f'Deck summary:\n{deck_summary}\n\n'
                    f'Question: {question}\n\n'
                    'Answer concisely for a live presentation. Mention when the deck does not fully support the answer.'
                ),
            },
        ]

    def _slide_block(self, label: str, slide: Slide | None) -> str:
        if not slide:
            return f'{label}: unavailable'
        faq = json.loads(slide.faq_json or '[]')
        return (
            f'{label} slide {slide.index + 1}: {slide.title}\n'
            f'Summary: {slide.summary}\n'
            f'Talk track: {slide.talk_track}\n'
            f'Raw text: {slide.raw_text[:1800]}\n'
            f'FAQs: {faq}'
        )

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get('output_text'), str):
            return payload['output_text']
        parts: list[str] = []
        for item in payload.get('output', []):
            for content in item.get('content', []):
                text = content.get('text')
                if text:
                    parts.append(text)
        return '\n'.join(parts)

    def _build_citations(self, current_slide: Slide | None, related_slides: list[Slide]) -> list[dict[str, Any]]:
        seen: set[int] = set()
        citations: list[dict[str, Any]] = []
        for slide, reason in [(current_slide, 'current slide context')] + [(slide, 'nearby slide context') for slide in related_slides[:3]]:
            if slide and slide.index not in seen:
                seen.add(slide.index)
                citations.append({'slide_index': slide.index, 'reason': reason})
        return citations or [{'slide_index': 0, 'reason': 'deck context'}]

    def _fallback_answer(self, *, current_slide: Slide | None, related_slides: list[Slide], question: str) -> GroundedAnswer:
        lead = None
        if current_slide and current_slide.summary:
            lead = current_slide.summary.strip()
        elif related_slides:
            lead = related_slides[0].summary.strip()

        answer_parts: list[str] = []
        if lead:
            answer_parts.append(lead)
        else:
            answer_parts.append('The deck does not provide enough detail to answer that confidently.')

        supporting_points = [
            slide.summary.strip()
            for slide in related_slides[:2]
            if slide.summary and slide.summary.strip() and slide.summary.strip() != lead
        ]
        if supporting_points:
            answer_parts.append('Supporting context: ' + ' '.join(supporting_points))

        answer_parts.append('I’m answering from the deck content shown here, so I’d keep any stronger claim qualified in the live demo.')
        return GroundedAnswer(answer=' '.join(answer_parts), citations=self._build_citations(current_slide, related_slides))


grounding_service = GroundingService()
