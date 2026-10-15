"""Finalize a completed TTS HTTP response without weakening fixture validation."""
from __future__ import annotations

import io
import wave


def finalize_tts_wav(audio: bytes) -> bytes:
    """Replace known streaming length markers with the observed PCM byte count.

    Kokoro may retain RIFF/data 0xffffffff lengths even with stream=False. This
    function is only for a successfully buffered provider response, never an
    uploaded fixture. Finite declarations must match; audio samples and metadata
    are preserved, and incomplete frames or unsupported formats fail closed.
    """
    invalid = 'TTS returned an incomplete or unsupported mono PCM16 WAV.'
    try:
        if (not isinstance(audio, bytes) or not 12 <= len(audio) <= 10_000_000
                or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE'):
            raise ValueError(invalid)
        unknown = 0xffffffff
        riff_size = int.from_bytes(audio[4:8], 'little')
        if riff_size != unknown and riff_size != len(audio) - 8:
            raise ValueError(invalid)
        finalized = bytearray(audio)
        position, data_size, seen_format = 12, None, False
        while position < len(audio):
            if position + 8 > len(audio):
                raise ValueError(invalid)
            kind = audio[position:position + 4]
            size = int.from_bytes(audio[position + 4:position + 8], 'little')
            start = position + 8
            if kind == b'fmt ':
                if seen_format or size < 16 or start + size > len(audio):
                    raise ValueError(invalid)
                seen_format = True
                tag = int.from_bytes(audio[start:start + 2], 'little')
                channels = int.from_bytes(audio[start + 2:start + 4], 'little')
                rate = int.from_bytes(audio[start + 4:start + 8], 'little')
                byte_rate = int.from_bytes(audio[start + 8:start + 12], 'little')
                alignment = int.from_bytes(audio[start + 12:start + 14], 'little')
                bits = int.from_bytes(audio[start + 14:start + 16], 'little')
                if (channels != 1 or rate not in {16000, 24000, 48000}
                        or bits != 16 or alignment != 2 or byte_rate != rate * 2):
                    raise ValueError(invalid)
                if tag == 0xfffe:
                    if size < 40:
                        raise ValueError(invalid)
                    extension_size = int.from_bytes(audio[start + 16:start + 18], 'little')
                    if (extension_size < 22 or 18 + extension_size != size
                            or int.from_bytes(audio[start + 18:start + 20], 'little') != 16):
                        raise ValueError(invalid)
            elif kind == b'data':
                if data_size is not None:
                    raise ValueError(invalid)
                if size == unknown:
                    if riff_size != unknown:
                        raise ValueError(invalid)
                    # Streaming data is terminal; the completed HTTP body bounds it.
                    size = len(audio) - start
                    finalized[position + 4:position + 8] = size.to_bytes(4, 'little')
                data_size = size
            elif size == unknown:
                raise ValueError(invalid)
            position = start + size + (size % 2)
            if position > len(audio):
                raise ValueError(invalid)
        if not data_size or data_size % 2:
            raise ValueError(invalid)
        if riff_size == unknown:
            finalized[4:8] = (len(audio) - 8).to_bytes(4, 'little')
        result = bytes(finalized)
        with wave.open(io.BytesIO(result), 'rb') as reader:
            if (reader.getcomptype() != 'NONE' or reader.getnchannels() != 1
                    or reader.getsampwidth() != 2 or reader.getframerate() not in {16000, 24000, 48000}):
                raise ValueError(invalid)
            pcm = reader.readframes(reader.getnframes())
            if len(pcm) != data_size or len(pcm) != reader.getnframes() * 2:
                raise ValueError(invalid)
        return result
    except (EOFError, wave.Error):
        raise ValueError(invalid) from None
