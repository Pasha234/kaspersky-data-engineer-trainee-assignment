FROM python:3.11-slim@sha256:6d10e720318bd84cc3d88af92bb5a47189222e17620a2fd214da6445e4afb679

COPY requirements.txt /tmp/requirements.txt

RUN python -m pip install --no-cache-dir --require-hashes \
    -r /tmp/requirements.txt

RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -d /home/appuser -s /sbin/nologin appuser

WORKDIR /app
COPY download.py .

RUN mkdir -p /models /tmp/hf_cache && \
    chown -R 10001:10001 /app /models /tmp/hf_cache

ENV HOME="/home/appuser" \
    HF_HOME="/tmp/hf_cache" \
    PYTHONUNBUFFERED=1

USER 10001

CMD ["python", "/app/download.py"]