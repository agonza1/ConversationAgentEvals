FROM node:22-bookworm-slim AS signalwire-runtime

WORKDIR /runtime

COPY apps/api/signalwire-runtime/package.json apps/api/signalwire-runtime/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.13-slim

WORKDIR /workspace

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY --from=signalwire-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=signalwire-runtime /runtime/node_modules /workspace/node_modules

COPY apps/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps/api /workspace/apps/api
COPY apps/pipecat /workspace/apps/pipecat
COPY docs /workspace/docs
COPY scripts /workspace/scripts
RUN mkdir -p /workspace/storage

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/apps/api
ENV APP_ENV=development

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir /workspace/apps/api --host 0.0.0.0 --port ${PORT:-8000}"]
