"""Bounded, opt-in portable media; saved evidence never contains private paths.

Local media stays in the execution artifact tree. Default downloads carry track
metadata and an explicit omission manifest; include_audio either includes every
selected track within the 20 MB decoded-media budget or fails without truncation.
"""
from __future__ import annotations

import base64
import math
import re
import wave
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.vcon_interop import is_vcon_timestamp, normalize_vcon_sha512, vcon_content_hash

MAX_INLINE_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_TRACKS = 1000


class MediaExportError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def recording_manifest(dialog: list[dict[str, Any]]) -> dict[str, Any]:
    included = [d for d in dialog if d.get('type') == 'recording' and (d.get('body') or d.get('url'))]
    return {'status': 'inline' if any(d.get('body') for d in included) else 'portable' if included else 'not_captured',
            'included_tracks': len(included), 'captured_tracks': len(included), 'omitted_tracks': 0}


def _duration_ms(path: Path) -> float | None:
    try:
        with wave.open(str(path), 'rb') as audio:
            return audio.getnframes() * 1000 / audio.getframerate()
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return None


def _timing(track: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    duration = track.get('duration_ms')
    if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
        result['duration'] = duration / 1000
    if is_vcon_timestamp(track.get('start')):
        result['start'] = track['start']
    elif (track.get('clock_origin') == 'cae_monotonic_since_transport_creation'
          and is_vcon_timestamp(track.get('clock_origin_utc'))
          and type(track.get('start_ms')) in (int, float)
          and math.isfinite(track['start_ms']) and track['start_ms'] >= 0):
        # Only convert a producer's explicitly paired clock anchor; never subtract
        # provider timestamps from observer clocks or infer start from end/duration.
        origin = datetime.fromisoformat(track['clock_origin_utc'].replace('Z', '+00:00'))
        result['start'] = (origin + timedelta(milliseconds=track['start_ms'])).isoformat()
    return result


def _local_tracks(conversation: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    recording = conversation.get('recording') or {}
    metadata = recording.get('metadata') or {}
    # A correctly aligned duplex recording is the complete media source. Export
    # it once, not again alongside overlapping per-turn tracks.
    if metadata.get('scope') == 'continuous_duplex' and recording.get('uri'):
        return [{**metadata, 'uri': recording['uri'], 'parties': [0, 1],
                 'mime_type': recording.get('mime_type', 'audio/wav'),
                 'duration_ms': recording.get('duration_ms'), 'track_id': 'continuous-duplex'}]
    tracks = conversation.get('local_audio_tracks') or metadata.get('audio_tracks')
    if isinstance(tracks, list) and tracks:
        if len(tracks) > MAX_AUDIO_TRACKS or any(not isinstance(t, dict) for t in tracks):
            raise MediaExportError('Invalid or oversized local audio track manifest.')
        return deepcopy(tracks)
    uri = recording.get('uri') or recording.get('recording_url')
    if uri:
        scope = str(metadata.get('scope') or '')
        return [{**metadata, 'uri': uri, 'mime_type': recording.get('mime_type', 'audio/wav'),
                 'duration_ms': recording.get('duration_ms'), 'track_id': 'conversation-recording',
                 'parties': [1] if 'target' in scope else [0] if 'caller' in scope else [0, 1]}]
    # Recover all retained old Waylo turn files, not its truncated inline greeting.
    # Names and directory are exact adapter contracts, not a recursive file sweep.
    transport = (conversation.get('audio_session') or {}).get('transport')
    if transport != 'waylo_livekit':
        return []
    directory = (root / 'audio' / str(conversation.get('conversation_id') or '')).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir():
        return []
    tracks = []
    for path in directory.iterdir():
        match = re.fullmatch(r'(agent-greeting-received|caller-([1-9][0-9]*)-sent|agent-([1-9][0-9]*)-received)\.wav', path.name)
        if not match or not path.is_file():
            continue
        tracks.append({'uri': str(path), 'track_id': path.stem, 'mime_type': 'audio/wav',
                       'parties': [0] if path.name.startswith('caller-') else [1],
                       'duration_ms': _duration_ms(path),
                       '_order': 0 if 'greeting' in path.name else int(match[2] or match[3]) * 2 + (1 if match[3] else 0)})
    if len(tracks) > MAX_AUDIO_TRACKS:
        raise MediaExportError('Too many local audio tracks for portable export.')
    return sorted(tracks, key=lambda t: t['_order'])


def export_conversation_media(vcon: dict[str, Any], conversation: dict[str, Any], root: Path,
                              *, include_audio: bool) -> dict[str, Any]:
    exported = deepcopy(vcon)
    root = root.resolve()
    dialogs = exported.setdefault('dialog', [])
    # Keep indices stable when omitting payloads. These are standard recording
    # placeholders, not fabricated HTTPS links or incomplete/failed call dialogs.
    inline_indexes = []
    inline_bytes = 0
    for index, item in enumerate(dialogs):
        digest = item.get('content_hash')
        if isinstance(digest, str):
            try:
                item['content_hash'] = normalize_vcon_sha512(digest)
            except ValueError:
                pass  # final semantic validation rejects malformed hashes
        if item.get('type') == 'recording' and item.get('encoding') == 'base64url' and isinstance(item.get('body'), str):
            inline_indexes.append(index)
            inline_bytes += len(item['body']) * 3 // 4
    tracks = _local_tracks(conversation, root)
    omitted: list[dict[str, Any]] = []
    local: list[tuple[dict[str, Any], Path, int]] = []
    seen: set[Path] = set()
    for index, track in enumerate(tracks):
        uri = track.get('uri') or track.get('recording_url')
        path = Path(uri).resolve() if isinstance(uri, str) and uri and '://' not in uri and not uri.startswith('/api/') else None
        if path is None or not path.is_relative_to(root) or not path.is_file():
            if include_audio:
                raise MediaExportError('No run-local audio available for the complete inline export.')
            omitted.append({'track_id': str(track.get('track_id') or f'track-{index + 1}'), 'reason': 'local_media_unavailable'})
            continue
        if path in seen:
            continue
        seen.add(path)
        size = path.stat().st_size
        if size <= 0:
            raise MediaExportError('Local audio contains an empty track; refusing a partial export.')
        local.append((track, path, size))
    total = sum(size for _, _, size in local)
    if include_audio and total > MAX_INLINE_AUDIO_BYTES:
        raise MediaExportError(f'Complete inline audio exceeds the 20 MB limit ({len(local)} tracks, {total} bytes); use the metadata-only export. No tracks were truncated.', 413)
    if include_audio and not local and not inline_indexes and not any(d.get('url') for d in dialogs if d.get('type') == 'recording'):
        raise MediaExportError('No local recording available for inline export; use the metadata-only export.')
    if not include_audio or local:
        for index in inline_indexes:
            item = dialogs[index]
            for key in ('body', 'encoding', 'content_hash'):
                item.pop(key, None)
        if not local:
            omitted.extend({'track_id': f'dialog-{i}', 'reason': 'audio_opt_in_required'} for i in inline_indexes)
    elif inline_bytes > MAX_INLINE_AUDIO_BYTES:
        raise MediaExportError('Inline audio exceeds the 20 MB limit; use the metadata-only export.', 413)
    portable_tracks = []
    for index, (track, path, size) in enumerate(local):
        track_id = str(track.get('track_id') or f'track-{index + 1}')
        info = {'track_id': track_id, 'bytes': size, 'parties': track.get('parties', [0, 1]), **_timing(track)}
        if not include_audio:
            omitted.append({**info, 'reason': 'audio_opt_in_required'})
            continue
        # Defend against concurrent growth after stat, and bound actual reads.
        remaining = MAX_INLINE_AUDIO_BYTES - sum(t['bytes'] for t in portable_tracks)
        with path.open('rb') as stream:
            audio = stream.read(remaining + 1)
        if len(audio) > remaining:
            raise MediaExportError('Inline audio changed or exceeds the 20 MB limit; no partial export produced.', 413)
        if not audio:
            raise MediaExportError('Local audio changed during export; no partial export produced.')
        info['bytes'] = len(audio)
        item = {'type': 'recording', 'parties': info['parties'], 'mediatype': track.get('mime_type', 'audio/wav'),
                'encoding': 'base64url', 'body': base64.urlsafe_b64encode(audio).decode('ascii').rstrip('='),
                'content_hash': vcon_content_hash(audio), **_timing(track)}
        # Replacement avoids leaving a duplicate greeting placeholder.
        if index < len(inline_indexes):
            dialog_index = inline_indexes[index]
            dialogs[dialog_index] = item
        else:
            dialog_index = len(dialogs)
            dialogs.append(item)
        portable_tracks.append({**info, 'dialog': dialog_index, 'content_hash': item['content_hash'],
                                'clock_origin': track.get('clock_origin', 'not_recorded')})
    included = sum(1 for d in dialogs if d.get('type') == 'recording' and (d.get('body') or d.get('url')))
    manifest = {'status': 'inline' if include_audio and any(d.get('body') for d in dialogs if d.get('type') == 'recording') else 'retained_locally' if local or inline_indexes else 'portable' if included else 'not_captured',
                'captured_tracks': len(local) if local else len(inline_indexes) + sum(1 for d in dialogs if d.get('type') == 'recording' and d.get('url')),
                'included_tracks': included, 'omitted_tracks': len(omitted),
                'local_audio_bytes': total, 'inline_audio_limit_bytes': MAX_INLINE_AUDIO_BYTES,
                'media_scope': 'continuous_duplex' if len(local) == 1 and local[0][0].get('scope') == 'continuous_duplex' else 'captured_tracks_only',
                'full_media_coverage': 'all_retained_local_tracks_included' if include_audio and local and not omitted else 'not_established',
                'capture_completeness': 'partial' if any(t.get('partial') is True for t, _, _ in local) else
                                        'source_reported_continuous' if len(local) == 1 and local[0][0].get('scope') == 'continuous_duplex' and local[0][0].get('partial') is False else 'unknown',
                'tracks': portable_tracks, 'omissions': omitted}
    for attachment in exported.get('attachments') or []:
        if 'dialog' not in attachment and dialogs:
            attachment['dialog'] = 0  # normalize known legacy CAE native attachments
    for analysis in exported.get('analysis') or []:
        if analysis.get('schema') == 'cae-execution-evidence-v1' and isinstance(analysis.get('body'), dict):
            analysis['body']['recording'] = manifest
    return exported
