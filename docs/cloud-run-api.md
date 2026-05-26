# Cloud Run API Deployment

This is the bounded Cloud Run path for the FastAPI evaluation API. The web and Pipecat services should deploy as separate services so each container has one public port, one health check, and one scaling policy.

## Build

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export ARTIFACT_REPOSITORY=conversation-agent-evals
export IMAGE="${GOOGLE_CLOUD_LOCATION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${ARTIFACT_REPOSITORY}/api:$(git rev-parse --short HEAD)"
```

Create the Artifact Registry repository once per project:

```bash
gcloud artifacts repositories create "${ARTIFACT_REPOSITORY}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --repository-format docker \
  --location "${GOOGLE_CLOUD_LOCATION}" \
  --description "ConversationAgentEvals containers"
```

Build and push the API image:

```bash
gcloud builds submit \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --config deploy/cloudbuild-api.yaml \
  --substitutions "_IMAGE=${IMAGE}" \
  .
```

## Deploy

Cloud Run injects `PORT`; the API image starts Uvicorn on `${PORT:-8000}` so the same image still works locally.

```bash
gcloud run deploy conversation-agent-evals-api \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --image "${IMAGE}" \
  --allow-unauthenticated \
  --set-env-vars APP_ENV=production \
  --set-env-vars API_BASE_URL=https://REPLACE_WITH_SERVICE_URL \
  --set-env-vars PIPECAT_SERVICE_URL=https://REPLACE_WITH_PIPECAT_SERVICE_URL
```

Set secrets separately instead of committing them to env files:

```bash
gcloud run services update conversation-agent-evals-api \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --set-secrets DATABASE_URL=database-url:latest \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest
```

Use a persistent PostgreSQL database for `DATABASE_URL` in production, such as Cloud SQL with the Cloud Run service connected through its private IP or Cloud SQL connector. The API image includes the `psycopg2` DBAPI driver, so standard SQLAlchemy PostgreSQL URLs such as `postgresql://USER:PASSWORD@HOST:5432/DB_NAME` are supported. Without `DATABASE_URL`, the API falls back to local SQLite, which is ephemeral on Cloud Run instances.

## Smoke Check

After deployment, verify the public health endpoint before wiring the web app to the API:

```bash
API_URL="$(gcloud run services describe conversation-agent-evals-api \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_LOCATION}" \
  --format 'value(status.url)')"

curl --fail --show-error --silent "${API_URL}/health"
```

Expected response:

```json
{"status":"ok"}
```
