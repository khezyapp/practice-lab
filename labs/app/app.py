#!/usr/bin/env python3
"""
TaskFlow - a deliberately small task-management service, used as the practice app
for this Kubernetes lab. ONE image, FOUR modes:

    gunicorn app:app            # HTTP API on :8080      (what the Deployment runs)
    python app.py worker        # consume jobs from Redis (a second Deployment)
    python app.py migrate       # create the DB schema   (run once, as a Job)
    python app.py cleanup       # delete old tasks       (run on a schedule, a CronJob)

Teaching goals baked into the design:

  * ALL configuration comes from the environment (ConfigMap / Secret). Nothing is
    baked into the image, so the same image runs in every environment.
  * The app starts and becomes Ready even when Postgres and Redis are missing or
    unreachable. That lets the lab teach Pods/Deployments/Services BEFORE the
    dependencies exist, and it demonstrates graceful degradation.
  * Every failure path returns a useful JSON body AND logs the reason, so
    `kubectl logs` is genuinely informative.
  * Handles SIGTERM, so rolling updates do not drop requests.
"""

import json
import logging
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone

import psycopg
import redis as redis_lib
import requests
from flask import Flask, jsonify, request

# --------------------------------------------------------------------------- #
# Configuration - the ONLY source is the environment (ConfigMap / Secret)
# --------------------------------------------------------------------------- #

APP_ENV = os.getenv("APP_ENV", "dev")
RELEASE = os.getenv("RELEASE", "dev")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "postgres"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "taskflow"),
    "user": os.getenv("DB_USER", "taskflow"),
    "password": os.getenv("DB_PASSWORD", ""),
}
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_QUEUE = os.getenv("REDIS_QUEUE", "taskflow:jobs")

# Demonstrates egress: the API is allowed to reach exactly this URL by NetworkPolicy.
EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL", "https://api.github.com/zen")

# If "true", the readiness probe fails while the database is unreachable.
# Default false so the API stays Ready in early lab steps (and so a DB blip does
# not pull every replica out of the Service - a real design decision to discuss).
READINESS_REQUIRES_DB = os.getenv("READINESS_REQUIRES_DB", "false").lower() in ("1", "true", "yes")

RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/worker-heartbeat")

# Injected by the Deployment from the Pod's own metadata (downward API).
POD_NAME = os.getenv("POD_NAME") or socket.gethostname()
NODE_NAME = os.getenv("NODE_NAME", "unknown")
START_TIME = time.time()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("taskflow")


# --------------------------------------------------------------------------- #
# Dependencies - always fail soft, always explain
# --------------------------------------------------------------------------- #

def db_connect():
    """Open a Postgres connection. Raises on failure (callers handle it)."""
    return psycopg.connect(connect_timeout=3, **DB_CONFIG)


def db_status():
    """(ok, error_message) - used by /readyz and /api/config."""
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True, None
    except Exception as exc:  # noqa: BLE001 - we want the message, whatever it is
        return False, f"{type(exc).__name__}: {exc}"


def redis_client():
    return redis_lib.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        socket_connect_timeout=3,
        socket_timeout=5,
        decode_responses=True,
    )


def enqueue(job):
    """Push a job onto the Redis queue. Best effort: the request must still succeed."""
    try:
        redis_client().lpush(REDIS_QUEUE, json.dumps(job))
        return True, None
    except Exception as exc:  # noqa: BLE001
        log.warning("could not enqueue job (redis unavailable): %s", exc)
        return False, str(exc)


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #

app = Flask(__name__)


@app.get("/")
def index():
    return jsonify(
        service="taskflow-api",
        release=RELEASE,
        app_env=APP_ENV,
        pod=POD_NAME,
        endpoints=[
            "GET  /healthz", "GET  /readyz", "GET  /api/whoami", "GET  /api/config",
            "GET  /api/tasks", "POST /api/tasks", "PATCH /api/tasks/<id>",
            "GET  /api/queue", "POST /api/queue", "GET  /api/egress",
        ],
    )


@app.get("/healthz")
def healthz():
    """Liveness: is the process itself functioning? Never checks dependencies.

    If this failed because the database was down, the kubelet would restart every
    replica at once - turning a dependency blip into a full outage.
    """
    return jsonify(status="ok", pod=POD_NAME, uptime_s=round(time.time() - START_TIME, 1))


@app.get("/readyz")
def readyz():
    """Readiness: may this Pod receive traffic?

    By default a missing database does NOT make us unready (see READINESS_REQUIRES_DB).
    Flip that env var in step 10 to see the opposite behaviour.
    """
    db_ok, db_err = db_status()
    payload = {"pod": POD_NAME, "db_ok": db_ok, "readiness_requires_db": READINESS_REQUIRES_DB}
    if READINESS_REQUIRES_DB and not db_ok:
        payload["error"] = db_err
        return jsonify(payload), 503
    return jsonify(payload)


@app.get("/api/whoami")
def whoami():
    """Answers 'which replica served this request?' - the load-balancing demo."""
    return jsonify(
        pod=POD_NAME,
        node=NODE_NAME,
        release=RELEASE,
        app_env=APP_ENV,
        uptime_s=round(time.time() - START_TIME, 1),
    )


@app.get("/api/config")
def config():
    """Proves configuration is injected at runtime, not baked into the image."""
    db_ok, db_err = db_status()
    return jsonify(
        app_env=APP_ENV,
        release=RELEASE,
        log_level=LOG_LEVEL,
        db={"host": DB_CONFIG["host"], "port": DB_CONFIG["port"], "name": DB_CONFIG["dbname"],
            "user": DB_CONFIG["user"], "password_set": bool(DB_CONFIG["password"]),
            "reachable": db_ok, "error": db_err},
        redis={"host": REDIS_HOST, "port": REDIS_PORT, "queue": REDIS_QUEUE},
        external_api_url=EXTERNAL_API_URL,
        retention_days=RETENTION_DAYS,
        pod=POD_NAME,
        node=NODE_NAME,
    )


@app.get("/api/tasks")
def list_tasks():
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, done, created_at, completed_at FROM tasks ORDER BY id DESC LIMIT 100"
            )
            rows = cur.fetchall()
    except psycopg.errors.UndefinedTable:
        return jsonify(
            error="database is reachable but the schema does not exist yet",
            hint="run the migration: kubectl apply -f labs/07-postgres.yaml (the taskflow-migrate Job)",
        ), 503
    except Exception as exc:  # noqa: BLE001
        return jsonify(error="database unavailable", detail=f"{type(exc).__name__}: {exc}",
                       hint="is postgres running and is the taskflow-secret correct?"), 503

    return jsonify(
        pod=POD_NAME,
        count=len(rows),
        tasks=[
            {"id": r[0], "title": r[1], "done": r[2],
             "created_at": r[3].isoformat() if r[3] else None,
             "completed_at": r[4].isoformat() if r[4] else None}
            for r in rows
        ],
    )


@app.post("/api/tasks")
def create_task():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify(error="body must be JSON with a non-empty 'title'"), 400

    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO tasks (title) VALUES (%s) RETURNING id", (title,))
            task_id = cur.fetchone()[0]
            conn.commit()
    except psycopg.errors.UndefinedTable:
        return jsonify(error="schema not migrated yet",
                       hint="run the taskflow-migrate Job"), 503
    except Exception as exc:  # noqa: BLE001
        return jsonify(error="could not write to the database",
                       detail=f"{type(exc).__name__}: {exc}"), 503

    queued, queue_err = enqueue({"id": task_id, "action": "notify_new_task", "title": title})
    log.info("created task id=%s queued=%s", task_id, queued)
    return jsonify(id=task_id, title=title, done=False, queued=queued,
                   queue_error=queue_err, served_by=POD_NAME), 201


@app.patch("/api/tasks/<int:task_id>")
def complete_task(task_id):
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET done = TRUE, completed_at = now() WHERE id = %s RETURNING id",
                (task_id,),
            )
            updated = cur.fetchone()
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        return jsonify(error="database unavailable", detail=f"{type(exc).__name__}: {exc}"), 503

    if not updated:
        return jsonify(error=f"task {task_id} not found"), 404
    enqueue({"id": task_id, "action": "notify_task_completed"})
    return jsonify(id=task_id, done=True)


@app.get("/api/queue")
def queue_depth():
    """How much work is waiting - the metric you would autoscale a worker on."""
    try:
        r = redis_client()
        return jsonify(pod=POD_NAME, queue=REDIS_QUEUE, depth=r.llen(REDIS_QUEUE),
                       processed_total=int(r.get("taskflow:processed") or 0))
    except Exception as exc:  # noqa: BLE001
        return jsonify(error="redis unavailable", detail=f"{type(exc).__name__}: {exc}"), 503


@app.post("/api/queue")
def queue_job():
    body = request.get_json(silent=True) or {}
    ok, err = enqueue({"id": body.get("id", int(time.time())), "action": "manual_test",
                       "title": body.get("title", "manual test job")})
    if not ok:
        return jsonify(error="redis unavailable", detail=err), 503
    return jsonify(queued=True, queue=REDIS_QUEUE, pod=POD_NAME), 202


@app.get("/api/egress")
def egress():
    """The egress demo: call a third-party API from inside the Pod.

    Works only if a NetworkPolicy explicitly allows egress to the internet on 443.
    On a blocked egress you will see a *timeout* (packets dropped), not a refusal.
    """
    started = time.time()
    try:
        resp = requests.get(EXTERNAL_API_URL, timeout=5,
                            headers={"User-Agent": f"taskflow/{RELEASE}"})
        return jsonify(
            url=EXTERNAL_API_URL,
            status=resp.status_code,
            body=resp.text.strip()[:200],
            elapsed_ms=int((time.time() - started) * 1000),
            pod=POD_NAME,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify(
            error="could not reach the external API",
            detail=f"{type(exc).__name__}: {exc}",
            url=EXTERNAL_API_URL,
            elapsed_ms=int((time.time() - started) * 1000),
            hint="a NetworkPolicy is probably dropping egress (a timeout means 'dropped')",
        ), 504


@app.errorhandler(404)
def not_found(_):
    return jsonify(error="no such route", path=request.path, pod=POD_NAME), 404


# --------------------------------------------------------------------------- #
# Non-HTTP modes
# --------------------------------------------------------------------------- #

def heartbeat():
    """Touch a file the liveness probe checks - the 'is the loop still turning?' pattern."""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        log.warning("could not write heartbeat: %s", exc)


def mode_migrate():
    """Idempotent schema migration - safe to run more than once (it will be)."""
    statements = [
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    TEXT PRIMARY KEY,
               applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
           )""",
        """CREATE TABLE IF NOT EXISTS tasks (
               id           BIGSERIAL PRIMARY KEY,
               title        TEXT        NOT NULL,
               done         BOOLEAN     NOT NULL DEFAULT FALSE,
               created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
               completed_at TIMESTAMPTZ
           )""",
        "CREATE INDEX IF NOT EXISTS tasks_done_created_idx ON tasks (done, created_at)",
    ]
    with db_connect() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES ('0001_init') ON CONFLICT DO NOTHING"
            )
        conn.commit()
    log.info("migration complete (version 0001_init) - taskflow schema is ready")


def mode_cleanup():
    """Delete completed tasks older than RETENTION_DAYS. Idempotent by construction."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM tasks WHERE done = TRUE AND completed_at < now() - INTERVAL '1 day' * %s",
            (RETENTION_DAYS,),
        )
        deleted = cur.rowcount
        conn.commit()
    log.info("cleanup complete: deleted %s completed task(s) older than %s day(s)",
             deleted, RETENTION_DAYS)


def mode_worker():
    """Consume jobs from Redis. Long-running, so SIGTERM handling matters."""
    stop = {"now": False}

    def on_signal(signum, _frame):
        log.info("received signal %s - finishing the current job, then exiting", signum)
        stop["now"] = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    r = redis_client()
    log.info("worker started: pod=%s queue=%s redis=%s:%s", POD_NAME, REDIS_QUEUE, REDIS_HOST, REDIS_PORT)

    while not stop["now"]:
        heartbeat()
        try:
            item = r.blpop(REDIS_QUEUE, timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("redis unavailable (%s) - retrying in 5s", exc)
            time.sleep(5)
            continue

        if item is None:
            continue

        _, raw = item
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("skipping malformed job payload: %r", raw)
            continue

        log.info("processing job id=%s action=%s", job.get("id"), job.get("action"))
        time.sleep(1)  # pretend this is real work
        try:
            r.incr("taskflow:processed")
        except Exception as exc:  # noqa: BLE001
            log.warning("could not update the processed counter: %s", exc)
        log.info("job complete id=%s", job.get("id"))

    log.info("worker stopped cleanly")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
    log.info("starting mode=%s pod=%s node=%s release=%s app_env=%s",
             mode, POD_NAME, NODE_NAME, RELEASE, APP_ENV)

    if mode == "serve":
        app.run(host="0.0.0.0", port=8080)
    elif mode == "migrate":
        mode_migrate()
    elif mode == "cleanup":
        mode_cleanup()
    elif mode == "worker":
        mode_worker()
    else:
        log.error("unknown mode %r - use one of: serve, worker, migrate, cleanup", mode)
        sys.exit(2)


if __name__ == "__main__":
    main()
