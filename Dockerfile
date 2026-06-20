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
# ONE worker, MANY threads (gthread). A single sync worker serves one request
# at a time, so a burst of traffic (e.g. an aggressive crawler walking
# /article, /export, /citations, /explore) queues behind it and the 5s
# readiness probe can't get a slot — Fly then marks the machine critical and
# the proxy stops routing, taking the whole site down (incident 2026-06-20).
# Worse, each 300s worker-timeout kill restarts the process and wipes the
# in-memory rate-limiter counters, so the per-IP cap never accumulates.
# gthread keeps a SINGLE process (so the in-memory limiter stays coherent and
# SQLite keeps one writer) while letting the health check and quick 429s
# interleave with slower requests, and stops the timeout/restart cycle so the
# rate limiter actually bites. Multi-PROCESS (--workers >1) would break both
# the limiter and the single-writer assumption; threads do not.
#
# --preload is intentionally omitted: it imports app.py (and runs init_db) in the
# master process before forking, which conflicts with any background SQLite writer
# (e.g. fetch_institutions.py) that may be holding a write lock at deploy time,
# causing the single worker to deadlock on startup. Without --preload the worker
# imports the app after forking, which is safe.
# --access-logfile -  : emit one access line per request to stdout (Fly logs).
# --access-logformat   : include the real client IP (Fly-Client-IP header, the
#   same one rate_limit.py keys on) and the User-Agent, so an abusive crawler
#   can be identified and blocked. Added during the 2026-06-20 incident: a bot
#   was overwhelming the box and there was no way to attribute the traffic.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--worker-class", "gthread", "--timeout", "300", "--capture-output", "--log-level", "info", "--access-logfile", "-", "--access-logformat", "cip=%({Fly-Client-IP}i)s %(s)s %(M)sms \"%(r)s\" ua=\"%(a)s\"", "app:app"]
