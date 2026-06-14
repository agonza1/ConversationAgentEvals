# Voice Lab Private Deployment Runbook

## Deployment target

- Target: repo-local static proof site generated from the seeded `ConversationAgentEvals` voice-lab runner.
- Access boundary: bind the HTTP server to `127.0.0.1` only. This keeps the demo private to the host session while still making it inspectable in a browser.
- Data policy: synthetic/demo data only. The bundle is generated from the deterministic contact-center proof plus the transcript-injected `/ask` loop.

This is the lowest-risk production shape available in the current runtime because `docker` and `gcloud` are unavailable here. Instead of pretending to ship a public SaaS surface, this runbook ships a private, inspectable proof environment with explicit boundaries and reproducible artifacts.

## Deploy

From the repo root:

```bash
./scripts/ensure-venv.sh apps/api/.venv apps/api/requirements.txt
apps/api/.venv/bin/python scripts/voice_lab_private_site.py build   --artifact-root artifacts/voice-lab   --site-root artifacts/voice-lab-private-site/current
apps/api/.venv/bin/python scripts/voice_lab_private_site.py serve   --site-root artifacts/voice-lab-private-site/current   --host 127.0.0.1   --port 18766
```

Expected private access path:

```text
http://127.0.0.1:18766/
```

The static site root is written to:

```text
artifacts/voice-lab-private-site/current/
```

The generated manifest and latest proof pointer are available at:

```text
artifacts/voice-lab-private-site/current/manifest.json
artifacts/voice-lab-private-site/current/voice-lab-proof-latest.json
```

## Smoke test

In a second shell while the server is running:

```bash
curl --fail --show-error --silent http://127.0.0.1:18766/ >/dev/null
apps/api/.venv/bin/python scripts/voice_lab_private_site.py smoke --base-url http://127.0.0.1:18766
```

Expected smoke result:

- `curl` exits `0`
- the smoke command prints a JSON summary with `scenario_count >= 1`

Inspect these proof files after the smoke run:

```text
artifacts/voice-lab-private-site/current/index.html
artifacts/voice-lab-private-site/current/bundle/manifest.json
artifacts/voice-lab/voice-lab-proof-latest.json
```

## Rollback / disable

- If the server is running in the foreground, stop it with `Ctrl-C`.
- If it was started in the background, stop that process explicitly.
- To remove the currently published private site, delete `artifacts/voice-lab-private-site/current/` after stopping the server.
- To invalidate the current proof pointer, regenerate the site or remove `artifacts/voice-lab-private-site/current/voice-lab-proof-latest.json`.

## Supported and unsupported layers

Supported in this deployment shape:

- deterministic contact-center scripted and fallback proof bundle capture
- transcript-injected `/ask` loop with citations, transcript export, and event export
- HTML report plus JSON manifest for operator review

Not supported yet:

- live ASR
- live TTS
- SIP trunking
- WebRTC media transport
- recorded audio waveform capture
- transcript-to-audio alignment
- real-customer data or production credentials

The site surfaces these unsupported layers directly from the generated manifest so QA can see the gap in the artifact, not just in the docs.
