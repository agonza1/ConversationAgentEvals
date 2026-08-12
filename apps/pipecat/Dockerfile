FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY apps/pipecat/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/pipecat/package.json apps/pipecat/package-lock.json /app/
RUN npm ci --omit=dev \
    && npm cache clean --force

COPY apps/pipecat /app

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8110"]
