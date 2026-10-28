# ==========================================
# STAGE 1: Builder
# ==========================================
FROM ubuntu:24.04@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3.12 \
    python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv

ENV VIRTUAL_ENV=/opt/venv
RUN uv venv $VIRTUAL_ENV --python python3.12
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# By default, uses universal 'requirements.txt'.
# Can be overridden with: docker build --build-arg REQUIREMENTS_FILE=requirements-zen.txt
ARG REQUIREMENTS_FILE=requirements.txt
COPY ${REQUIREMENTS_FILE} /tmp/requirements.txt

ARG VLLM_VERSION=0.32.0
RUN uv pip install --no-cache --require-hashes \
    -r /tmp/requirements.txt \
    --extra-index-url https://wheels.vllm.ai/${VLLM_VERSION}/cpu \
    --index-strategy first-index \
    --torch-backend cpu


# ==========================================
# STAGE 2: Runtime (Rootless & Clean)
# ==========================================
FROM ubuntu:24.04@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    g++ \
    libgomp1 \
    libomp-dev \
    libtcmalloc-minimal4 \
    ca-certificates \
    libnuma1 \
    && rm -rf /var/lib/apt/lists/*

# 1. REQUIREMENT: Rootless (Create unprivileged user 'vllmuser')
RUN groupadd -g 10001 vllmuser && \
    useradd -u 10001 -g vllmuser -m -d /home/vllmuser -s /sbin/nologin vllmuser

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Default configs for standalone Docker; Kubernetes mounts files from vllm-config.
WORKDIR /app
COPY privacy_filter.py /app/privacy_filter.py
COPY privacy_auth.py /app/privacy_auth.py

# Ensure /app and cache directories belong to non-root user
RUN mkdir -p /models/current /home/vllmuser/.cache && \
    chown -R vllmuser:vllmuser /app /models /home/vllmuser

# 2. REQUIREMENT: Custom Privacy Logging Environment Variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libtcmalloc_minimal.so.4:/opt/venv/lib/libiomp5.so" \
    VLLM_CONFIGURE_LOGGING=1 \
    VLLM_LOGGING_CONFIG_PATH="/app/logging_config.json" \
    HOME="/home/vllmuser"

# Switch to unprivileged user before running the container
USER vllmuser

EXPOSE 8000
CMD ["vllm", "serve", "--config", "/app/config.yaml"]
