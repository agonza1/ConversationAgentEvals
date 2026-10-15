FROM python:3.13-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/pipecat/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/pipecat /app
COPY apps/api/app/services/speech_settings.py /app/speech_settings.py
COPY apps/api/app/services/synthesized_audio.py /app/synthesized_audio.py
COPY apps/api/app/services/billing_address_simulation.py /app/billing_address_simulation.py
COPY apps/api/app/services/waylo_audio_conditions.py /app/waylo_audio_conditions.py
COPY apps/api/app/assets/background-noise /app/background-noise

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8110"]
