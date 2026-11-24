FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py .
RUN useradd --create-home --uid 10001 venom \
    && mkdir -p /tmp/venom-cutter-jobs \
    && chown -R venom:venom /app /tmp/venom-cutter-jobs
USER venom

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:\${PORT:-5001} --workers 1 --threads 2 --timeout 360 api:app"]
