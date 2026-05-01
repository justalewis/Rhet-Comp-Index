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

# Single-process deployment: gunicorn serves HTTP, daily fetch + nightly
# backup are triggered externally by .github/workflows/cron.yml. The
# previous standalone scheduler.py process group was removed because Fly
# volumes are single-attach and the scheduler had no way to share /data
# with the app machine. See docs/refactor-notes/13-scheduler-architecture-fix.md.
#
# --preload is intentionally omitted: it imports app.py (and runs init_db) in the
# master process before forking, which conflicts with any background SQLite writer
# (e.g. fetch_institutions.py) that may be holding a write lock at deploy time,
# causing the single worker to deadlock on startup. Without --preload each worker
# imports the app independently after forking, which is safe.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "300", "--capture-output", "--log-level", "info", "app:app"]
