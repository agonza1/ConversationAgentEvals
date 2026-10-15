# Background noise recordings

These bundled presets are 20-second excerpts of CC0 recordings, converted to mono
PCM16 WAV at 24 kHz. Calls never download external audio. `manifest.json` records
the source URLs, credits, license, excerpt, and SHA-256 hashes of both the source
download and the bundled WAV.

- Office / call center: [Office Ambience.wav by DiArchangeli](https://freesound.org/people/DiArchangeli/sounds/108695/), an office soundscape with typing, telephone sounds, and voices. This is an office ambience proxy, not a recording of a specific call center.
- Café: [Coffee shop ambience, remastered by C_Rogers](https://freesound.org/people/C_Rogers/sounds/453074/), originally recorded by [waweee](https://freesound.org/people/waweee/sounds/370973/).
- Street traffic: [Chicago Traffic by craigsmith](https://freesound.org/people/craigsmith/sounds/817138/).

Each source page identifies its recording as [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

To rebuild an excerpt using FFmpeg 7.1 and the manifest's `download` URL:

```sh
ffmpeg -i source.mp3 -ss 5 -t 20 -ac 1 -ar 24000 \
  -af 'lowpass=f=7000,afade=t=in:d=0.02,afade=t=out:st=19.98:d=0.02' \
  -c:a pcm_s16le output.wav
```

The low-pass filter allows safe playback at 16 kHz. Short fades reduce loop-boundary
clicks. The mixer chooses a seeded offset, loops the excerpt as needed, and scales
it once to the requested signal-to-noise ratio against the first caller audio (the
first TTS chunk for the streaming built-in agent). It records
the actual recording hash, offset, seed, RMS levels, and clipping count alongside
the sent-audio evidence. The preset manifest is validated before playback; Waylo
scenario contracts also freeze the recording hash when queued.

The same opt-in control is available for all live voice tests: the built-in local
agent, public Pipecat Daily demo, SignalWire demo, and Waylo. It does not alter text
tests or saved replays. No new service or model is required.

One continuous caller microphone stream carries the noise during the greeting,
caller speech, target responses, and silence between turns. Its gain stays fixed;
after a greeting lead-in, it is calibrated once more at the actual first-speech
cursor, then stays fixed for subsequent turns. Lead-in calibration is retained in metadata.
SNR is a calibration reference, not an instantaneous ratio during silence. The
same mixed PCM feeds the live monitor and recording. Waylo also retains a bounded
raw caller WAV if monitoring fails. SignalWire loops a seeded 20-second noise bed.
White noise remains
a seeded generated baseline. Neither preset type restarts at a turn boundary.
