"""Non-secret speech selection shared by the API and Pipecat runtime.

The Pipecat Dockerfile copies this module; credentials are read only by the
media worker. Console settings never contain credentials or arbitrary endpoints.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpeechSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    stt_provider: Literal['rtc-asr', 'deepgram', 'cartesia'] = 'rtc-asr'
    tts_provider: Literal['kokoro', 'deepgram', 'cartesia'] = 'kokoro'
    stt_model: str = Field(default='service-selected', min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:-]+$')
    tts_model: str = Field(default='kokoro', min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:-]+$')
    tester_voice: str = Field(default='af_heart', min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:-]+$')
    target_voice: str = Field(default='af_bella', min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:-]+$')
    language: str = Field(default='en', min_length=2, max_length=20, pattern=r'^[A-Za-z-]+$')
    cartesia_version: str = Field(default='2026-03-01', pattern=r'^\d{4}-\d{2}-\d{2}$')

    @model_validator(mode='after')
    def distinct_voices(self) -> 'SpeechSelection':
        if self.tester_voice == self.target_voice:
            raise ValueError('Choose different caller and agent voices.')
        if self.tts_provider == 'deepgram' and any(
            not voice.startswith('aura-') for voice in (self.tester_voice, self.target_voice)
        ):
            raise ValueError('Deepgram voice IDs must be Aura model IDs (aura-...).')
        return self


def deployment_speech_selection() -> SpeechSelection:
    stt = os.getenv('CAE_STT_PROVIDER', 'rtc-asr').strip()
    tts = os.getenv('CAE_TTS_PROVIDER', 'kokoro').strip()
    voices = {
        'kokoro': (os.getenv('KOKORO_TESTER_VOICE', 'af_heart'), os.getenv('KOKORO_TARGET_VOICE', 'af_bella')),
        'deepgram': (os.getenv('DEEPGRAM_TESTER_VOICE', 'aura-2-asteria-en'), os.getenv('DEEPGRAM_TARGET_VOICE', 'aura-2-orion-en')),
        # Voice IDs depend on the account's voice library: require explicit IDs.
        'cartesia': (os.getenv('CARTESIA_TESTER_VOICE') or 'configure-caller-voice', os.getenv('CARTESIA_TARGET_VOICE') or 'configure-agent-voice'),
    }
    caller, agent = voices.get(tts, voices['kokoro'])
    return SpeechSelection(
        stt_provider=stt, tts_provider=tts,
        stt_model={'rtc-asr': 'service-selected', 'deepgram': os.getenv('DEEPGRAM_STT_MODEL', 'nova-3'), 'cartesia': os.getenv('CARTESIA_STT_MODEL', 'ink-2')}.get(stt, 'service-selected'),
        tts_model={'kokoro': os.getenv('KOKORO_MODEL', 'kokoro'), 'deepgram': 'aura-2', 'cartesia': os.getenv('CARTESIA_TTS_MODEL', 'sonic-3.6')}.get(tts, 'kokoro'),
        tester_voice=caller, target_voice=agent,
        language=os.getenv('CAE_SPEECH_LANGUAGE', 'en'),
        cartesia_version=os.getenv('CARTESIA_API_VERSION', '2026-03-01'),
    )


def selection_requirements(selection: SpeechSelection, *, rtc_asr_url: str, kokoro_url: str) -> dict:
    stages = {'stt': [], 'tts': []}
    if selection.stt_provider == 'rtc-asr' and not rtc_asr_url:
        stages['stt'].append('Set RTC_ASR_BASE_URL.')
    if selection.tts_provider == 'kokoro' and not kokoro_url:
        stages['tts'].append('Set KOKORO_BASE_URL.')
    for stage, provider in (('stt', selection.stt_provider), ('tts', selection.tts_provider)):
        if provider in {'deepgram', 'cartesia'} and not os.getenv(f'{provider.upper()}_API_KEY', '').strip():
            stages[stage].append(f'Set {provider.upper()}_API_KEY in the Pipecat server environment.')
    if selection.tts_provider == 'cartesia' and (
        selection.tester_voice.startswith('configure-') or selection.target_voice.startswith('configure-')
    ):
        stages['tts'].append('Configure distinct Cartesia caller and agent voice IDs.')
    return {stage: {'ready': not issues, 'issues': issues} for stage, issues in stages.items()}


def selection_issues(selection: SpeechSelection, *, rtc_asr_url: str, kokoro_url: str) -> list[str]:
    stages = selection_requirements(selection, rtc_asr_url=rtc_asr_url, kokoro_url=kokoro_url)
    return list(dict.fromkeys(issue for stage in stages.values() for issue in stage['issues']))


def settings_path() -> Path:
    # Keep the shared contract importable without loading the API OAuth stack.
    from app.services.llm_providers.openai_codex import default_token_path
    return default_token_path().parent / 'speech-settings.json'


def speech_selection() -> SpeechSelection:
    from app.security import document_get, hosted
    if hosted():
        saved = document_get('settings', 'speech')
        return SpeechSelection.model_validate(saved) if saved else deployment_speech_selection()
    path = settings_path()
    if path.exists():
        return SpeechSelection.model_validate_json(path.read_text(encoding='utf-8'))
    return deployment_speech_selection()


def save_speech_selection(selection: SpeechSelection) -> None:
    from app.security import document_put, hosted
    if hosted():
        document_put('settings', 'speech', selection.model_dump(mode='json'))
        return
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(selection.model_dump_json(), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
