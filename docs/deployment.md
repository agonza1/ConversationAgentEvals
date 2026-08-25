# Deployment

The default production shape uses separate API, web, and Pipecat services so each has one
port, health check, and scaling policy. Deploy only the services a target path needs;
the deterministic benchmark and saved-evidence paths do not require Pipecat.

The current web client makes browser-side requests to the configured public API base. A
production deployment therefore needs either:

- a browser-reachable authenticated API or API-gateway endpoint; or
- one browser-facing web/reverse-proxy origin that routes API requests to the private API
  service and is supplied as the frontend API base.

Pipecat's HTTP service can remain private behind the API. Browser live-listener media still
requires browser-reachable ICE/TURN infrastructure as documented in `environment.md`.

## Security boundary

Do not expose the API directly to unauthenticated, untrusted clients. Several product and
execution endpoints accept `user_id` or project ownership fields from the request; those
fields scope data but are not request authentication.

Put the API behind an authenticated ingress, API gateway, identity-aware proxy, or trusted
same-origin reverse proxy that:

- authenticates the caller and supplies trusted identity;
- rejects or replaces caller-provided ownership fields;
- restricts CORS to the deployed web origin when the API uses a separate origin;
- rate-limits execution and provider-backed routes;
- keeps provider credentials and internal service tokens server-side.

Cloud Run services require authentication unless unauthenticated invocation is explicitly
allowed. Do not add `--allow-unauthenticated` to the API until an application authentication
boundary is implemented and verified. If the browser reaches the API through Cloud Run or a
gateway, ensure that ingress supplies the required authenticated identity; the CAE frontend
does not itself mint Cloud Run identity tokens.

## Data and secrets

- Use PostgreSQL through `DATABASE_URL`. The SQLite fallback is local-development storage and
  is ephemeral on Cloud Run instances.
- Store `DATABASE_URL`, provider credentials, and `REFERENCE_AGENT_INTERNAL_TOKEN` in the
  platform secret manager, not an env file or image.
- Give the API and Pipecat services the same internal token when the reference voice path is
  enabled.
- Use provider API credentials for hosted model and upstream ASSERT judging. The local Codex
  OAuth callback/token store is a development workflow, not a hosted authentication design.
- Keep generated artifacts in durable object storage before relying on them for retention or
  audit requirements.

`APP_ENV=production` disables the development-only synthetic ASSERT sidecar. The separate
execution-conversation judge route remains mounted, but upstream judging still requires
`ASSERT_UPSTREAM_JUDGE_ENABLED=1`, an allowed model, and provider credentials.

## Build the API image

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export ARTIFACT_REPOSITORY=conversation-agent-evals
export IMAGE="${GOOGLE_CLOUD_LOCATION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${ARTIFACT_REPOSITORY}/api:$(git rev-parse --short HEAD)"
```

Create the repository once:

```bash
gcloud artifacts repositories create "${ARTIFACT_REPOSITORY}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --repository-format docker \
  --location "${GOOGLE_CLOUD_LOCATION}"
```

Build and push:

```bash
gcloud builds submit \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --config deploy/cloudbuild-api.yaml \
  --substitutions "_IMAGE=${IMAGE}" \
  .
```

## Deploy the API

Cloud Run injects `PORT`; the API image starts Uvicorn on `${PORT:-8000}`.

```bash
gcloud run deploy conversation-agent-evals-api \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --image "${IMAGE}" \
  --no-allow-unauthenticated \
  --set-env-vars APP_ENV=production \
  --set-env-vars PIPECAT_SERVICE_URL=https://REPLACE_WITH_PIPECAT_SERVICE_URL
```

Attach secrets separately:

```bash
gcloud run services update conversation-agent-evals-api \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --set-secrets DATABASE_URL=database-url:latest \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest \
  --set-secrets REFERENCE_AGENT_INTERNAL_TOKEN=reference-agent-internal-token:latest
```

Deploy the web and Pipecat containers as separate services. Keep Pipecat private where
possible. Expose the API only through the authenticated browser-reachable boundary selected
above, or route it through the web/reverse-proxy origin. Configure the built frontend's
`NEXT_PUBLIC_API_BASE_URL` to that browser-reachable URL; an internal Cloud Run service URL is
not usable by the browser.

## Release checks

Before deployment:

```bash
npm run check:env
npm run lint:web
npm run build:web
npm run test:api
npm run test:benchmark-smoke
```

After deployment, use an authorized request to check the API:

```bash
API_URL="$(gcloud run services describe conversation-agent-evals-api \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --format 'value(status.url)')"

curl --fail --show-error --silent \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  "${API_URL}/health"
```

Expected response:

```json
{"status":"ok"}
```

Also verify one deterministic pass, one deterministic failure, saved-run retrieval, and an
artifact export. Run provider-backed voice or judge smoke tests only when those services are
part of the release; they can incur cost and are intentionally excluded from default CI.
