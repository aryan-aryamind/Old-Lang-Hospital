FROM python:3.10-slim

# System deps required by scientific libs and faiss-cpu
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    \
    # Default to production; override as needed
    FLASK_ENV=production

WORKDIR /app

# Install Python deps first (leverage Docker layer caching)
COPY requirements.txt /app/
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy application code
COPY . /app

# Create static cache dir used by Google TTS helper
RUN mkdir -p /app/static/audio_cache

# Expose Flask port
EXPOSE 5000

# Healthcheck (simple TCP)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', 5000)); s.close()" || exit 1

# Default command: run via gunicorn
CMD ["gunicorn", "-w", "2", "-k", "gthread", "-b", "0.0.0.0:5000", "app_main:app"]


