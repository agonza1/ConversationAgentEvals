"""Cloud adapters for CAE's existing paced PCM / Pipecat turn pipeline.

STT uploads live PCM, never LLM text. TTS streams WAV bytes per first-clause /
sentence chunk. Provider credentials stay in the worker's environment.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any
from urllib.parse import urlencode

import websockets
from pipecat.frames.frames import InterimTranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection

from speech_configuration import SpeechSelection, deployment_speech_selection, selection_issues, selection_requirements

from streaming_media import StreamingKokoroProcessor, StreamingRtcAsrProcessor


class StreamingCloudTtsProcessor(StreamingKokoroProcessor):
    """Reuse measured chunking, PCM framing and timing with a cloud transport."""

    def __init__(self, *, selection: SpeechSelection, **kwargs: Any) -> None:
        self.selection = selection
        self.provider_name = selection.tts_provider
        kwargs.update(base_url='', model=selection.tts_model)
        super().__init__(**kwargs)

    def _speech_request(self, text: str) -> dict[str, Any]:
        if self.selection.tts_provider == 'deepgram':
            return {
                'url': 'https://api.deepgram.com/v1/speak',
                'headers': {'Authorization': f'Token {os.environ["DEEPGRAM_API_KEY"]}'},
                'params': {'model': self.voice, 'encoding': 'linear16', 'container': 'wav', 'sample_rate': 24000},
                'json': {'text': text},
            }
        return {
            'url': 'https://api.cartesia.ai/tts/bytes',
            'headers': {'Authorization': f'Bearer {os.environ["CARTESIA_API_KEY"]}', 'Cartesia-Version': self.selection.cartesia_version},
            'json': {
                'model_id': self.model, 'transcript': text, 'voice': self.voice,
                'language': self.selection.language,
                'output_format': {'container': 'wav', 'encoding': 'pcm_s16le', 'sample_rate': 24000},
            },
        }


class StreamingCloudAsrProcessor(StreamingRtcAsrProcessor):
    """Silero boundaries + vendor streaming STT with an explicit drain barrier.

Deepgram is_final is a *segment*, not end-of-turn. CloseStream + Metadata
drains all final segments. Cartesia uses its manual endpoint and flush_done.
Each Silero utterance has a fresh socket; clauses accumulate until CAE's
speech-end frame. This avoids idle keep-alives and cross-utterance receipts.
"""

    def __init__(self, *, selection: SpeechSelection, **kwargs: Any) -> None:
        self.selection = selection
        super().__init__(base_url='', **kwargs)
        self.protocol = ('deepgram-listen-v1' if selection.stt_provider == 'deepgram'
                         else 'cartesia-stt-websocket')
        self.interim_results = selection.stt_provider == 'deepgram'
        self.partial_interval_ms = None
        self.partial_window_seconds = None
        self.max_buffer_seconds = None
        self._segment_index = 0
        self._drained = False

    def _stream_settings(self):
        try:
            timeout = float(os.getenv('CAE_STT_FINAL_TIMEOUT_SECONDS', '20'))
        except ValueError:
            raise ValueError('CAE_STT_FINAL_TIMEOUT_SECONDS must be from 5 to 120.') from None
        if not 5 <= timeout <= 120:
            raise ValueError('CAE_STT_FINAL_TIMEOUT_SECONDS must be from 5 to 120.')
        return self.selection.stt_provider == 'deepgram', None, None, timeout

    def _buffer_limit(self):
        return None

    def _connection(self) -> tuple[str, dict[str, str]]:
        if self.selection.stt_provider == 'deepgram':
            query = {'model': self.selection.stt_model, 'encoding': 'linear16', 'sample_rate': 16000,
                     'channels': 1, 'language': self.selection.language, 'interim_results': 'true',
                     'punctuate': 'true', 'endpointing': 'false'}
            return 'wss://api.deepgram.com/v1/listen?' + urlencode(query), {
                'Authorization': f'Token {os.environ["DEEPGRAM_API_KEY"]}',
            }
        query = {'model': self.selection.stt_model, 'encoding': 'pcm_s16le', 'sample_rate': 16000,
                 'language': self.selection.language, 'cartesia_version': self.selection.cartesia_version}
        return 'wss://api.cartesia.ai/stt/websocket?' + urlencode(query), {
            'X-API-Key': os.environ['CARTESIA_API_KEY'],
        }

    async def _connect(self) -> None:
        self._raise_protocol_error()
        if self.websocket is not None:
            await self.websocket.close()
        if self.receiver_task is not None:
            await asyncio.gather(self.receiver_task, return_exceptions=True)
        self.closing = False
        self._drained = False
        url, headers = self._connection()
        # websockets 12/13 use extra_headers; 14/15 use additional_headers.
        header_arg = 'additional_headers' if int(websockets.__version__.split('.')[0]) >= 14 else 'extra_headers'
        try:
            self.websocket = await websockets.connect(url, **{header_arg: headers}, open_timeout=10, close_timeout=2)
        except Exception as exc:
            raise RuntimeError(f'{self.selection.stt_provider} STT connection failed ({type(exc).__name__}); check server credentials and connectivity.') from None
        self.receiver_task = asyncio.create_task(self._receive())

    async def _start_utterance(self, direction: FrameDirection) -> None:
        if self.active or self.finalizing:
            return
        if self.end_type is None or not self.turn_open:
            self.transcript = ''
            self.final_segments = []
            self.final_segment_event_ids = set()
            self.final_result = {}
            self.turn_final_emitted = False
            self.interims = []
            self.speech_started_at = self.speech_ended_at = self.final_at = None
            self.server_timing = {}
            self.turn_open = True
        self.final_received.clear()
        self.current_stream_id = f'{self.participant}-{time.time_ns()}'
        self._segment_index = 0
        await self._connect()
        self.active = True
        self.speech_started_at = time.time()
        await self.push_frame(VADUserStartedSpeakingFrame(start_secs=self.vad.params.start_secs), direction)
        buffered = tuple(self.pre_roll)
        self.pre_roll.clear()
        for audio in buffered:
            await self.websocket.send(audio)
        if self.event_callback:
            await self.event_callback({'type': 'vad', 'participant': self.participant, 'state': 'speech_started'})

    async def _finalize(self, direction: FrameDirection, *, wait_for_final: bool) -> None:
        if not self.active or self.websocket is None:
            return
        self.active = False
        self.finalizing = True
        self.speech_ended_at = time.time() - self.vad.params.stop_secs
        await self.push_frame(VADUserStoppedSpeakingFrame(stop_secs=self.vad.params.stop_secs), direction)
        command = json.dumps({'type': 'CloseStream'}) if self.selection.stt_provider == 'deepgram' else 'finalize'
        await self.websocket.send(command)
        if wait_for_final:
            await self._wait_for_final()

    async def _wait_for_final(self) -> None:
        try:
            await asyncio.wait_for(self.final_received.wait(), timeout=self.final_timeout_seconds)
        except TimeoutError:
            raise RuntimeError(f'{self.selection.stt_provider} STT did not finish within {self.final_timeout_seconds:g} seconds.') from None
        self._raise_protocol_error()

    async def _receive(self) -> None:
        try:
            async for raw in self.websocket:
                payload = json.loads(raw)
                kind = payload.get('type')
                if kind in {'Error', 'error'}:
                    # Do not reflect arbitrary remote text into logs/run evidence.
                    raise RuntimeError('provider_error')
                if kind == 'Results':
                    alternatives = (payload.get('channel') or {}).get('alternatives') or []
                    text = str(alternatives[0].get('transcript') or '').strip() if alternatives else ''
                elif kind == 'transcript':
                    text = str(payload.get('text') or '').strip()
                else:
                    text = ''
                if text:
                    if payload.get('is_final'):
                        self._segment_index += 1
                        revision = (payload.get('start'), payload.get('duration')) if kind == 'Results' else self._segment_index
                        normalized = {'text': text, 'is_final': True, 'revision': revision,
                                      'metadata': {'client_stream_id': self.current_stream_id},
                                      'provider': self.selection.stt_provider}
                        if self._record_final_segment(text, normalized):
                            self.final_result = normalized
                    else:
                        self.interims.append(text)
                        await self.push_frame(InterimTranscriptionFrame(text=text, user_id=self.participant,
                            timestamp=str(time.time()), language=self.selection.language, result={'provider': self.selection.stt_provider}))
                if self.finalizing and kind in {'Metadata', 'flush_done'}:
                    self._drained = True
                    self.final_at = time.time()
                    self.finalizing = False
                    if self.end_type is None and self.transcript:
                        await self.push_frame(self._final_frame(self.transcript, self.final_result))
                    self.final_received.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.protocol_error = RuntimeError(f'{self.participant} {self.selection.stt_provider} STT stream failed ({type(exc).__name__}).')
        finally:
            if not self.closing and not self._drained and self.protocol_error is None:
                self.protocol_error = RuntimeError(f'{self.selection.stt_provider} STT disconnected before finalization.')
            self.final_received.set()

    async def _close(self) -> None:
        self.closing = True
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None
        if self.receiver_task is not None:
            self.receiver_task.cancel()
            await asyncio.gather(self.receiver_task, return_exceptions=True)
            self.receiver_task = None
        self.active = self.finalizing = self.turn_open = False

    def _final_frame(self, text: str, result: dict[str, Any]):
        frame = super()._final_frame(text, result)
        frame.language = self.selection.language
        return frame


def build_tts(selection: SpeechSelection, *, base_url: str, **kwargs: Any) -> StreamingKokoroProcessor:
    if selection.tts_provider == 'kokoro':
        return StreamingKokoroProcessor(base_url=base_url, model=selection.tts_model, **kwargs)
    return StreamingCloudTtsProcessor(selection=selection, **kwargs)


def build_asr(selection: SpeechSelection, *, base_url: str, stream_path: str, **kwargs: Any) -> StreamingRtcAsrProcessor:
    if selection.stt_provider == 'rtc-asr':
        return StreamingRtcAsrProcessor(base_url=base_url, stream_path=stream_path, **kwargs)
    return StreamingCloudAsrProcessor(selection=selection, **kwargs)
