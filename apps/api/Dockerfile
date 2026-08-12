FROM python:3.13-slim

WORKDIR /workspace

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl build-essential nodejs npm \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json /workspace/
COPY apps/web/package.json /workspace/apps/web/package.json
RUN npm ci

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN npx playwright install --with-deps chromium

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
