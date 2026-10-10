"""Real LiveKit RTC transport; no text endpoint masquerading as a voice call."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import math
import time
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from app.services.waylo_target import WayloClient, WayloError, clean_native, project_capture


def pcm_wav(pcm: bytes, rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return output.getvalue()


def read_wav(audio: bytes):
    with wave.open(io.BytesIO(audio), 'rb') as reader:
        if reader.getsampwidth() != 2 or reader.getnchannels() != 1 or reader.getframerate() not in {16000, 24000, 48000}:
            raise WayloError('Tester audio must be mono PCM16 WAV at 16, 24 or 48 kHz.')
        return reader.getframerate(), reader.readframes(reader.getnframes())


class LiveKitVoicePeer:
    def __init__(self, *, rtc=None):
        if rtc is None:
            from livekit import rtc
        self.rtc = rtc
        self.room = rtc.Room()
        self.events, self.segments, self.tasks = [], {}, set()
        self.streams = []
        self.origin = time.monotonic()
        self.origin_utc = datetime.now(UTC).isoformat()
        self.source = None
        self.remote_pcm = bytearray()
        self.remote_chunks = []
        self.last_speech = 0.0
        self.speech_count = 0
        self.ready = asyncio.Event()
        self.remote_identity = None
        self.error = None

        def text_stream(reader, participant_identity):
            task = asyncio.create_task(self._read_transcription(reader, participant_identity))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        self.room.register_text_stream_handler('lk.transcription', text_stream)

        @self.room.on('track_subscribed')
        def subscribed(track, publication, participant):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            if self.remote_identity is not None and participant.identity != self.remote_identity:
                self.error = 'Multiple remote audio publishers; target identity is ambiguous.'
                return
            self.remote_identity = participant.identity  # memory only
            task = asyncio.create_task(self._receive(track))
            self.tasks.add(task)
            self.ready.set()

        @self.room.on('transcription_received')
        def transcribed(segments, participant, publication):
            local = participant is not None and participant.identity == self.room.local_participant.identity
            for segment in segments:
                key = ('caller' if local else 'remote', segment.id)
                revision = hashlib.sha256(f'{segment.text}/{segment.final}'.encode()).hexdigest()[:16]
                self.segments[key] = segment.text if segment.final else self.segments.get(key, '')
                event = {'event_id': f'livekit/transcript/{key[0]}/{segment.id}/{revision}',
                         'event_type': 'asr.final' if local and segment.final else 'asr.partial' if local else 'transcript.remote',
                         'source': 'livekit.transcription_received', 'text': segment.text, 'final': segment.final,
                         'segment_id': segment.id, 'speaker': key[0], 'received_at_ms': self.now_ms(),
                         'clock_origin': 'cae_monotonic_since_transport_creation',
                         'native_start_time': segment.start_time, 'native_end_time': segment.end_time,
                         'native_clock_origin': 'unspecified_by_provider', 'native_unit': 'unknown'}
                if not any(e['event_id'] == event['event_id'] for e in self.events):
                    self.events.append(event)

        for kind in ('reconnecting', 'reconnected', 'disconnected'):
            self.room.on(kind, lambda *args, event_kind=kind: self.events.append({
                'event_id': f'livekit/{event_kind}/{len(self.events)}', 'event_type': f'transport.{event_kind}',
                'source': 'livekit.rtc', 'timestamp_ms': self.now_ms(), 'clock_origin': 'cae_monotonic_since_transport_creation'}))

    def now_ms(self):
        return round((time.monotonic() - self.origin) * 1000, 3)

    async def _read_transcription(self, reader, participant_identity):
        attributes = reader.info.attributes
        segment_id = attributes.get('lk.segment_id') or reader.info.stream_id
        track_id = attributes.get('lk.transcribed_track_id')
        # Transcriptions are sent by the agent on behalf of the caller too.
        # Resolve the transcribed track, not the stream publisher, as speaker.
        local_tracks = self.room.local_participant.track_publications
        local = bool(track_id and track_id in local_tracks)
        if not local and participant_identity != self.remote_identity:
            return  # Unidentified publisher is not target evidence.
        try:
            text = await asyncio.wait_for(reader.read_all(), 15)
        except Exception:
            self.events.append({'event_id': f'livekit/stream-error/{len(self.events)}',
                'event_type': 'transcript.incomplete', 'source': 'livekit.lk.transcription'})
            return
        if len(text) > 32_000:
            self.error = 'LiveKit transcript exceeded capture limit.'
            return
        final = attributes.get('lk.transcription_final') == 'true'
        speaker = 'caller' if local else 'remote'
        key = (speaker, segment_id)
        if final:
            self.segments[key] = text
        revision = hashlib.sha256(f'{text}/{final}'.encode()).hexdigest()[:16]
        event_id = f'livekit/transcript/{speaker}/{segment_id}/{revision}'
        if not any(e['event_id'] == event_id for e in self.events):
            self.events.append({'event_id': event_id,
                'event_type': 'asr.final' if local and final else 'asr.partial' if local else 'transcript.remote',
                'source': 'livekit.lk.transcription', 'text': text, 'final': final,
                'segment_id': segment_id, 'speaker': speaker, 'received_at_ms': self.now_ms(),
                'clock_origin': 'cae_monotonic_since_transport_creation',
                'native_unit': 'unknown', 'native_clock_origin': 'unspecified_by_provider'})

    async def connect(self, bootstrap: dict):
        rtc = self.rtc
        options = rtc.RoomOptions(auto_subscribe=True)
        if bootstrap.get('turn'):
            turn = bootstrap['turn']
            options.rtc_config = rtc.RtcConfiguration(
                ice_transport_type=rtc.IceTransportType.TRANSPORT_RELAY,
                ice_servers=[rtc.IceServer(urls=turn['urls'], username=turn['username'], password=turn['credential'])])
        await self.room.connect(bootstrap['livekitUrl'], bootstrap['participantToken'], options=options)
        if self.room.name != bootstrap['roomName']:
            raise WayloError('Connected LiveKit room does not match the Waylo bootstrap.')
        await asyncio.wait_for(self.ready.wait(), 20)

    async def _receive(self, track):
        stream = self.rtc.AudioStream(track, sample_rate=24000, num_channels=1)
        self.streams.append(stream)
        try:
            async for event in stream:
                pcm = bytes(event.frame.data)
                if len(self.remote_pcm) + len(pcm) > 24_000 * 2 * 310:
                    self.error = 'Remote audio exceeded capture limit.'
                    return
                start = self.now_ms()
                offset = len(self.remote_pcm)
                self.remote_pcm.extend(pcm)
                samples = array('h', pcm)
                rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples)))
                if rms > 150:  # observer-side detector, NOT target interruption evidence
                    self.last_speech = time.monotonic()
                    self.speech_count += 1
                self.remote_chunks.append({'received_at_ms': start, 'pcm_offset': offset, 'bytes': len(pcm), 'speech': rms > 150})
        except Exception:
            self.error = 'LiveKit audio receive failed.'
        finally:
            await stream.aclose()

    async def send(self, audio: bytes, *, turn_id: str, reference: str, artifact_dir: Path):
        rate, pcm = read_wav(audio)
        if not self.source:
            self.source = self.rtc.AudioSource(rate, 1)
            self.track = self.rtc.LocalAudioTrack.create_audio_track('cae-controlled-caller', self.source)
            await self.room.local_participant.publish_track(self.track, self.rtc.TrackPublishOptions(
                source=self.rtc.TrackSource.SOURCE_MICROPHONE))
        if self.source.sample_rate != rate:
            raise WayloError('Tester sample rate changed within the call.')
        started, sent = self.now_ms(), bytearray()
        chunk_bytes = rate // 50 * 2  # 20 ms, AudioSource applies queue backpressure
        failure = False
        try:
            for offset in range(0, len(pcm), chunk_bytes):
                chunk = pcm[offset:offset + chunk_bytes]
                await self.source.capture_frame(self.rtc.AudioFrame(chunk, rate, 1, len(chunk) // 2))
                sent.extend(chunk)
            await self.source.wait_for_playout()
        except Exception:
            failure = True
        sent_wav = pcm_wav(bytes(sent), rate)
        # Preserve only samples actually accepted by AudioSource, including partial failures.
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / f'{turn_id}-sent.wav').write_bytes(sent_wav)
        observation = {'event_id': f'{turn_id}/sent', 'event_type': 'tester.audio.sent', 'source': 'livekit.AudioSource',
                       'turn_id': turn_id, 'reference_text': reference, 'sample_rate_hz': rate,
                       'channels': 1, 'intended_samples': len(pcm) // 2, 'accepted_samples': len(sent) // 2,
                       'audio_sha256': hashlib.sha256(sent_wav).hexdigest(), 'started_at_ms': started,
                       'ended_at_ms': self.now_ms(), 'clock_origin': 'cae_monotonic_since_transport_creation',
                       'clock_origin_utc': self.origin_utc, 'duration_ms': len(sent) / (rate * 2) * 1000,
                       'delivery': 'local_queue_drained' if not failure else 'partial_or_unconfirmed',
                       'peer_receipt': 'unknown', 'clipped_or_dropped_at_peer': 'unknown'}
        observation['overlap_with_observed_target_speech'] = time.monotonic() - getattr(self, 'last_speech', 0) < .2
        self.events.append(observation)
        if failure:
            raise WayloError('Tester audio send failed; accepted samples were retained for review.')
        return observation, sent_wav

    async def wait_response(self, previous_speech: int, *, timeout: float = 25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error:
                raise WayloError(self.error)
            if self.speech_count > previous_speech and time.monotonic() - self.last_speech > 1.2:
                return
            await asyncio.sleep(.05)
        raise WayloError('Target response speech/silence boundary was not observed before timeout.')

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.source:
            await self.source.aclose()
        await self.room.disconnect()


async def run_waylo_call(*, target: dict, correlation_id: str, scenario: dict, suite_id: str,
                         artifact_dir: Path, max_exchanges: int, timeout_seconds: int,
                         next_utterance: Callable, synthesize: Callable,
                         event_observer: Callable | None = None,
                         peer: LiveKitVoicePeer | None = None, client: WayloClient | None = None):
    client = client or WayloClient(target)
    peer = peer or LiveKitVoicePeer()
    bootstrap, before, capture, media_dialogs, turns = None, None, None, [], []
    error = None
    sent_media_bytes = 0
    after_items = []
    prepared = {}
    try:
        async with asyncio.timeout(timeout_seconds):
            # Resolve tester media before creating a billable remote session.
            utterance = await next_utterance([], 1)
            audio = await synthesize(utterance, 1)
            barge_turn = scenario.get('waylo_test', {}).get('barge_in_turn')
            if barge_turn and barge_turn <= max_exchanges:
                next_text = await next_utterance([], barge_turn)
                prepared[barge_turn] = (next_text, await synthesize(next_text, barge_turn))
            if scenario.get('waylo_test'):
                current = await client.request('GET', f'agents/{client.agent}')
                preset = current.get('presetId') if isinstance(current, dict) else None
                if preset in {'item_capture_assistant', 'catalog_order_assistant'}:
                    raise WayloError('Mike test contract conflicts with the active preset. Use a disposable catalog-aware notes-only agent; no call started.')
            bootstrap = await client.bootstrap(correlation_id)
            before = await client.request('GET', f'sessions/{bootstrap["sessionId"]}/items')
            if not isinstance(before, list):
                raise WayloError('Waylo pre-call items were not a list.')
            await peer.connect(bootstrap)
            # Let an optional greeting finish before the first controlled caller turn.
            try:
                await peer.wait_response(0, timeout=5)
            except WayloError:
                if peer.error:
                    raise
            if peer.speech_count:
                greeting = pcm_wav(bytes(peer.remote_pcm))
                artifact_dir.mkdir(parents=True, exist_ok=True)
                (artifact_dir / 'agent-greeting-received.wav').write_bytes(greeting)
                greeting_text = '\n'.join(text for key, text in peer.segments.items() if key[0] == 'remote' and text)
                if event_observer:
                    event_observer({'speaker': 'Agent', 'text': greeting_text or '[Greeting audio; transcript pending]',
                        'audio': greeting, 'direction': 'target_to_tester'})
                turns.append({'speaker': 'Agent', 'text': greeting_text or '[Greeting audio; transcript pending]',
                    'turn_index': 1, 'direction': 'target_to_tester', 'frame_metadata': {'greeting': True}})
                sent_media_bytes += len(greeting)
                if sent_media_bytes <= 600_000:
                    media_dialogs.append({'type': 'recording', 'parties': [1], 'mediatype': 'audio/wav',
                        'encoding': 'base64url', 'body': base64.urlsafe_b64encode(greeting).decode().rstrip('='),
                        'content_hash': base64.urlsafe_b64encode(hashlib.sha512(greeting).digest()).decode().rstrip('=')})
            for index in range(1, max_exchanges + 1):
                turn_id = f'caller-{index}'
                previous = peer.speech_count
                offset = len(peer.remote_pcm)
                previous_segments = dict(peer.segments)
                audio_bytes, audio_condition = audio if isinstance(audio, tuple) else (audio, {})
                observation, sent_wav = await peer.send(audio_bytes, turn_id=turn_id, reference=utterance,
                                                        artifact_dir=artifact_dir)
                observation['audio_condition'] = audio_condition
                if event_observer:
                    event_observer({'speaker': 'Caller', 'text': utterance, 'audio': sent_wav,
                                    'direction': 'tester_to_target', 'llm_output': utterance})
                turns.append({'speaker': 'Caller', 'text': utterance, 'turn_index': len(turns) + 1,
                              'direction': 'tester_to_target', 'frame_metadata': {'source_text': utterance,
                              'duration_ms': observation['duration_ms'], 'audio_sha256': observation['audio_sha256']}})
                sent_media_bytes += len(sent_wav)
                if sent_media_bytes <= 600_000:
                    media_dialogs.append({'type': 'recording', 'parties': [0], 'mediatype': 'audio/wav',
                        'encoding': 'base64url', 'body': base64.urlsafe_b64encode(sent_wav).decode().rstrip('='),
                        'content_hash': base64.urlsafe_b64encode(hashlib.sha512(sent_wav).digest()).decode().rstrip('=')})
                if scenario.get('waylo_test', {}).get('barge_in_turn') == index + 1:
                    # Start the next caller turn during observed target speech,
                    # not after the usual observer-side silence boundary.
                    deadline = time.monotonic() + 15
                    while peer.speech_count <= previous and time.monotonic() < deadline:
                        await asyncio.sleep(.02)
                    if peer.speech_count <= previous:
                        raise WayloError('No target speech available for the controlled interruption.')
                    peer.events.append({'event_id': f'{turn_id}/barge-in-scheduled', 'event_type': 'tester.barge_in_scheduled',
                        'source': 'cae_controlled_tester', 'timestamp_ms': peer.now_ms(),
                        'clock_origin': 'cae_monotonic_since_transport_creation', 'target_interruption_handled': 'unknown'})
                else:
                    await peer.wait_response(previous)
                target_audio = pcm_wav(bytes(peer.remote_pcm[offset:]))
                (artifact_dir / f'agent-{index}-received.wav').write_bytes(target_audio)
                response = '\n'.join(text for key, text in peer.segments.items()
                                     if key[0] == 'remote' and text and text != previous_segments.get(key))
                peer.events.append({'event_id': f'agent-{index}/received', 'event_type': 'target.audio.received',
                    'source': 'livekit.AudioStream', 'turn_id': f'agent-{index}',
                    'audio_sha256': hashlib.sha256(target_audio).hexdigest(), 'sample_rate_hz': 24000,
                    'duration_ms': (len(target_audio) - 44) / 48, 'channels': 1,
                    'clock_origin': 'cae_monotonic_since_transport_creation', 'clock_origin_utc': peer.origin_utc,
                    'observer_end_ms': peer.now_ms(), 'target_playback_ack': 'unknown'})
                candidates = [chunk for chunk in peer.remote_chunks if chunk['speech']
                              and chunk['pcm_offset'] >= offset and chunk['received_at_ms'] >= observation['ended_at_ms']]
                if candidates and not observation['overlap_with_observed_target_speech']:
                    peer.events.append({'event_id': f'caller-{index}/observer-latency', 'event_type': 'latency.observation',
                        'source': 'cae_livekit_observer', 'turn_id': turn_id,
                        'value_ms': round(candidates[0]['received_at_ms'] - observation['ended_at_ms'], 3),
                        'unit': 'ms', 'clock_origin': 'cae_monotonic_since_transport_creation',
                        'definition': 'Local caller queue drained to first received remote frame above RMS 150; not target-internal latency.'})
                sent_media_bytes += len(target_audio)
                if sent_media_bytes <= 600_000:
                    media_dialogs.append({'type': 'recording', 'parties': [1], 'mediatype': 'audio/wav',
                        'encoding': 'base64url', 'body': base64.urlsafe_b64encode(target_audio).decode().rstrip('='),
                        'content_hash': base64.urlsafe_b64encode(hashlib.sha512(target_audio).digest()).decode().rstrip('=')})
                if event_observer:
                    event_observer({'speaker': 'Agent', 'text': response or 'Target audio captured; transcript pending.',
                                    'audio': target_audio, 'direction': 'target_to_tester'})
                turns.append({'speaker': 'Agent', 'text': response or '[Target audio captured; transcript pending]',
                              'turn_index': len(turns) + 1, 'direction': 'target_to_tester',
                              'frame_metadata': {'audio_sha256': hashlib.sha256(target_audio).hexdigest(),
                                                 'transcript_source': 'livekit_remote_transcription'}})
                if index < max_exchanges:
                    if index + 1 in prepared:
                        utterance, audio = prepared[index + 1]
                    else:
                        utterance = await next_utterance(turns, index + 1)
                        audio = await synthesize(utterance, index + 1)
                # Do not delay a scheduled interruption with an HTTP read.
                if index + 1 != barge_turn:
                    try:
                        items = await client.request('GET', f'sessions/{bootstrap["sessionId"]}/items')
                        if isinstance(items, list):
                            after_items.append({'turn_id': f'caller-{index}', 'items': items,
                                                'observed_at': datetime.now(UTC).isoformat()})
                    except WayloError:
                        pass  # Final capture reports unavailable state, not synthetic success.
    except Exception as exc:
        # SDK/provider errors can embed URLs/tokens. Export only a safe class/adapter message.
        error = str(exc) if isinstance(exc, WayloError) else f'Waylo call interrupted ({type(exc).__name__}).'
    finally:
        try:
            await asyncio.wait_for(peer.close(), 10)
        except Exception:
            error = error or 'LiveKit cleanup failed; capture needs review.'
    try:
        if not bootstrap:
            raise WayloError(error or 'No Waylo session was created.')
        # Re-read complete data, not just cached preview, to include late worker flushes.
        # Stable snapshots are provisional; provider lifecycle is not our authority.
        previous_digest = None
        for _ in range(6):
            capture = await client.capture(bootstrap['sessionId'], origin='cae_ai_tester')
            digest = hashlib.sha256(str(capture).encode()).hexdigest()
            if digest == previous_digest and capture['session'].get('endedAt'):
                break
            previous_digest = digest
            await asyncio.sleep(.5)
        media_events = clean_native(peer.events, (bootstrap['participantToken'], client._secret))
        for event in media_events:
            event.update(target_id=target['id'], session_id=bootstrap['sessionId'])
        exported = project_capture(capture, voice_events=media_events, before_items=before,
                                   suite_id=suite_id, scenario_id=scenario['id'], media_dialogs=media_dialogs,
                                   test_expectations=scenario.get('waylo_test'), after_items=after_items)
        # Native finals remain receipts, not the tester's intended utterance. Both are retained.
        transcript = '\n'.join(f'{"Caller" if line["role"] == "user" else "Agent"}: {line["text"]}'
                               for line in capture['transcripts'])
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / 'agent-received.wav').write_bytes(pcm_wav(bytes(peer.remote_pcm)))
        return {'turns': turns, 'transcript': transcript, 'ietf_vcon_export': exported['vcon'],
                'evidence_coverage': exported['coverage'], 'waylo_capture': capture,
                'configuration_check': exported['configuration_check'], 'action_trace': [],
                'final_state': {'evidence_scope': 'waylo_capture', 'tester_error': error} if error else {},
                'verdict': 'needs_review', 'score': None, 'latency_marks': [],
                'evaluation_report': {}, 'recording': None,
                'audio_session': {'transport': 'waylo_livekit', 'session_id': bootstrap['sessionId']}}
    finally:
        await client.close()
