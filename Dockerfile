FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime deps first so they layer-cache independently of source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the runtime needs.
COPY app ./app

# Drop privileges.
RUN useradd --create-home --shell /bin/bash --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PORT=8080
EXPOSE 8080

# Cloud Run sends SIGTERM; uvicorn handles it cleanly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]
