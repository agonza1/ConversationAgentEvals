"""Caller TTS shared by external-target adapters; keys never leave the worker."""
from typing import Any

import httpx

from speech_configuration import SpeechSelection, deployment_speech_selection, selection_requirements
from synthesized_audio import finalize_tts_wav


def tester_selection(speech: dict[str, Any] | None = None) -> SpeechSelection:
    return SpeechSelection.model_validate(speech) if speech is not None else deployment_speech_selection()


def tester_tts_issues(selection: SpeechSelection, kokoro_url: str) -> list[str]:
    # External target capture stays on rtc-asr regardless of the built-in STT setting.
    return selection_requirements(selection, rtc_asr_url='', kokoro_url=kokoro_url)['tts']['issues']


def tester_tts_request(selection: SpeechSelection, text: str, *, kokoro_url: str,
                       voice: str | None = None, speed: float = 1.0) -> dict[str, Any]:
    issues = tester_tts_issues(selection, kokoro_url)
    if issues:
        raise ValueError(' '.join(issues))
    voice = voice or selection.tester_voice
    if selection.tts_provider == 'kokoro':
        return {'url': f'{kokoro_url.rstrip("/")}/v1/audio/speech',
                'json': {'model': selection.tts_model, 'voice': voice, 'input': text,
                         'speed': speed, 'response_format': 'wav'}}
    # Use exactly the same adapter as the built-in streaming caller.
    from pipecat.frames.frames import TextFrame
    from speech_providers import StreamingCloudTtsProcessor
    processor = StreamingCloudTtsProcessor(selection=selection, voice=voice,
        input_type=TextFrame, participant='tester')
    return processor._speech_request(text)


async def synthesize_tester(text: str, selection: SpeechSelection, *, kokoro_url: str,
                            voice: str | None = None, client: httpx.AsyncClient | None = None) -> bytes:
    request = tester_tts_request(selection, text, kokoro_url=kokoro_url, voice=voice)
    async def fetch(active: httpx.AsyncClient) -> bytes:
        try:
            response = await active.post(**request)
            response.raise_for_status()
            if not response.content:
                raise ValueError('empty audio')
            return finalize_tts_wav(response.content)
        except Exception:
            # Provider errors can contain authorization headers or remote account details.
            raise RuntimeError(f'{selection.tts_provider} tester speech synthesis failed; check provider readiness.') from None
    if client is not None:
        return await fetch(client)
    async with httpx.AsyncClient(timeout=60) as active:
        return await fetch(active)
