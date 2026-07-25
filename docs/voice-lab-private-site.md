# Voice Lab Private Site

This optional workflow builds a static, localhost-only review surface from seeded synthetic
voice-lab artifacts. It is useful for reviewing proof bundles without deploying the product or
exposing a public service.

## Build and run

From the repository root:

```bash
./scripts/voice_lab_private_deploy.sh build
./scripts/voice_lab_private_deploy.sh start
./scripts/voice_lab_private_deploy.sh status
./scripts/voice_lab_private_deploy.sh smoke
```

Open `http://127.0.0.1:18767/`.

The generated site is stored under `artifacts/voice-lab-private-site/current/`. The script
archives the previous version under `releases/`, uses a detached `tmux` session when available,
and otherwise records a background server PID and log in the same artifact tree.

Stop or roll back:

```bash
./scripts/voice_lab_private_deploy.sh stop
./scripts/voice_lab_private_deploy.sh rollback
```

Pass a `site-YYYYMMDDTHHMMSSZ` release name to `rollback` to select a specific snapshot.

## Scope

The site demonstrates deterministic contact-center proof bundles, transcript and event export,
and the generated HTML/JSON review artifacts.

It does not demonstrate live ASR or TTS, SIP, WebRTC media transport, recorded waveform capture,
real customer data, production credentials, or public deployment. The generated manifest lists
unsupported layers so the review artifact carries its own honesty boundary.
