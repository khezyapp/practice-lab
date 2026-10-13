# Lab 12 — Job & CronJob

**Goal:** run work that must *finish* on a schedule, find the missing NetworkPolicy that
blocks it, and understand why idempotency is not optional.

**Read first:** [Jobs & CronJobs](../concepts/13-jobs-and-cronjobs.md).

**Time:** ~60 minutes.

---

## 1. A Job you already have

You already created a Job in step 07. Look at it again with the concepts in mind:

```powershell
kubectl get jobs -n taskflow
kubectl describe job taskflow-migrate -n taskflow
kubectl get pods -n taskflow -l job-name=taskflow-migrate
```

```
NAME                STATUS     COMPLETIONS   DURATION
taskflow-migrate    Complete   1/1           6s
```

The Pod is still there with status `Completed`. That is the Job's guarantee, and the
reason a Deployment cannot do this job (pun intended): a Deployment would have restarted
that exited container forever.

Three fields to notice in `describe`:

- **`Parallelism` / `Completions`** — 1/1 here: run once, finish once.
- **`Backoff Limit: 6`** — retries if Postgres was still booting.
- **`Events`** — usually `SuccessfulCreate` and then nothing. A clean Job is quiet.

Re-run it by hand and watch it be a no-op — this is what *idempotent* looks like:

```powershell
kubectl create job --from=job/taskflow-migrate migrate-again -n taskflow
kubectl logs job/migrate-again -n taskflow
# "migration complete (version 0001_init)" -> ran fine a second time, changed nothing
kubectl delete job migrate-again -n taskflow
```

The `CREATE TABLE IF NOT EXISTS` and `ON CONFLICT DO NOTHING` in `app.py` are what make
that true. Remove them and the second run would fail — and `Job`s **will** run more than
once. Retries, restarts, a controller restart, a manual re-run: at-least-once is the
semantics, always.

---

## 2. Apply the CronJob

```powershell
kubectl apply -f labs/12-job-cronjob.yaml
kubectl get cronjob -n taskflow
```

```
NAME               SCHEDULE      SUSPEND   ACTIVE   LAST SCHEDULE   AGE
taskflow-cleanup   */5 * * * *   False     0        <none>          10s
```

The schedule is every 5 minutes so you can watch it in a lab. Production would be
`0 2 * * *`. Note the other fields you configured, and what each one prevents:

| Field | Value | What it prevents |
|---|---|---|
| `concurrencyPolicy: Forbid` | never overlap | a slow run overlapping itself and double-processing |
| `startingDeadlineSeconds: 120` | give up on a missed schedule | a stampede of catch-up Jobs after a controller outage |
| `successfulJobsHistoryLimit` / `failedJobsHistoryLimit` | 3 / 3 | thousands of finished Jobs accumulating (which eventually stops the controller from creating new ones) |
| `timeZone: UTC` | explicit | "2am" meaning something other than what you meant |
| `activeDeadlineSeconds: 180` | hard stop | a hung Job blocking every future run, forever |
| `ttlSecondsAfterFinished: 600` | auto-delete | manual cleanup |
| `backoffLimit: 2` | 3 attempts | infinite retries |

---

## 3. Watch the first run — and find the bug

Do not wait for the 5-minute tick. Trigger it now:

```powershell
kubectl create job --from=cronjob/taskflow-cleanup manual-cleanup-1 -n taskflow
kubectl get jobs,pods -n taskflow -w
```

**It fails.** And it fails three times (1 + `backoffLimit: 2`), taking about 30 seconds:

```powershell
kubectl get jobs -n taskflow
kubectl logs job/manual-cleanup-1 -n taskflow
kubectl describe job manual-cleanup-1 -n taskflow
```

```
Traceback (most recent call last):
  ...
psycopg.OperationalError: connection failed: connection to server at
"10.244.0.4", port 5432 failed: timeout expired
```

### Diagnose it before reading on

Three candidate explanations:

1. Postgres is down.
2. The credentials are wrong.
3. Something is blocking the connection.

Check the cheap ones first:

```powershell
kubectl get pod postgres-0 -n taskflow          # Running, 1/1
kubectl get svc postgres -n taskflow            # exists, has endpoints
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=postgres
```

All healthy. So the database is fine and the *path* to it is blocked. You built that
block on purpose in step 09:

```powershell
kubectl get networkpolicies -n taskflow
kubectl describe networkpolicy allow-migrate-egress -n taskflow
kubectl describe networkpolicy default-deny-all -n taskflow
```

**`default-deny-all` selects every Pod in the namespace, including your new cleanup
Job's Pod — and nothing grants it egress.** `taskflow-migrate` has a rule
(`allow-migrate-egress`); `taskflow-cleanup` does not.

And notice how the error presented itself: **a timeout, not a refusal, and not "blocked
by a NetworkPolicy".** That is the single most expensive property of NetworkPolicy —
denials look exactly like a slow or broken server.

> This is a real-world pattern worth internalising: **adding a workload without adding
> its NetworkPolicy**. The policy is scoped to a Pod *selector*, so a new label set means
> a new rule. Teams hit this every time they add a Job, a CronJob or a sidecar.

---

## 4. Fix it

The fix is commented out at the bottom of `labs/12-job-cronjob.yaml`. Uncomment the
whole block and apply:

```powershell
kubectl apply -f labs/12-job-cronjob.yaml
kubectl get networkpolicies -n taskflow
```

Both halves are required, and that is the other half of the lesson:

- `allow-cleanup-egress` — the cleanup Pod may **go** to Postgres.
- `allow-cleanup-into-postgres` — Postgres may **accept** the cleanup Pod.

A connection needs both directions allowed. Allowing only the egress rule and forgetting
the ingress rule is the most common NetworkPolicy mistake after forgetting DNS, and it
fails identically — a timeout.

Trigger another run and watch it succeed:

```powershell
kubectl delete job manual-cleanup-1 -n taskflow --ignore-not-found
kubectl create job --from=cronjob/taskflow-cleanup manual-cleanup-2 -n taskflow
kubectl logs job/manual-cleanup-2 -n taskflow
```

```
cleanup complete: deleted 0 completed task(s) older than 7 day(s)
```

Zero, because everything you created is minutes old. Now make it delete something, to
prove the logic works rather than merely not-erroring:

```powershell
# create a task and complete it
curl.exe -s -X POST -H "Host: taskflow.local" -H "Content-Type: application/json" `
  -d '{\"title\":\"this one gets cleaned up\"}' http://localhost:8080/api/tasks
curl.exe -s -X PATCH -H "Host: taskflow.local" http://localhost:8080/api/tasks/4
```

```powershell
# backdate its completion so it looks 30 days old
kubectl run psql --rm -it --image=postgres:16-alpine -n taskflow -- `
  psql -h postgres-0.postgres -U taskflow -d taskflow -c `
  "UPDATE tasks SET completed_at = now() - INTERVAL '30 days' WHERE done = TRUE;"
```

```powershell
# now the job has work to do
kubectl create job --from=cronjob/taskflow-cleanup manual-cleanup-3 -n taskflow
kubectl logs job/manual-cleanup-3 -n taskflow
# "cleanup complete: deleted 1 completed task(s) older than 7 day(s)"
```

Change `RETENTION_DAYS` in the ConfigMap to `1`, apply, re-trigger, and compare:

```powershell
kubectl patch configmap taskflow-config -n taskflow --type merge -p '{"data":{"RETENTION_DAYS":"1"}}'
kubectl create job --from=cronjob/taskflow-cleanup manual-cleanup-4 -n taskflow
kubectl logs job/manual-cleanup-4 -n taskflow
```

That is runtime configuration reaching a scheduled Job, with no rebuild. The image is
still `1.0.0`.

---

## 5. Break it

### 5a. Let two runs overlap

```powershell
kubectl patch cronjob taskflow-cleanup -n taskflow --type merge -p '{"spec":{"concurrencyPolicy":"Allow"}}'
kubectl patch cronjob taskflow-cleanup -n taskflow --type merge -p '{"spec":{"schedule":"* * * * *"}}'
kubectl get jobs -n taskflow -w
```

Within a couple of minutes you will see runs stacking up concurrently. On a cache purge
that is merely wasteful; on a billing job or a data migration it is a corruption bug.
This is why `Forbid` is the default choice for anything that mutates state.

```powershell
kubectl apply -f labs/12-job-cronjob.yaml       # restore schedule and policy
```

### 5b. Remove the safety net

```powershell
kubectl patch cronjob taskflow-cleanup -n taskflow --type json `
  -p '[{"op":"remove","path":"/spec/jobTemplate/spec/activeDeadlineSeconds"},{"op":"remove","path":"/spec/suspend"}]'
kubectl patch cronjob taskflow-cleanup -n taskflow --type json `
  -p '[{"op":"add","path":"/spec/jobTemplate/spec/template/spec/containers/0/command","value":["sh","-c","sleep 3600"]}]'
```

Now a run hangs forever, and with `concurrencyPolicy: Forbid` **every future run is
skipped** — you have silently disabled your nightly job. Check the symptoms:

```powershell
kubectl get jobs -n taskflow
kubectl get cronjob taskflow-cleanup -n taskflow
kubectl get events -n taskflow --field-selector type=Warning | Select-String -Pattern "cleanup"
```

Restore:

```powershell
kubectl delete jobs -n taskflow -l app.kubernetes.io/name=taskflow-cleanup
kubectl apply -f labs/12-job-cronjob.yaml
```

`activeDeadlineSeconds` and `startingDeadlineSeconds` are the two fields people omit and
then need at the worst possible time.

### 5c. Non-idempotent work

Change the cleanup command to a hard `DELETE FROM tasks` with no age filter, and run it
twice. The first run destroys data; the second returns 0 deleted and looks successful.
**The job reported success both times.** Nothing in Kubernetes can detect that your job
was semantically wrong — only you can, by designing it so running it twice is the same
as running it once.

```powershell
kubectl apply -f labs/12-job-cronjob.yaml       # restore everything
```

---

## 6. Exercises

1. **Parallelism.** Uncomment the parallel `Job` example at the bottom of
   `labs/12-job-cronjob.yaml` (`completions: 6`, `parallelism: 3`) and apply it. Watch
   how three Pods run at a time until six succeed. Then change `completions` to 12 and
   explain where the extra Pods get their identity.
2. **`completionMode: Indexed`.** Add it and read the
   `JOB_COMPLETION_INDEX` env var in each Pod — this is how you shard work.
3. **`ttlSecondsAfterFinished`.** Set it to `30`, trigger a Job, and watch it delete
   itself. Then explain the debugging trade-off: a self-deleting Job also deletes the
   logs you need unless you ship them elsewhere.
4. **Suspend.** `kubectl patch cronjob taskflow-cleanup -n taskflow -p '{"spec":{"suspend":true}}'`
   and confirm no Jobs are created. When is this the right move? (During an incident —
   pausing a destructive scheduled job is a legitimate first response.)
5. **Migrate before you roll out.** Write the deployment sequence as your CI would run
   it: apply the Job, wait for completion, then apply the new Deployment. Then explain
   why expand/contract migrations are required when the old and new code run at the same
   time during a rolling update.
6. **A `Job` that must not run twice.** Can you enforce that? (Not really — a Job is
   at-least-once. Use a database lock, `INSERT ... ON CONFLICT`, or an external
   coordinator. Kubernetes is not a transaction manager.)
7. **Look at the CronJob's children.**
   `kubectl get jobs -n taskflow -l app.kubernetes.io/name=taskflow-cleanup` — each run
   is a separate Job with a generated name. Where does the name come from, and what
   happens to old ones? (`successfulJobsHistoryLimit`.)

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Job Pods fail with a DB timeout | missing egress/ingress NetworkPolicy (this step's exercise) | add both halves |
| Job never completes, retries forever | the command keeps failing | `kubectl logs job/<name>`, check `backoffLimit` |
| Job `Failed`, `DeadlineExceeded` | `activeDeadlineSeconds` too low, or genuinely hung | raise it, or fix the job |
| CronJob stopped creating Jobs | too many retained Jobs, or a stuck active run with `Forbid` | check history limits; delete the stuck Job |
| No Jobs at all | `suspend: true`, or a schedule that never matches | `kubectl get cronjob -o yaml` |
| Job ran twice and corrupted data | at-least-once semantics | make it idempotent — this is your bug, not Kubernetes' |
| Job Pod `restartPolicy: Always` rejected | Jobs need `Never` or `OnFailure` | fix the field |

---

## 8. Clean up

Keep the CronJob (it is harmless — it deletes nothing when there is nothing old), but
remove your manual test Jobs:

```powershell
kubectl delete jobs -n taskflow -l app.kubernetes.io/name=taskflow-cleanup --ignore-not-found
kubectl delete job manual-cleanup-1 manual-cleanup-2 manual-cleanup-3 manual-cleanup-4 -n taskflow --ignore-not-found
kubectl get jobs -n taskflow
```

To stop the CronJob from producing Jobs during the rest of the lab:

```powershell
kubectl patch cronjob taskflow-cleanup -n taskflow --type merge -p '{"spec":{"suspend":true}}'
```

---

**Checkpoint:** you can explain why a schema migration is a Job and not an init container
job-with-replicas, why a cleanup Job must be idempotent, which CronJob fields prevent
which incident, and why a new workload needs a new NetworkPolicy.

**Next:** [Lab 13 — Cleanup & review](13-cleanup-and-review.md) →
