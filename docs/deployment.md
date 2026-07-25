# Deployment

The default production shape uses separate API, web, and Pipecat services so each has one
public port, health check, and scaling policy. Deploy only the services a target path needs;
the deterministic benchmark and saved-evidence paths do not require Pipecat.

## Security boundary

Do not expose the API directly to untrusted clients. Several product and execution endpoints
accept `user_id` or project ownership fields from the request; those fields scope data but are
not request authentication.

Put the API behind an authenticated ingress, API gateway, or identity-aware proxy that:

- authenticates the caller and supplies trusted identity;
- rejects or replaces caller-provided ownership fields;
- restricts CORS to the deployed web origin;
- rate-limits execution and provider-backed routes;
- keeps provider credentials and internal service tokens server-side.

Cloud Run services require authentication by default. Do not add
`--allow-unauthenticated` until an application authentication boundary is implemented and
verified.

## Data and secrets

- Use PostgreSQL through `DATABASE_URL`. The SQLite fallback is local-development storage and
  is ephemeral on Cloud Run instances.
- Store `DATABASE_URL`, provider credentials, and `REFERENCE_AGENT_INTERNAL_TOKEN` in the
  platform secret manager, not an env file or image.
- Give the API and Pipecat services the same internal token when the reference voice path is
  enabled.
- Keep generated artifacts in durable object storage before relying on them for retention or
  audit requirements.

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

Deploy the web and Pipecat containers as separate services. Prefer private service-to-service
access for Pipecat; only the web ingress should be browser-facing.

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
