from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field


DEFAULT_SIGNALWIRE_TARGET_URL = 'https://holyguacamole.signalwire.me/'
DEFAULT_SIGNALWIRE_VOICE = 'elevenlabs.adam'


class SignalWireHolyGuacamoleRequest(BaseModel):
    caller_text: str = Field(min_length=1, max_length=2_000)
    timeout_seconds: int = Field(default=90, ge=30, le=300)
    scenario: dict[str, Any] = Field(default_factory=dict)
    max_turn_pairs: int = Field(default=1, ge=1, le=1)
    tester_model_name: str | None = None
    execution_run_id: str | None = None
    session_id: str | None = None
    target_url: str = DEFAULT_SIGNALWIRE_TARGET_URL
    voice: str = DEFAULT_SIGNALWIRE_VOICE


class SignalWireHolyGuacamoleError(RuntimeError):
    """Safe-to-return failure from known direct SignalWire execution stages."""


async def run_signalwire_holyguacamole_direct(
    request: SignalWireHolyGuacamoleRequest,
    *,
    kokoro_base_url: str,
    kokoro_model: str,
    kokoro_voice: str,
    event_callback: Any | None = None,
) -> dict[str, Any]:
    if not kokoro_base_url:
        raise SignalWireHolyGuacamoleError(
            'Holy Guacamole SignalWire direct execution requires KOKORO_BASE_URL.'
        )
    caller_wav = await _synthesize_caller(
        request.caller_text,
        kokoro_base_url=kokoro_base_url,
        model=kokoro_model,
        voice=kokoro_voice,
    )
    if event_callback is not None:
        await event_callback({
            'type': 'live_audio',
            'turn_pair': 1,
            'speaker': 'Caller',
            'direction': 'tester_to_target',
            'text': request.caller_text,
            'audio_wav_base64': base64.b64encode(caller_wav).decode('ascii'),
            'media_event': 'tester_audio_ready',
        })
    result = await _run_node_direct(request, caller_wav)
    if result.get('status') != 'pass':
        raise SignalWireHolyGuacamoleError(
            str(result.get('reason') or 'Holy Guacamole SignalWire direct execution failed.')
        )
    if event_callback is not None:
        media = result.get('media') if isinstance(result.get('media'), dict) else {}
        await event_callback({
            'type': 'live_audio',
            'turn_pair': 1,
            'speaker': 'Agent',
            'direction': 'target_to_tester',
            'text': '',
            'audio_wav_base64': str(media.get('target_audio_wav_base64') or ''),
            'media_event': 'target_response_complete',
            'latency': (result.get('exchanges') or [{}])[0].get('latency', {}),
        })
    return result


async def _synthesize_caller(text: str, *, kokoro_base_url: str, model: str, voice: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f'{kokoro_base_url.rstrip("/")}/v1/audio/speech',
                json={'model': model, 'voice': voice, 'input': text, 'response_format': 'wav'},
            )
            response.raise_for_status()
    except Exception as exc:
        raise SignalWireHolyGuacamoleError(
            'Holy Guacamole tester audio synthesis failed; verify Kokoro is reachable.'
        ) from exc
    if not response.content:
        raise SignalWireHolyGuacamoleError('Kokoro returned no tester audio.')
    return response.content


async def _run_node_direct(request: SignalWireHolyGuacamoleRequest, caller_wav: bytes) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix='cae-signalwire-direct-') as temp_dir:
        input_path = Path(temp_dir) / 'input.json'
        input_path.write_text(json.dumps({
            'caller_text': request.caller_text,
            'caller_audio_wav_base64': base64.b64encode(caller_wav).decode('ascii'),
            'target_url': request.target_url,
            'timeout_ms': request.timeout_seconds * 1000,
            'voice': request.voice,
        }), encoding='utf-8')
        script_path = Path(__file__).with_name('signalwire_holyguacamole_direct.mjs')
        env = {**os.environ, 'NODE_ENV': 'production'}
        process = await asyncio.create_subprocess_exec(
            'node',
            str(script_path),
            '--input-json',
            str(input_path),
            '--json-only',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=max(60, request.timeout_seconds + 30),
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise SignalWireHolyGuacamoleError(
                'Holy Guacamole SignalWire direct execution timed out.'
            ) from exc
    output = stdout.decode('utf-8', errors='replace').strip()
    try:
        payload = json.loads(output.splitlines()[-1]) if output else {}
    except (json.JSONDecodeError, IndexError) as exc:
        raise SignalWireHolyGuacamoleError(
            'Holy Guacamole SignalWire direct executor returned invalid JSON.'
        ) from exc
    if process.returncode != 0 and payload.get('status') != 'pass':
        detail = str(payload.get('reason') or stderr.decode('utf-8', errors='replace')).strip()
        raise SignalWireHolyGuacamoleError(
            'Holy Guacamole SignalWire direct execution failed.'
            + (f' Detail: {detail[:240]}' if detail else '')
        )
    return payload
