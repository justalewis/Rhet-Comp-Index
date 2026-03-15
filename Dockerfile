# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies into a separate layer for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create the data directory that the Fly.io volume will be mounted on
RUN mkdir -p /data

# The database lives on the persistent volume at /data/articles.db
# We set this via environment variable so db.py can find it
ENV DB_PATH=/data/articles.db

# Fly.io expects the app to listen on 0.0.0.0:8080
ENV PORT=8080

EXPOSE 8080

# Use gunicorn for production; 1 worker keeps SQLite writes safe
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "120", "app:app"]
