# Concept 13 — Jobs & CronJobs

> **In one sentence:** Use a **Job** for work that must run and *finish* — migrations, batch
> processing, backups — and a **CronJob** when that work must run on a schedule.

Estimated read: 10 minutes. Prerequisites: [Pod](02-pod.md), [Deployment](03-deployment.md).

---

## 1. Why not a Deployment or a bare Pod?

| | Bare Pod | Deployment | Job |
|---|---|---|---|
| Restarts the container forever | `restartPolicy: Always` — yes | yes | **no**, stops when done |
| Retries on failure | no | yes | yes, with `backoffLimit` |
| Guarantees completion | no | no | **yes** — tracks `COMPLETIONS` |
| Parallel workers | no | yes, but unbounded | yes, with `parallelism` |
| Suitable for "run once" | kind of | **no** (a completed container is restarted!) | yes |

A Deployment is designed so its Pods **never exit**. Give it a container that exits 0 and the
kubelet restarts it in a loop. A Job is designed for the opposite case.

---

## 2. Job

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: taskflow-migrate
  namespace: taskflow
  labels:
    app.kubernetes.io/name: taskflow-migrate
    app.kubernetes.io/part-of: taskflow
spec:
  backoffLimit: 3                  # retries before the Job is marked Failed
  activeDeadlineSeconds: 300       # hard stop; prevents "stuck forever"
  ttlSecondsAfterFinished: 600     # auto-delete the Job (and its Pod) 10 min after it ends
  template:
    metadata:
      labels:
        app.kubernetes.io/name: taskflow-migrate
    spec:
      restartPolicy: OnFailure     # REQUIRED: Never or OnFailure (not Always)
      containers:
        - name: migrate
          image: taskflow-api:1.0.0
          command: ["python","app.py","migrate"]
          envFrom:
            - configMapRef: { name: taskflow-config }
            - secretRef:    { name: taskflow-secret }
```

| Field | Meaning |
|---|---|
| `completions` | how many successful Pod completions are required (default 1) |
| `parallelism` | how many Pods may run at once (default 1) |
| `backoffLimit` | retry attempts with exponential backoff before failing (default 6) |
| `activeDeadlineSeconds` | wall-clock limit for the whole Job; also caps retries |
| `ttlSecondsAfterFinished` | auto-cleanup — use it, or finished Jobs pile up forever |
| `restartPolicy` | `Never` (fresh Pod per attempt, better for debugging) or `OnFailure` (retry in place). `Always` is **invalid** |
| `suspend: true` | create the Job but do not run it yet |

### The two classic shapes

**Run once, exactly one Pod** — a migration:

```yaml
spec:
  backoffLimit: 3
  activeDeadlineSeconds: 300
```

**Fixed-size parallel batch** — process a known workload in N workers:

```yaml
spec:
  completions: 10
  parallelism: 3        # 3 at a time until 10 succeed
```

**Work-queue pattern** — workers pull tasks until the queue is empty:

```yaml
spec:
  parallelism: 5        # no completions: runs until one worker succeeds and exits
```

**Indexed** — each Pod gets a stable index (`JOB_COMPLETION_INDEX`), for sharding:

```yaml
spec:
  completions: 5
  parallelism: 5
  completionMode: Indexed
```

### Exit codes are the API

The container's exit code is how a Job decides success: `0` = success, anything else =
failure. If your "migration" script exits 0 on error, the Job reports `Complete` and you have
a silent data problem. **Never swallow errors in a Job.**

---

## 3. CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: taskflow-cleanup
  namespace: taskflow
spec:
  schedule: "0 2 * * *"                    # 02:00 every day
  timeZone: "Asia/Bangkok"                 # explicit; otherwise UTC
  concurrencyPolicy: Forbid                # Allow | Forbid | Replace
  startingDeadlineSeconds: 300             # if it could not start within 5 min, skip
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  suspend: false
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 600
      ttlSecondsAfterFinished: 3600
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: cleanup
              image: taskflow-api:1.0.0
              command: ["python","app.py","cleanup"]
              envFrom:
                - configMapRef: { name: taskflow-config }
                - secretRef:    { name: taskflow-secret }
```

### Cron syntax

```
┌──────── minute        (0-59)
│ ┌────── hour          (0-23)
│ │ ┌──── day of month  (1-31)
│ │ │ ┌── month         (1-12)
│ │ │ │ ┌ day of week   (0-6, Sunday=0)
* * * * *
```

| Schedule | Meaning |
|---|---|
| `*/5 * * * *` | every 5 minutes |
| `0 * * * *` | hourly, on the hour |
| `0 2 * * *` | daily at 02:00 |
| `0 2 * * 0` | Sundays at 02:00 |
| `@daily`, `@hourly`, `@weekly` | macros (also supported) |

There is **no seconds field** — use `*/1 * * * *` for the fastest standard interval, or a
Job/Controller for sub-minute work.

### The fields that actually prevent incidents

| Field | Why it matters |
|---|---|
| `concurrencyPolicy: Forbid` | if the previous run is still going, skip this one. **Without this, a slow job overlaps with itself**, doubling the load and often corrupting data |
| `startingDeadlineSeconds` | without it, a CronJob that missed ~100 schedules can produce a burst of catch-up Jobs |
| `successfulJobsHistoryLimit` / `failedJobsHistoryLimit` | without them, you accumulate thousands of finished Jobs; the CronJob controller then stops being able to create new ones |
| `timeZone` | schedules are UTC by default; "2am" almost never means 2am UTC |
| `activeDeadlineSeconds` on the Job | a hung job otherwise runs forever and blocks (with `Forbid`) all future runs |

Say it plainly: **a CronJob that is not idempotent will eventually corrupt your data.**
Retries, restarts, overlapping runs and manual re-runs all mean your job will execute more
than once. Write jobs that can be run twice safely.

---

## 4. How to use Jobs for database migrations

Three patterns, in increasing order of safety:

| Pattern | Where | Trade-off |
|---|---|---|
| **Init container** | inside each app Pod, before the app starts | simple, but N replicas each try to migrate; needs an advisory lock |
| **Job, run before the rollout** | CI/CD or a manual step, before `kubectl apply` of the new Deployment | explicit ordering, no race. **Recommended** |
| **Helm/Kustomize hook** | `helm.sh/hook: pre-upgrade` | automated, but coupling release logic to schema changes |

The recommended flow:

```powershell
kubectl apply -f labs/13-cronjob-migrate.yaml       # creates the migrate Job
kubectl wait --for=condition=complete job/taskflow-migrate -n taskflow --timeout=120s
kubectl apply -f labs/04-deployment.yaml            # then roll out the new app version
```

Backward-compatible ("expand/contract") migrations are what make this safe: add the new
column in release N, use it in release N+1, drop the old one in release N+2. Rolling updates
mean old and new code run **at the same time**, so the schema must serve both.

---

## 5. Commands

```powershell
kubectl apply -f labs/13-cronjob-migrate.yaml
kubectl get jobs -n taskflow
kubectl get pods -n taskflow --show-labels | Select-String job-name
kubectl logs job/taskflow-migrate -n taskflow
kubectl describe job taskflow-migrate -n taskflow        # shows conditions + why it failed
kubectl wait --for=condition=complete job/taskflow-migrate -n taskflow --timeout=120s

kubectl get cronjob -n taskflow                          # SCHEDULE, LAST SCHEDULE, ACTIVE
kubectl create job --from=cronjob/taskflow-cleanup manual-1 -n taskflow   # test now, don't wait
kubectl patch cronjob taskflow-cleanup -n taskflow -p '{"spec":{"suspend":true}}'
```

That `kubectl create job --from=cronjob/...` line is the trick to remember: it lets you
trigger a scheduled job immediately for testing instead of waiting until 02:00.

---

## 6. Debugging

| Symptom | Cause |
|---|---|
| Job never completes, Pods keep being created | the app keeps failing; `backoffLimit` exhausted → check logs |
| Job `Failed` with `DeadlineExceeded` | `activeDeadlineSeconds` too low, or the job is genuinely hung |
| Job Pod stuck `Terminating` | long `terminationGracePeriodSeconds`, or a finalizer |
| CronJob creates nothing | `suspend: true`, a schedule that never matches, or `startingDeadlineSeconds` too small |
| CronJob stops creating Jobs after a while | too many retained Jobs (history limits) or a stuck active run with `Forbid` |
| Job ran twice | expected at-least-once semantics — make it idempotent |
| Job hanging forever, blocking the CronJob | missing `activeDeadlineSeconds` |

```powershell
kubectl get events -n taskflow --field-selector type=Warning
kubectl logs <job-pod> -n taskflow --previous
```

---

## 7. Common mistakes

1. **Non-idempotent jobs.** Any retry corrupts state. Use `INSERT ... ON CONFLICT`, upserts,
   `IF NOT EXISTS`, or explicit migration versioning.
2. **No `activeDeadlineSeconds`.** A hung job runs forever.
3. **No `concurrencyPolicy: Forbid`** on a long-running CronJob → overlapping runs.
4. **No `ttlSecondsAfterFinished` / history limits** → endless accumulation of Jobs.
5. **`restartPolicy: Always`** in a Job spec → API rejects it.
6. **Assuming a CronJob runs exactly once.** It does not. Ever.
7. **Timezone confusion.** `0 2 * * *` is 02:00 UTC unless you set `timeZone`.
8. **A migration Job that starts writing before the schema is ready**, or that races with a
   rolling deployment. Use expand/contract migrations.
9. **Putting the migration in an init container on every replica.** Works only with an
   advisory lock and a sound ordering; a Job is simpler to reason about.
10. **Deleting a Job and expecting the Pods to go.** `kubectl delete job` cascades by default,
    but check for orphaned Pods after `--cascade=orphan`.
11. **Using CronJob as a scheduler for long DP work.** For DAGs, retries with backoff, and
    visibility, use Argo Workflows / Tekton — CronJob is a scheduler, not a workflow engine.

---

## 8. Check yourself

1. What `restartPolicy` values are valid for a Job? → *`Never` or `OnFailure`*
2. Why does a Deployment never suit a "run once" container? → *it restarts exited containers
   forever by design*
3. What does `concurrencyPolicy: Forbid` prevent? → *overlapping runs of the same CronJob*
4. `completions: 10, parallelism: 3` — what happens? → *3 Pods at a time until 10 successful
   completions*
5. How do you run a CronJob's workload immediately? → *`kubectl create job --from=cronjob/<name> <job-name>`*
6. What happens if `startingDeadlineSeconds` is unset and the controller was down for an
   hour? → *it may create a burst of catch-up Jobs (up to 100 missed schedules are
   considered)*
7. Why must migration Jobs be idempotent? → *at-least-once execution: retries, restarts and
   manual re-runs all mean "more than once"*
8. How do you safely roll out a schema change with rolling updates? → *expand/contract:
   additive change first, use it in the next release, remove the old in the one after*

---

**Practice it:** [Lab step 12 — Job & CronJob](../lab/12-job-and-cronjob.md) ·
**Next:** [Architecture: request lifecycle](../architecture/01-request-lifecycle.md)
