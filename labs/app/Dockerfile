# TaskFlow API - the image you build in lab step 04.
#
# Design notes that matter for Kubernetes:
#   * a fixed, non-root UID (1000) so `runAsNonRoot: true` works and so the
#     filesystem permissions are predictable on mounted volumes
#   * no configuration baked in - everything arrives via ConfigMap / Secret
#   * a single ENTRYPOINT that can be overridden for the worker / migrate / cleanup
#     modes, so ONE image serves four Kubernetes workloads
#   * unbuffered stdout so `kubectl logs` shows output immediately

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is not needed to run the app; it is there so you can debug from inside the
# Pod (curl localhost:8080/healthz). Remove it if you care about image size.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app.py ./

# Create the non-root user that owns the app directory.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser \
 && chown -R 1000:1000 /app

USER 1000

EXPOSE 8080

# The default mode is the HTTP API. Kubernetes overrides `command` for the worker,
# the migration Job and the cleanup CronJob - same image, different entry point.
ENTRYPOINT ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--threads", "4", \
            "--timeout", "30", "--graceful-timeout", "25", \
            "--access-logfile", "-", "--error-logfile", "-", "app:app"]
