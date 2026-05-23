FROM python:3.13-slim

WORKDIR /workspace

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY apps/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps/api /workspace/apps/api
COPY storage /workspace/storage

ENV PYTHONUNBUFFERED=1
ENV APP_ENV=development

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "/workspace/apps/api", "--host", "0.0.0.0", "--port", "8000"]
