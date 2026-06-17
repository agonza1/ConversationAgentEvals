# Voice Lab Private Deployment Runbook

## Decision for June 19

Keep the review path private and localhost-only.

Why this is the lowest-risk deployment-equivalent shape right now:

- `docker` is unavailable in this runtime, so the checked-in container path cannot be rebuilt or validated here.
- `gcloud` is unavailable in this runtime, so the documented Cloud Run path cannot be executed or smoke-tested here.
- The only checked-in external deployment material is for the API container, not for a full buyer-facing proof site with a safe private access boundary.
- A public tunnel or ad hoc host would create an unnecessary exposure path without adding proof quality.

The approved review surface for June 19 is therefore a repo-local static proof site bound to `127.0.0.1`, generated only from seeded synthetic/demo scenarios.

## Review surface

- Browser URL: `http://127.0.0.1:18767/`
- Canonical site root: `artifacts/voice-lab-private-site/current/`
- Handoff mirror root: `artifacts/voice-lab-private-site/parent-current/`
- HTML report: `artifacts/voice-lab-private-site/current/index.html`
- Site manifest: `artifacts/voice-lab-private-site/current/manifest.json`
- Latest proof JSON: `artifacts/voice-lab-private-site/current/voice-lab-proof-latest.json`
- Latest raw bundle manifest: `artifacts/voice-lab-private-site/current/bundle/manifest.json`
- Managed session name when `tmux` exists: `voice-lab-private-site`
- Background pidfile fallback: `artifacts/voice-lab-private-site/server.pid`
- Server log: `artifacts/voice-lab-private-site/server.log`
- Release snapshots for rollback: `artifacts/voice-lab-private-site/releases/`

`parent-current/` is a mirrored copy of `current/` so parent-card reviewers can keep using the previously shared artifact path.

## Start / publish

From the repo root:

```bash
./scripts/voice_lab_private_deploy.sh build
./scripts/voice_lab_private_deploy.sh start
```

What `build` does:

- ensures the API virtualenv exists
- generates a fresh seeded proof bundle under `artifacts/voice-lab/`
- publishes the static site to `artifacts/voice-lab-private-site/current/`
- mirrors the same site to `artifacts/voice-lab-private-site/parent-current/`
- archives the previous `current/` site under `artifacts/voice-lab-private-site/releases/site-<timestamp>/` before replacing it

What `start` does:

- serves the current site from a detached `tmux` session named `voice-lab-private-site` when `tmux` is available
- falls back to a background process tracked in `artifacts/voice-lab-private-site/server.pid` when `tmux` is unavailable
- binds only to `127.0.0.1`
- listens on port `18767`
- writes the HTTP server log to `artifacts/voice-lab-private-site/server.log`

To inspect status:

```bash
./scripts/voice_lab_private_deploy.sh status
```

## Smoke test

With the server running:

```bash
./scripts/voice_lab_private_deploy.sh smoke
```

This performs:

```bash
curl --fail --show-error --silent http://127.0.0.1:18767/ >/dev/null
apps/api/.venv/bin/python scripts/voice_lab_private_site.py smoke --base-url http://127.0.0.1:18767
```

Smoke output is saved to `artifacts/voice-lab-private-site/smoke-<timestamp>.json`.

Expected smoke result:

- `curl` exits `0`
- JSON output includes `scenario_count >= 1`
- JSON output includes the current `bundle_id`
- JSON output lists `unsupported_layers` so QA can verify what is intentionally out of scope

## Stop / rollback

Stop the private review server:

```bash
./scripts/voice_lab_private_deploy.sh stop
```

Rollback to the latest archived site snapshot:

```bash
./scripts/voice_lab_private_deploy.sh rollback
```

Rollback to a specific snapshot:

```bash
./scripts/voice_lab_private_deploy.sh rollback site-YYYYMMDDTHHMMSSZ
```

Rollback restores both `current/` and `parent-current/` from the chosen archived site. Start the server again after rollback if QA needs the reverted site live.

## Supported and unsupported layers

Supported in this deployment shape:

- deterministic contact-center scripted and fail-closed proof bundle capture
- transcript-injected `/ask` loop with citations, transcript export, and event export
- buyer-facing HTML report plus JSON manifests for operator review
- explicit unsupported-layer disclosure embedded in the artifact itself

Not supported in this proof:

- live ASR
- live TTS
- SIP trunking
- WebRTC media transport
- recorded audio waveform capture
- transcript-to-audio alignment
- real customer data processing
- production credentials
- public internet exposure

The site manifest surfaces the unsupported layers directly so QA can validate the current boundary from the artifact, not just from the runbook.
