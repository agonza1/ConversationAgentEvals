"""Real LiveKit RTC transport; no text endpoint masquerading as a voice call."""
from __future__ import annotations

import asyncio
import hashlib
import io
import math
import time
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from app.services.waylo_target import WayloClient, WayloError, clean_native, project_capture, validate_items
from app.services.live_audio_monitor import LiveAudioMonitor
from app.services.waylo_call_policy import CLOSING_TEXT, caller_has_closed, closing_due, closing_reserve_seconds


def pcm_wav(pcm: bytes, rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return output.getvalue()


def read_wav(audio: bytes):
    try:
        if not isinstance(audio, bytes) or len(audio) > 10_000_000:
            raise ValueError
        with wave.open(io.BytesIO(audio), 'rb') as reader:
            if (reader.getcomptype() != 'NONE' or reader.getsampwidth() != 2
                    or reader.getnchannels() != 1 or reader.getframerate() not in {16000, 24000, 48000}):
                raise ValueError
            pcm = reader.readframes(reader.getnframes())
            if not pcm or len(pcm) != reader.getnframes() * 2:
                raise ValueError
            return reader.getframerate(), pcm
    except (ValueError, EOFError, wave.Error):
        raise WayloError('Tester audio must be complete, nonempty mono PCM16 WAV at 16, 24 or 48 kHz (up to 10 MB).') from None


def _record_transcription(peer, key: tuple, event: dict) -> None:
    """Keep native revisions; collapse only matching dual-channel final receipts.

    LiveKit's deprecated event and modern text stream may use different IDs for
    the same speech. A text-only global set would erase legitimate repetition.
    Pair one receipt per channel in a short, turn-scoped observation window;
    preserve partials, corrections and every native final in the event journal.
    """
    if any(e.get('event_id') == event['event_id'] for e in peer.events):
        return
    aliases = getattr(peer, '_transcript_aliases', None)
    if aliases is None:
        peer._transcript_aliases = aliases = {}
        peer._transcript_finals = {}
    finals = peer._transcript_finals
    canonical = aliases.get(key, key)
    scope = getattr(peer, 'transcript_turn_id', 'greeting')
    event['observation_turn_id'] = scope
    source = event['source']
    if event['final']:
        if key not in aliases and key not in finals:
            for candidate, info in reversed(list(finals.items())):
                if (candidate[0] != key[0] or info['scope'] != scope
                        or source in info['sources'] or info['text'] != event['text']
                        or not 0 <= event['received_at_ms'] - info['received_at_ms'] <= 1000):
                    continue
                track = event.get('transcribed_track_id')
                if track and info.get('track') and track != info['track']:
                    continue
                canonical = candidate
                aliases[key] = canonical
                break
        previous = finals.get(canonical)
        if canonical != key:
            event['canonical_segment_id'] = canonical[1]
            if previous and previous['text'] == event['text']:
                event['duplicate_of_segment_id'] = canonical[1]
        # Corrected finals remain authoritative, not concatenated or paraphrased.
        peer.segments[canonical] = event['text']
        finals[canonical] = {'text': event['text'], 'scope': scope,
            'sources': (previous['sources'] if previous else set()) | {source},
            'received_at_ms': event['received_at_ms'],
            'track': event.get('transcribed_track_id') or (previous.get('track') if previous else None)}
    peer.events.append(event)
    if key[0] == 'remote':
        peer.last_remote_transcript = time.monotonic()
    elif event['final'] and getattr(peer, 'sent_references', None) and not getattr(peer, 'consent_alert', False):
        from app.services.waylo_consent import approval_integrity
        receipts = {}
        for receipt in peer.events:
            if (receipt.get('speaker') == 'caller' and receipt.get('final')
                    and not receipt.get('duplicate_of_segment_id')):
                receipts[receipt.get('canonical_segment_id') or receipt.get('segment_id') or receipt['event_id']] = {
                    'id': receipt['event_id'], 'text': receipt.get('text', '')}
        integrity = approval_integrity(list(peer.sent_references.values()), list(receipts.values()))
        if integrity['status'] == 'critical_mismatch':
            peer.consent_alert = True
            peer.error = 'Critical purchase-consent ASR mismatch; call stopped for review.'
            peer.events.append({'event_id': 'tester/consent-integrity-alert',
                'event_type': 'tester.consent_integrity_alert', 'source': 'cae_reference_vs_asr',
                'diagnostic': integrity, 'remote_action_prevention': 'not_proven'})


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
        self.last_remote_transcript = 0.0
        self.agent_state = None
        self.agent_state_updated = 0.0
        self.pending_remote_transcripts = 0
        self.sent_references = {}
        self.consent_alert = False
        self.speech_count = 0
        self.ready = asyncio.Event()
        self.remote_identity = None
        self.error = None
        self.monitor = None
        self.monitor_turn_pair = 1
        self.noise_mixer = None
        self.continuous_caller = None

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
            self._observe_agent_state(getattr(participant, 'attributes', {}), participant)
            task = asyncio.create_task(self._receive(track))
            self.tasks.add(task)
            self.ready.set()

        @self.room.on('transcription_received')
        def transcribed(segments, participant, publication):
            local = participant is not None and participant.identity == self.room.local_participant.identity
            for segment in segments:
                key = ('caller' if local else 'remote', segment.id)
                revision = hashlib.sha256(f'{segment.text}/{segment.final}'.encode()).hexdigest()[:16]
                event = {'event_id': f'livekit/transcript/{key[0]}/{segment.id}/{revision}',
                         'event_type': 'asr.final' if local and segment.final else 'asr.partial' if local else 'transcript.remote',
                         'source': 'livekit.transcription_received', 'text': segment.text, 'final': segment.final,
                         'segment_id': segment.id, 'speaker': key[0], 'received_at_ms': self.now_ms(),
                         'clock_origin': 'cae_monotonic_since_transport_creation',
                         'native_start_time': segment.start_time, 'native_end_time': segment.end_time,
                         'native_clock_origin': 'unspecified_by_provider', 'native_unit': 'unknown'}
                if getattr(publication, 'sid', None):
                    event['transcribed_track_id'] = publication.sid
                _record_transcription(self, key, event)

        @self.room.on('participant_attributes_changed')
        def attributes_changed(changed, participant):
            self._observe_agent_state(changed, participant)

        for kind in ('reconnecting', 'reconnected', 'disconnected'):
            self.room.on(kind, lambda *args, event_kind=kind: self.events.append({
                'event_id': f'livekit/{event_kind}/{len(self.events)}', 'event_type': f'transport.{event_kind}',
                'source': 'livekit.rtc', 'timestamp_ms': self.now_ms(), 'clock_origin': 'cae_monotonic_since_transport_creation'}))

    def now_ms(self):
        return round((time.monotonic() - self.origin) * 1000, 3)

    def _observe_agent_state(self, attributes, participant):
        if participant.identity != self.remote_identity:
            return
        state = attributes.get('lk.agent.state')
        if state not in {'initializing', 'idle', 'listening', 'thinking', 'speaking'}:
            return
        self.agent_state = state
        self.agent_state_updated = time.monotonic()
        self.events.append({'event_id': f'livekit/agent-state/{len(self.events)}',
            'event_type': 'target.agent_state', 'state': state, 'source': 'livekit.participant_attributes',
            'timestamp_ms': self.now_ms(), 'clock_origin': 'cae_monotonic_since_transport_creation'})

    def _monitor_audio(self, direction, pcm, rate, *, media_activity=True):
        if self.monitor:
            try:
                self.monitor.add(direction, pcm, sample_rate=rate, turn_pair=self.monitor_turn_pair,
                    **({'media_activity': False} if not media_activity else {}))
            except Exception:
                # Optional monitoring must not falsify delivery or stop capture.
                self.monitor.disable()

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
        if not local:
            self.pending_remote_transcripts = getattr(self, 'pending_remote_transcripts', 0) + 1
        try:
            remaining = getattr(self, 'response_deadline', time.monotonic() + 45) - time.monotonic()
            text = await asyncio.wait_for(reader.read_all(), max(.1, min(45, remaining)))
        except Exception:
            self.events.append({'event_id': f'livekit/stream-error/{len(self.events)}',
                'event_type': 'transcript.incomplete', 'source': 'livekit.lk.transcription'})
            return
        finally:
            if not local:
                self.pending_remote_transcripts -= 1
        if len(text) > 32_000:
            self.error = 'LiveKit transcript exceeded capture limit.'
            return
        final = attributes.get('lk.transcription_final') == 'true'
        speaker = 'caller' if local else 'remote'
        key = (speaker, segment_id)
        revision = hashlib.sha256(f'{text}/{final}'.encode()).hexdigest()[:16]
        event_id = f'livekit/transcript/{speaker}/{segment_id}/{revision}'
        event = {'event_id': event_id,
                'event_type': 'asr.final' if local and final else 'asr.partial' if local else 'transcript.remote',
                'source': 'livekit.lk.transcription', 'text': text, 'final': final,
                'segment_id': segment_id, 'speaker': speaker, 'received_at_ms': self.now_ms(),
                'clock_origin': 'cae_monotonic_since_transport_creation',
                'native_unit': 'unknown', 'native_clock_origin': 'unspecified_by_provider'}
        if track_id:
            event['transcribed_track_id'] = track_id
        _record_transcription(self, key, event)

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
        if self.noise_mixer is not None:
            from app.services.continuous_caller_audio import ContinuousCallerAudio
            rate = self.noise_mixer.rate
            self.source = rtc.AudioSource(rate, 1)
            self.track = rtc.LocalAudioTrack.create_audio_track('cae-controlled-caller', self.source)
            await self.room.local_participant.publish_track(self.track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            self.continuous_caller = ContinuousCallerAudio(source=self.source, mixer=self.noise_mixer,
                make_frame=lambda pcm: rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2),
                on_audio=lambda pcm, sr: self._monitor_audio('tester_to_target', pcm, sr,
                    media_activity=self.continuous_caller.speech_activity),
                on_error=lambda error: setattr(self, 'error', error), now_ms=self.now_ms,
                path=self.noise_artifact_dir / 'caller-continuous-sent.wav')
            self.continuous_caller.start()
        await asyncio.wait_for(self.ready.wait(), 20)

    def configure_noise(self, audio: bytes, condition: dict, artifact_dir: Path):
        from app.services.waylo_audio_conditions import ContinuousNoise
        self.noise_mixer = ContinuousNoise(audio, snr_db=condition['snr_db'], seed=condition['seed'],
            noise_type=condition.get('noise_type', 'white'), recording_sha256=condition.get('recording_sha256'))
        self.noise_artifact_dir = artifact_dir

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
                self._monitor_audio('target_to_tester', pcm, 24000)
        except Exception:
            self.error = 'LiveKit audio receive failed.'
        finally:
            await stream.aclose()

    async def send(self, audio: bytes, *, turn_id: str, reference: str, artifact_dir: Path):
        if not hasattr(self, 'sent_references'):
            self.sent_references = {}
        self.sent_references[turn_id] = {'turn_id': turn_id, 'reference_text': reference}
        if getattr(self, 'continuous_caller', None) is not None:
            return await self._send_continuous(audio, turn_id=turn_id, reference=reference, artifact_dir=artifact_dir)
        self.transcript_turn_id = turn_id
        self.monitor_turn_pair = int(turn_id.split('-')[-1])
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
        playout_origin = time.monotonic()
        failure = False
        try:
            for offset in range(0, len(pcm), chunk_bytes):
                # Pace the accepted frames instead of enqueuing a whole turn
                # ahead of local playout. Peer receipt remains unconfirmed.
                await asyncio.sleep(max(0, playout_origin + offset / (rate * 2) - time.monotonic()))
                chunk = pcm[offset:offset + chunk_bytes]
                await self.source.capture_frame(self.rtc.AudioFrame(chunk, rate, 1, len(chunk) // 2))
                sent.extend(chunk)
                self._monitor_audio('tester_to_target', chunk, rate)
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

    async def _send_continuous(self, audio: bytes, *, turn_id: str, reference: str, artifact_dir: Path):
        self.transcript_turn_id = turn_id
        self.monitor_turn_pair = int(turn_id.split('-')[-1])
        rate, pcm = read_wav(audio)
        if rate != self.noise_mixer.rate:
            raise WayloError('Tester sample rate changed within the call.')
        speech = self.continuous_caller.enqueue(pcm)
        frame_samples = rate // 50
        intended_samples = (len(pcm) // 2 + frame_samples - 1) // frame_samples * frame_samples
        failure = True
        try:
            await asyncio.shield(speech.done)
            failure = False
        except asyncio.CancelledError:
            speech.done.cancel()
            # Stop the only writer before finalizing the partial utterance WAV.
            await self.continuous_caller.close()
            raise
        except Exception:
            pass
        finally:
            sent_wav = pcm_wav(bytes(speech.accepted), rate)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / f'{turn_id}-sent.wav').write_bytes(sent_wav)
            observation = {'event_id': f'{turn_id}/sent', 'event_type': 'tester.audio.sent', 'source': 'livekit.AudioSource',
                'turn_id': turn_id, 'reference_text': reference, 'sample_rate_hz': rate, 'channels': 1,
                'intended_samples': intended_samples, 'accepted_samples': len(speech.accepted) // 2,
                'source_speech_samples': len(pcm) // 2,
                'audio_sha256': hashlib.sha256(sent_wav).hexdigest(), 'started_at_ms': speech.started_ms,
                'ended_at_ms': speech.ended_ms if speech.ended_ms is not None else self.now_ms(),
                'clock_origin': 'cae_monotonic_since_transport_creation', 'clock_origin_utc': self.origin_utc,
                'duration_ms': len(speech.accepted) / (rate * 2) * 1000,
                'delivery': 'partial_or_unconfirmed' if failure else 'local_queue_drained', 'peer_receipt': 'unknown',
                'clipped_or_dropped_at_peer': 'unknown', 'audio_condition': self.noise_mixer.metadata(),
                'overlap_with_observed_target_speech': time.monotonic() - self.last_speech < .2}
            self.events.append(observation)
        if failure:
            raise WayloError('Tester audio send failed; accepted samples were retained for review.')
        return observation, sent_wav

    async def wait_response(self, previous_speech: int, *, timeout: float = 45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error:
                raise WayloError(self.error)
            now = time.monotonic()
            if self.speech_count > previous_speech:
                state = getattr(self, 'agent_state', None)
                # Speaking/thinking may include gaps between synthesized clauses.
                # A stale listening state is not proof the new reply has ended.
                fresh_listening = state in {'listening', 'idle'} and getattr(self, 'agent_state_updated', 0) >= self.last_speech
                quiet_window = 1.2 if fresh_listening else 2.5
                if (state not in {'speaking', 'thinking', 'initializing'}
                        and now - self.last_speech >= quiet_window
                        and now - getattr(self, 'last_remote_transcript', 0) >= .5
                        and not getattr(self, 'pending_remote_transcripts', 0)):
                    return
            await asyncio.sleep(.05)
        raise WayloError('Target response speech/silence boundary was not observed before timeout.')

    async def wait_caller_slot(self, *, timeout=45):
        # Generation/TTS can take seconds after the preceding boundary. Recheck
        # immediately before sending rather than talking over a resumed clause.
        deadline = time.monotonic() + timeout
        while not self.speech_count and getattr(self, 'agent_state', None) in {'speaking', 'thinking', 'initializing'}:
            if self.error:
                raise WayloError(self.error)
            if time.monotonic() >= deadline:
                raise WayloError('Target did not become ready for caller speech before timeout.')
            await asyncio.sleep(.05)
        if self.speech_count:
            await self.wait_response(max(0, self.speech_count - 1), timeout=max(.1, deadline - time.monotonic()))

    async def close(self):
        try:
            if getattr(self, 'continuous_caller', None) is not None:
                await self.continuous_caller.close()
                self.events.append({'event_id': 'caller/background-noise', 'event_type': 'tester.background_noise.sent',
                    'source': 'livekit.AudioSource', 'audio_condition': self.continuous_caller.metadata(),
                    'clock_origin': 'cae_monotonic_since_transport_creation', 'clock_origin_utc': self.origin_utc})
        finally:
            try:
                if self.source:
                    await self.source.aclose()
            finally:
                try:
                    # AudioStream.aclose waits for the SDK's receive task/EOS.
                    # Disconnect first so cancelling/joining our readers cannot
                    # wait on a room disconnect scheduled after that same join.
                    await asyncio.wait_for(self.room.disconnect(), 5)
                finally:
                    tasks = tuple(self.tasks)
                    for task in tasks:
                        task.cancel()
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)


async def run_waylo_call(*, target: dict, correlation_id: str, scenario: dict, suite_id: str,
                         artifact_dir: Path, max_exchanges: int, timeout_seconds: int,
                         next_utterance: Callable, synthesize: Callable,
                         event_observer: Callable | None = None,
                         peer: LiveKitVoicePeer | None = None, client: WayloClient | None = None,
                         credential_lease=None, execution_run_id: str | None = None,
                         conversation_id: str | None = None, monitor_runtime=None):
    client = client or WayloClient(target, credential_lease=credential_lease)
    call = None
    guard = None
    local_evidence = {}
    try:
        if credential_lease is None:
            return await _run_waylo_call(target=target, correlation_id=correlation_id, scenario=scenario,
                suite_id=suite_id, artifact_dir=artifact_dir, max_exchanges=max_exchanges,
                timeout_seconds=timeout_seconds, next_utterance=next_utterance, synthesize=synthesize,
                event_observer=event_observer, peer=peer, client=client,
                execution_run_id=execution_run_id, conversation_id=conversation_id, monitor_runtime=monitor_runtime)

        async def monitor_authorization():
            while True:
                credential_lease.check()
                await asyncio.sleep(.1)

        call = asyncio.create_task(_run_waylo_call(target=target, correlation_id=correlation_id,
            scenario=scenario, suite_id=suite_id, artifact_dir=artifact_dir, max_exchanges=max_exchanges,
            timeout_seconds=timeout_seconds, next_utterance=next_utterance, synthesize=synthesize,
            event_observer=event_observer, peer=peer, client=client,
            execution_run_id=execution_run_id, conversation_id=conversation_id, monitor_runtime=monitor_runtime,
            local_evidence=local_evidence))
        guard = asyncio.create_task(monitor_authorization())
        await asyncio.wait((call, guard), return_when=asyncio.FIRST_COMPLETED)
        from app.services.waylo_connection import WayloConnectionError
        try:
            if guard.done():
                await guard
            credential_lease.check()
        except WayloConnectionError:
            # Stop transport first. Finalize ONLY already-sanitized local data;
            # never read the provider or retrieve a token after revocation.
            if not call.done():
                call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            if not local_evidence.get('bootstrap'):
                raise
            return _local_partial_result(local_evidence, target=target, scenario=scenario, suite_id=suite_id,
                artifact_dir=artifact_dir, execution_run_id=execution_run_id, conversation_id=conversation_id)
        return await call
    finally:
        for task in (call, guard):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (call, guard) if task), return_exceptions=True)
        # Cancellation can skip the capture block's finally; close here as well.
        await client.close()


async def _run_waylo_call(*, target: dict, correlation_id: str, scenario: dict, suite_id: str,
                          artifact_dir: Path, max_exchanges: int, timeout_seconds: int,
                          next_utterance: Callable, synthesize: Callable,
                          event_observer: Callable | None,
                          peer: LiveKitVoicePeer | None, client: WayloClient,
                          execution_run_id: str | None = None, conversation_id: str | None = None,
                          monitor_runtime=None, local_evidence: dict | None = None):
    peer = peer or LiveKitVoicePeer()
    bootstrap, before, capture, media_dialogs, turns = None, None, None, [], []
    error = None
    audio_tracks = []
    recording = None
    monitor = None

    def monitor_unavailable():
        if event_observer:
            event_observer({'speaker': 'Listener',
                'text': 'Continuous live monitoring is unavailable. The target call continues; captured turns remain available.',
                'frame_metadata': {'media_event': 'live_monitor_unavailable', 'transport': 'waylo_livekit'}})

    if isinstance(peer, LiveKitVoicePeer):
        try:
            monitor = LiveAudioMonitor(execution_run_id=execution_run_id, conversation_id=conversation_id,
                runtime=monitor_runtime, max_seconds=timeout_seconds + 30, on_unavailable=monitor_unavailable)
            peer.monitor = monitor
        except Exception:
            monitor_unavailable()
    after_items = []
    item_read_errors = {}
    prepared = {}
    pending_response = None
    steps = scenario.get('caller_steps')
    adaptive_closing = not steps and max_exchanges > 1
    closing_audio = None
    termination_reason = None
    scheduled_closing_reason = None
    hard_deadline = time.monotonic() + timeout_seconds
    peer.response_deadline = hard_deadline
    closing_reserve = closing_reserve_seconds(timeout_seconds)
    if local_evidence is not None:
        local_evidence.update(peer=peer, turns=turns, audio_tracks=audio_tracks,
                              after_items=after_items, item_read_errors=item_read_errors, references={})

    def audio_track(path, track_id, parties, audio, rate, start_ms=None):
        audio_tracks.append({'uri': str(path.resolve()), 'track_id': track_id, 'parties': parties,
            'mime_type': 'audio/wav', 'duration_ms': (len(audio) - 44) / (rate * 2) * 1000,
            **({'start_ms': start_ms, 'clock_origin': 'cae_monotonic_since_transport_creation',
                'clock_origin_utc': peer.origin_utc} if start_ms is not None else {})})

    def media_key(index, direction):
        return f'{conversation_id}:{index}:{direction}'

    def safe_text(text):
        turn_auth = bootstrap.get('turn') or {} if bootstrap else {}
        values = (*client.redaction_values, bootstrap.get('participantToken', '') if bootstrap else '',
                  turn_auth.get('credential', ''), turn_auth.get('username', ''))
        return clean_native(text, values)

    if steps:
        max_exchanges = min(max_exchanges, len(steps))
    try:
        if monitor:
            await monitor.start()  # Pre-register before TTS and remote bootstrap.
        async with asyncio.timeout(timeout_seconds):
            # Resolve tester media before creating a billable remote session.
            utterance = await next_utterance([], 1)
            audio = await synthesize(utterance, 1)
            first_rate, _ = read_wav(audio[0] if isinstance(audio, tuple) else audio)
            if adaptive_closing:
                # Prepare a truthful, non-transactional goodbye before starting
                # a remote call; the final seconds must not depend on an LLM.
                closing_audio = await synthesize(CLOSING_TEXT, max_exchanges)
                close_rate, _ = read_wav(closing_audio[0] if isinstance(closing_audio, tuple) else closing_audio)
                if close_rate != first_rate:
                    raise WayloError('Closing audio sample rate differs; no call started.')
            # All fixed caller turns are available before the remote write. Validate
            # even later fixtures now, rather than discovering a broken file mid-call.
            for step_index in range(2, max_exchanges + 1) if steps else []:
                text = await next_utterance([], step_index)
                prepared_audio = await synthesize(text, step_index)
                rate, _ = read_wav(prepared_audio[0] if isinstance(prepared_audio, tuple) else prepared_audio)
                if rate != first_rate:
                    raise WayloError('Tester sample rate changed within the call; no call started.')
                prepared[step_index] = (text, prepared_audio)
            barge_turn = scenario.get('waylo_test', {}).get('barge_in_turn')
            if barge_turn and barge_turn > max_exchanges:
                # A capped-out caller turn cannot interrupt this call or skip
                # its final sequential response/state observation.
                barge_turn = None
            if barge_turn and barge_turn <= max_exchanges and barge_turn not in prepared:
                next_text = await next_utterance([], barge_turn)
                prepared[barge_turn] = (next_text, await synthesize(next_text, barge_turn))
                rate, _ = read_wav(prepared[barge_turn][1][0] if isinstance(prepared[barge_turn][1], tuple) else prepared[barge_turn][1])
                if rate != first_rate:
                    raise WayloError('Tester sample rate changed within the call; no call started.')
            if scenario.get('waylo_test'):
                current = await client.request('GET', f'agents/{client.agent}')
                preset = current.get('presetId') if isinstance(current, dict) else None
                if preset in {'item_capture_assistant', 'catalog_order_assistant'}:
                    raise WayloError('Mike test contract conflicts with the active preset. Use a disposable catalog-aware notes-only agent; no call started.')
            first_audio, first_condition = audio if isinstance(audio, tuple) else (audio, {})
            if first_condition.get('kind') == 'noise':
                if not hasattr(peer, 'configure_noise'):
                    raise WayloError('This voice transport does not support continuous background noise; no call started.')
                peer.configure_noise(first_audio, first_condition, artifact_dir)
            bootstrap = await client.bootstrap(correlation_id)
            if local_evidence is not None:
                local_evidence['bootstrap'] = bootstrap
            try:
                before = validate_items(await client.request('GET', f'sessions/{bootstrap["sessionId"]}/items'))
            except WayloError:
                before = None
                item_read_errors['items_before'] = 'Pre-call items unavailable or invalid; no before snapshot retained.'
            before = clean_native(before, client.redaction_values)
            if local_evidence is not None:
                local_evidence['before'] = before
            await peer.connect(bootstrap)
            # Let an optional greeting finish before the first controlled caller turn.
            try:
                await peer.wait_response(0, timeout=5)
            except WayloError:
                if peer.error:
                    raise
                if peer.speech_count or getattr(peer, 'agent_state', None) in {'speaking', 'thinking', 'initializing'}:
                    # No greeting is optional; a greeting already in flight is not.
                    if not peer.speech_count and callable(getattr(peer, 'wait_caller_slot', None)):
                        await peer.wait_caller_slot(timeout=45)
                    else:
                        await peer.wait_response(0, timeout=45)
            if peer.speech_count:
                greeting = pcm_wav(bytes(peer.remote_pcm))
                artifact_dir.mkdir(parents=True, exist_ok=True)
                (artifact_dir / 'agent-greeting-received.wav').write_bytes(greeting)
                audio_track(artifact_dir / 'agent-greeting-received.wav', 'greeting', [1], greeting, 24000,
                            peer.remote_chunks[0]['received_at_ms'] if peer.remote_chunks else None)
                greeting_text = safe_text('\n'.join(text for key, text in peer.segments.items() if key[0] == 'remote' and text))
                if event_observer:
                    event_observer({'speaker': 'Agent', 'text': greeting_text or '[Greeting audio; transcript pending]',
                        'audio': greeting, 'direction': 'target_to_tester',
                        'frame_metadata': {'listener_media_key': media_key(1, 'target_to_tester')}})
                turns.append({'speaker': 'Agent', 'text': greeting_text or '[Greeting audio; transcript pending]',
                    'turn_index': 1, 'direction': 'target_to_tester', 'frame_metadata': {'greeting': True}})
            for index in range(1, max_exchanges + 1):
                closing_reason, scheduled_closing_reason = scheduled_closing_reason, None
                if closing_reason:
                    utterance, audio = CLOSING_TEXT, closing_audio
                elif adaptive_closing and closing_due(index, max_exchanges, hard_deadline - time.monotonic(), closing_reserve):
                    utterance, audio = CLOSING_TEXT, closing_audio
                    closing_reason = 'exchange_budget_goodbye' if index >= max_exchanges else 'time_budget_goodbye'
                elif adaptive_closing and caller_has_closed(utterance):
                    closing_reason = 'caller_goodbye'
                if index != barge_turn and callable(getattr(peer, 'wait_caller_slot', None)):
                    await peer.wait_caller_slot(timeout=min(45, max(.1, hard_deadline - time.monotonic())))
                if (adaptive_closing and not closing_reason
                        and closing_due(index, max_exchanges, hard_deadline - time.monotonic(), closing_reserve)):
                    utterance, audio, closing_reason = CLOSING_TEXT, closing_audio, 'time_budget_goodbye'
                turn_id = f'caller-{index}'
                previous = peer.speech_count
                offset = pending_response['offset'] if pending_response else len(peer.remote_pcm)
                previous_segments = pending_response['segments'] if pending_response else dict(peer.segments)
                response_index = pending_response['first_index'] if pending_response else index
                response_to = [f'caller-{n}' for n in range(response_index, index + 1)]
                audio_bytes, audio_condition = audio if isinstance(audio, tuple) else (audio, {})
                rate, _ = read_wav(audio_bytes)
                if rate != first_rate:
                    raise WayloError('Tester sample rate changed within the call.')
                observation, sent_wav = await peer.send(audio_bytes, turn_id=turn_id, reference=utterance,
                                                        artifact_dir=artifact_dir)
                observation['audio_condition'] = {**audio_condition, **observation.get('audio_condition', {})}
                audio_track(artifact_dir / f'{turn_id}-sent.wav', turn_id, [0], sent_wav, rate,
                            observation.get('started_at_ms'))
                safe_utterance = safe_text(utterance)
                if local_evidence is not None:
                    local_evidence['references'][turn_id] = safe_utterance
                if event_observer:
                    event_observer({'speaker': 'Caller', 'text': safe_utterance, 'audio': sent_wav,
                                    'direction': 'tester_to_target', 'llm_output': safe_utterance,
                                    'frame_metadata': {'listener_media_key': media_key(index, 'tester_to_target')}})
                turns.append({'speaker': 'Caller', 'text': safe_utterance, 'turn_index': len(turns) + 1,
                              'direction': 'tester_to_target', 'frame_metadata': {'source_text': safe_utterance,
                              'duration_ms': observation['duration_ms'], 'audio_sha256': observation['audio_sha256'],
                              **({'caller_closing': closing_reason} if closing_reason else {})}})
                if index < max_exchanges and barge_turn == index + 1:
                    # Start the next caller turn during observed target speech,
                    # not after the usual observer-side silence boundary.
                    deadline = time.monotonic() + 15
                    while (peer.speech_count <= previous or time.monotonic() - getattr(peer, 'last_speech', 0) > .2) and time.monotonic() < deadline:
                        if peer.error:
                            raise WayloError(peer.error)
                        await asyncio.sleep(.02)
                    if peer.speech_count <= previous or time.monotonic() - getattr(peer, 'last_speech', 0) > .2:
                        raise WayloError('No target speech available for the controlled interruption.')
                    peer.events.append({'event_id': f'{turn_id}/barge-in-scheduled', 'event_type': 'tester.barge_in_scheduled',
                        'source': 'cae_controlled_tester', 'timestamp_ms': peer.now_ms(),
                        'clock_origin': 'cae_monotonic_since_transport_creation', 'target_interruption_handled': 'unknown'})
                    # Do not end/split the response at its first frame. Preserve
                    # one overlapping response window through the next silence
                    # boundary; individual reply attribution remains unknown.
                    pending_response = {'offset': offset, 'segments': previous_segments, 'first_index': response_index}
                    utterance, audio = prepared[index + 1]
                    continue
                else:
                    await peer.wait_response(previous)
                target_audio = pcm_wav(bytes(peer.remote_pcm[offset:]))
                (artifact_dir / f'agent-{response_index}-received.wav').write_bytes(target_audio)
                response_chunk = next((chunk for chunk in peer.remote_chunks if chunk['pcm_offset'] >= offset), None)
                audio_track(artifact_dir / f'agent-{response_index}-received.wav', f'agent-{response_index}',
                            [1], target_audio, 24000, response_chunk['received_at_ms'] if response_chunk else None)
                response = safe_text('\n'.join(text for key, text in peer.segments.items()
                                     if key[0] == 'remote' and text and text != previous_segments.get(key)))
                peer.events.append({'event_id': f'agent-{response_index}/received', 'event_type': 'target.audio.received',
                    'source': 'livekit.AudioStream', 'turn_id': f'agent-{response_index}',
                    'response_to_turn_ids': response_to, 'individual_response_attribution': 'unknown' if pending_response else 'sequential_observation',
                    'audio_sha256': hashlib.sha256(target_audio).hexdigest(), 'sample_rate_hz': 24000,
                    'duration_ms': (len(target_audio) - 44) / 48, 'channels': 1,
                    'clock_origin': 'cae_monotonic_since_transport_creation', 'clock_origin_utc': peer.origin_utc,
                    'observer_end_ms': peer.now_ms(), 'target_playback_ack': 'unknown'})
                candidates = [chunk for chunk in peer.remote_chunks if chunk['speech']
                              and chunk['pcm_offset'] >= offset and chunk['received_at_ms'] >= observation['ended_at_ms']]
                if candidates and not pending_response and not observation['overlap_with_observed_target_speech']:
                    peer.events.append({'event_id': f'caller-{index}/observer-latency', 'event_type': 'latency.observation',
                        'source': 'cae_livekit_observer', 'turn_id': turn_id,
                        'value_ms': round(candidates[0]['received_at_ms'] - observation['ended_at_ms'], 3),
                        'unit': 'ms', 'clock_origin': 'cae_monotonic_since_transport_creation',
                        'definition': 'Local caller queue drained to first received remote frame above RMS 150; not target-internal latency.'})
                if event_observer:
                    event_observer({'speaker': 'Agent', 'text': response or 'Target audio captured; transcript pending.',
                                    'audio': target_audio, 'direction': 'target_to_tester',
                                    'frame_metadata': {'listener_media_key': media_key(index, 'target_to_tester')}})
                turns.append({'speaker': 'Agent', 'text': response or '[Target audio captured; transcript pending]',
                              'turn_index': len(turns) + 1, 'direction': 'target_to_tester',
                              'frame_metadata': {'audio_sha256': hashlib.sha256(target_audio).hexdigest(),
                                                 'response_to_turn_ids': response_to, 'overlapping_response_window': bool(pending_response),
                                                 'transcript_source': 'livekit_remote_transcription'}})
                pending_response = None
                # Do not delay a scheduled interruption with an HTTP read.
                if index + 1 != barge_turn:
                    try:
                        items = validate_items(await client.request('GET', f'sessions/{bootstrap["sessionId"]}/items'))
                        after_items.append({'turn_id': f'caller-{index}', 'items': clean_native(items, client.redaction_values),
                                            'observed_at': datetime.now(UTC).isoformat()})
                    except WayloError:
                        item_read_errors[f'items_after_caller-{index}'] = 'Per-turn items unavailable or invalid; no snapshot retained for this read.'
                if closing_reason:
                    termination_reason = closing_reason
                    break
                if index < max_exchanges:
                    if adaptive_closing and closing_due(index + 1, max_exchanges, hard_deadline - time.monotonic(), closing_reserve):
                        utterance, audio = CLOSING_TEXT, closing_audio
                    elif index + 1 in prepared:
                        utterance, audio = prepared[index + 1]
                    else:
                        if adaptive_closing:
                            generation_budget = max(.001, min(30, hard_deadline - time.monotonic() - closing_reserve))
                            budget = asyncio.timeout(generation_budget)
                            try:
                                async with budget:
                                    utterance = await next_utterance(turns, index + 1)
                                    audio = await synthesize(utterance, index + 1)
                            except TimeoutError:
                                if not budget.expired():
                                    raise  # Do not disguise a provider failure as a normal goodbye.
                                utterance, audio = CLOSING_TEXT, closing_audio
                                scheduled_closing_reason = 'generation_budget_goodbye'
                        else:
                            utterance = await next_utterance(turns, index + 1)
                            audio = await synthesize(utterance, index + 1)
    except asyncio.CancelledError:
        error = 'Waylo call cancelled; partial local audio retained.'
        raise
    except Exception as exc:
        # SDK/provider errors can embed URLs/tokens. Export only a safe class/adapter message.
        error = str(exc) if isinstance(exc, WayloError) else f'Waylo call interrupted ({type(exc).__name__}).'
        termination_reason = 'hard_timeout' if isinstance(exc, TimeoutError) else 'execution_error'
    finally:
        try:
            await asyncio.wait_for(peer.close(), 10)
        except Exception:
            error = error or 'LiveKit cleanup failed; capture needs review.'
        # Transcription callbacks may finish during disconnect. A late safety
        # alert must not leave a normal-goodbye verdict on an unsafe receipt.
        if getattr(peer, 'error', None):
            error = error or peer.error
            termination_reason = 'execution_error'
        if bootstrap and hasattr(peer, 'events'):
            peer.events.append({'event_id': 'tester/call-ended', 'event_type': 'tester.call_ended',
                'source': 'cae_controlled_tester', 'reason': termination_reason or 'script_or_exchange_cap',
                'business_completion': 'not_inferred', 'timestamp_ms': peer.now_ms(),
                'clock_origin': 'cae_monotonic_since_transport_creation'})
        continuous = getattr(peer, 'continuous_caller', None)
        if continuous is not None and continuous.error:
            error = error or continuous.error
        if continuous is not None and continuous.accepted_samples:
            # A whole-call caller track replaces its turn slices in fallback
            # playback; the individual sent WAVs remain available for ASR audit.
            audio_tracks[:] = [track for track in audio_tracks if track.get('parties') != [0]]
            audio_tracks.append({'uri': str(continuous.path.resolve()), 'track_id': 'caller-continuous',
                'parties': [0], 'mime_type': 'audio/wav',
                'duration_ms': continuous.accepted_samples / continuous.mixer.rate * 1000,
                'start_ms': continuous.started_ms, 'clock_origin': 'cae_monotonic_since_transport_creation',
                'clock_origin_utc': peer.origin_utc, 'audio_condition': continuous.metadata()})
        if monitor:
            wav, metadata = await monitor.finish(partial=bool(error))
            if wav:
                path = artifact_dir / 'conversation-continuous.wav'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(wav)
                metadata.update(audio_tracks=audio_tracks,
                    start_ms=round((monitor.recording.origin - peer.origin) * 1000, 3),
                    clock_origin='cae_monotonic_since_transport_creation', clock_origin_utc=peer.origin_utc)
                if continuous is not None:
                    metadata['background_noise'] = continuous.metadata()
                recording = {'uri': str(path.resolve()), 'recording_url': str(path.resolve()),
                    'mime_type': 'audio/wav', 'sha256': hashlib.sha256(wav).hexdigest(),
                    'duration_ms': metadata['duration_ms'], 'bytes_captured': len(wav),
                    'transport': 'waylo_livekit', 'metadata': metadata}
                if local_evidence is not None:
                    local_evidence['recording'] = recording
                if event_observer:
                    event_observer({'speaker': 'Conversation', 'text': 'Continuous local call recording.',
                                    'audio': wav, 'frame_metadata': {
                                        **{k: v for k, v in metadata.items() if k != 'audio_tracks'},
                                        'media_event': 'continuous_recording'}})
    try:
        if not bootstrap:
            raise WayloError(error or 'No Waylo session was created.')
        # Re-read complete data, not just cached preview, to include late worker flushes.
        # Stable snapshots are provisional; provider lifecycle is not our authority.
        previous_digest = None
        for _ in range(6):
            try:
                capture = await client.capture(bootstrap['sessionId'], origin='cae_ai_tester')
            except Exception:
                # A completed RTC call must survive unavailable/invalid provider
                # reads. Keep any earlier successful snapshot; otherwise retain
                # only bootstrap identity, NOT invented provider observations.
                capture_error = 'Final Waylo provider capture unavailable; local RTC evidence retained. Reconnect and refresh this run to retry capture.'
                error = error or capture_error
                if capture is None:
                    capture = {'target_id': target['id'], 'origin': 'cae_ai_tester',
                        'session': {'id': bootstrap['sessionId'],
                            'workspaceId': target['connection']['workspace_id'],
                            'agentId': target['connection']['waylo_agent_id'],
                            'createdAt': peer.origin_utc, 'metadata_source': 'cae_local_bootstrap_identity'},
                        'transcripts': [], 'unavailable': {source: capture_error for source in
                            ('session', 'transcripts', 'events', 'items', 'agent_version')}}
                capture['capture_error'] = capture_error
                break
            digest = hashlib.sha256(str(capture).encode()).hexdigest()
            if digest == previous_digest and capture['session'].get('endedAt'):
                break
            previous_digest = digest
            await asyncio.sleep(.5)
        turn_credentials = bootstrap.get('turn') or {}
        secret_values = (bootstrap['participantToken'], *client.redaction_values,
                         turn_credentials.get('credential', ''), turn_credentials.get('username', ''))
        # Scrub every evidence channel, including opaque strings in backend
        # before/after snapshots and native source, not only RTC events.
        capture.setdefault('unavailable', {}).update(item_read_errors)
        capture = clean_native(capture, secret_values)
        before = clean_native(before, secret_values)
        after_items = clean_native(after_items, secret_values)
        media_events = clean_native(peer.events, secret_values)
        for event in media_events:
            event.update(target_id=target['id'], session_id=bootstrap['sessionId'])
        try:
            exported = project_capture(capture, voice_events=media_events, before_items=before,
                                   suite_id=suite_id, scenario_id=scenario['id'], media_dialogs=media_dialogs,
                                   test_expectations=scenario.get('waylo_test'), after_items=after_items,
                                   execution_run_id=execution_run_id, conversation_id=conversation_id,
                                   rtc_turns=clean_native(turns, secret_values) if capture.get('capture_error') and not capture['transcripts'] else None,
                                   media_capture={'status': 'retained_locally' if recording or audio_tracks else 'not_captured',
                                       'captured_tracks': 1 if recording else len(audio_tracks), 'included_tracks': 0,
                                       'omitted_tracks': 1 if recording else len(audio_tracks),
                                       'media_scope': 'continuous_duplex' if recording else 'captured_tracks_only',
                                       'reason': 'audio_opt_in_required' if recording or audio_tracks else 'media_unavailable'})
        except Exception:
            # A malformed/oversized native source must not erase the completed
            # RTC call. Keep only sanitized local evidence, not invalid native
            # fields or provider-derived state that could break projection again.
            safe_references = {event['turn_id']: event['reference_text'] for event in media_events
                if isinstance(event.get('turn_id'), str) and isinstance(event.get('reference_text'), str)}
            return _local_partial_result({'bootstrap': bootstrap, 'peer': peer,
                'turns': clean_native(turns, secret_values), 'audio_tracks': audio_tracks,
                'recording': recording, 'after_items': [], 'references': safe_references},
                target=target, scenario=scenario, suite_id=suite_id, artifact_dir=artifact_dir,
                execution_run_id=execution_run_id, conversation_id=conversation_id,
                error='Waylo provider evidence could not be projected; partial local RTC evidence retained. Reconnect and refresh this run.')
        # Native finals remain receipts, not the tester's intended utterance. Both are retained.
        transcript = '\n'.join(f'{"Caller" if line["role"] == "user" else "Agent"}: {line["text"]}'
                               for line in capture['transcripts'])
        if capture.get('capture_error') and not capture['transcripts']:
            transcript = '\n'.join(f'{turn["speaker"]}: {turn["text"]}' for turn in clean_native(turns, secret_values))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / 'agent-received.wav').write_bytes(pcm_wav(bytes(peer.remote_pcm)))
        return {'turns': clean_native(turns, secret_values), 'transcript': transcript, 'ietf_vcon_export': exported['vcon'],
                'evidence_coverage': exported['coverage'], 'waylo_capture': capture,
                'configuration_check': exported['configuration_check'], 'action_trace': [],
                'final_state': {'evidence_scope': 'waylo_capture', 'tester_error': error} if error else {},
                'verdict': 'failed' if error else 'needs_review', 'score': None, 'latency_marks': [],
                'evaluation_report': {}, 'recording': recording, 'local_audio_tracks': audio_tracks,
                'audio_session': {'transport': 'waylo_livekit', 'session_id': bootstrap['sessionId']}}
    finally:
        await client.close()


def _local_partial_result(state: dict, *, target: dict, scenario: dict, suite_id: str,
                          artifact_dir: Path, execution_run_id: str | None, conversation_id: str | None,
                          error: str | None = None) -> dict:
    """No credential access, HTTP, or unfiltered native text after revocation."""
    bootstrap, peer = state['bootstrap'], state['peer']
    turn_auth = bootstrap.get('turn') or {}
    secrets = (bootstrap.get('participantToken', ''), turn_auth.get('credential', ''), turn_auth.get('username', ''))
    error = error or 'Waylo authorization ended; partial local RTC evidence retained. Reconnect to refresh provider evidence.'
    capture = {'target_id': target['id'], 'origin': 'cae_ai_tester', 'capture_error': error,
        'session': {'id': bootstrap['sessionId'], 'createdAt': peer.origin_utc,
                    'metadata_source': 'cae_local_bootstrap_identity'},
        'transcripts': [], 'unavailable': {key: error for key in ('session', 'transcripts', 'events', 'items', 'agent_version')}}
    capture['unavailable'].update(state.get('item_read_errors', {}))
    # Peer events include unsanitized remote transcription revisions. Retain only
    # typed local media/timing observations, never raw strings or unknown keys.
    allowed = {'tester.audio.sent', 'target.audio.received', 'latency.observation', 'tester.barge_in_scheduled'}
    numeric = {'duration_ms', 'started_at_ms', 'ended_at_ms', 'intended_samples', 'accepted_samples',
               'sample_rate_hz', 'channels', 'observer_end_ms', 'value_ms', 'timestamp_ms'}
    events = []
    for index, event in enumerate(peer.events):
        if event.get('event_type') not in allowed:
            continue
        safe = {'event_id': f'local-rtc/{index}', 'event_type': event['event_type'],
                'source': 'cae_local_rtc_partial', 'target_id': target['id'], 'session_id': bootstrap['sessionId'],
                'clock_origin': 'cae_monotonic_since_transport_creation'}
        safe.update({key: value for key, value in event.items() if key in numeric and type(value) in {int, float}})
        # Caller IDs come from our own accepted, already-redacted reference map.
        turn_id = event.get('turn_id')
        if isinstance(turn_id, str) and turn_id in state['references']:
            safe.update(turn_id=turn_id, reference_text=state['references'][turn_id])
        events.append(safe)
    turns = clean_native(state['turns'], secrets)
    recording = state.get('recording')
    tracks = list(state['audio_tracks'])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    received_path = artifact_dir / 'agent-received.wav'
    received_path.write_bytes(pcm_wav(bytes(peer.remote_pcm)))
    # send() can retain accepted caller samples in its finally block before it
    # returns a turn observation. Include those exact adapter-owned WAVs too.
    retained = {track['uri'] for track in tracks}
    for path in artifact_dir.glob('caller-*-sent.wav'):
        if any(track.get('track_id') == 'caller-continuous' for track in tracks):
            break
        number = path.stem.removeprefix('caller-').removesuffix('-sent')
        if not number.isdigit() or int(number) < 1 or str(path.resolve()) in retained:
            continue
        try:
            rate, pcm = read_wav(path.read_bytes())
        except WayloError:
            continue
        tracks.append({'uri': str(path.resolve()), 'track_id': f'caller-{number}',
                       'parties': [0], 'mime_type': 'audio/wav', 'duration_ms': len(pcm) / (rate * 2) * 1000})
    if peer.remote_pcm and (not recording or (recording.get('metadata') or {}).get('partial')):
        # Cancellation can occur before the in-flight agent turn is segmented.
        # Retain all received target samples without duplicating its old slices
        # or claiming individual-turn attribution/clock alignment.
        tracks = [track for track in tracks if track.get('parties') == [0]] + [{
            'uri': str(received_path.resolve()), 'track_id': 'target-aggregate-partial',
            'parties': [1], 'mime_type': 'audio/wav', 'duration_ms': len(peer.remote_pcm) / 48}]
        if recording:
            recording = {**recording, 'metadata': {**recording.get('metadata', {}), 'audio_tracks': tracks}}
    exported = project_capture(clean_native(capture, secrets), rtc_turns=turns,
        voice_events=clean_native(events, secrets), before_items=clean_native(state.get('before'), secrets),
        after_items=clean_native(state['after_items'], secrets), suite_id=suite_id, scenario_id=scenario['id'],
        test_expectations=scenario.get('waylo_test'), execution_run_id=execution_run_id, conversation_id=conversation_id,
        media_capture={'status': 'retained_locally' if recording or tracks else 'not_captured',
                       'media_scope': 'partial_local_rtc', 'included_tracks': 0,
                       'omitted_tracks': 1 if recording else len(tracks), 'reason': 'audio_opt_in_required'})
    return {'turns': turns, 'transcript': '\n'.join(f'{turn["speaker"]}: {turn["text"]}' for turn in turns),
        'ietf_vcon_export': exported['vcon'], 'evidence_coverage': exported['coverage'],
        'configuration_check': exported['configuration_check'], 'action_trace': [],
        'final_state': {'evidence_scope': 'waylo_capture', 'tester_error': error},
        'verdict': 'failed', 'score': None, 'latency_marks': [], 'evaluation_report': {},
        'recording': recording, 'local_audio_tracks': tracks,
        'audio_session': {'transport': 'waylo_livekit', 'session_id': bootstrap['sessionId']}}
