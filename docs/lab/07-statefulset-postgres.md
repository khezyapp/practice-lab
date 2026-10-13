# Lab 07 — StatefulSet + Postgres

**Goal:** run a real database with a stable identity and its own persistent disk,
migrate the schema with a Job, and prove that data survives both a Pod deletion and a
rescheduling.

**Read first:** [StatefulSet](../concepts/07-statefulset.md),
[Storage & Volumes](../concepts/10-storage-and-volumes.md), and
[Jobs & CronJobs](../concepts/13-jobs-and-cronjobs.md) for the migration Job.

**Time:** ~90 minutes.

---

## 1. Apply it

```powershell
kubectl apply -f labs/07-postgres.yaml
kubectl get pvc,statefulset,pods -n taskflow -w
```

Watch three things happen in order:

1. `data-postgres-0` goes `Pending` → `Bound` (the StorageClass provisioner created a disk)
2. `postgres-0` goes `PodInitializing` → `Running` (initdb on first boot, which takes ~15s)
3. `taskflow-migrate-xxxxx` runs, completes, and stays as `Completed`

Stop the watch with `Ctrl+C` once `postgres-0` is `1/1`.

```powershell
kubectl get statefulset,pvc -n taskflow
kubectl get jobs -n taskflow
kubectl get pods -n taskflow
```

Two names to notice: the Pod is `postgres-0` (not a random hash), and the PVC is
`data-postgres-0` (derived from the template **and the ordinal**). That pairing is the
entire reason a StatefulSet exists.

---

## 2. Understand what you got

### The headless Service

```powershell
kubectl get svc postgres -n taskflow -o yaml | Select-String -Pattern "clusterIP"
# clusterIP: None      <- headless: no virtual IP, no load balancing
```

### Per-Pod DNS

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh
```

```sh
nslookup postgres          # the headless Service -> A record(s) for the Pod(s)
nslookup postgres-0.postgres.taskflow.svc.cluster.local   # ONE specific replica
exit
```

Compare this with what you saw for `taskflow-api` in step 05. The API's Service gave
you one **virtual IP** that load-balances. Postgres's gives you **addresses of actual
replicas** — which is what a database client needs, because "connect to whichever
replica is round-robined" is not how a database works.

That is the whole reason `serviceName` is a **required** field on a StatefulSet spec.

### The PVC is bound to the ordinal, not the Pod

```powershell
kubectl get pvc data-postgres-0 -n taskflow -o yaml
kubectl describe pvc data-postgres-0 -n taskflow
```

Look at `spec.volumeName` — that PV follows `postgres-0` forever.

### The migration Job

```powershell
kubectl describe job taskflow-migrate -n taskflow
kubectl logs job/taskflow-migrate -n taskflow
```

```
migration complete (version 0001_init) - taskflow schema is ready
```

Why a Job and not part of the API's startup? Because a schema migration must run
**exactly once, in order, before the new code serves traffic** — while the API runs as
3 (later up to 6) replicas that all start at once. A Job gives you completion
tracking, retries with backoff, and a deadline. Those are the properties you need, and
they are not the properties of a Deployment.

---

## 3. Connect the application to the database

The API Pods started before the database existed and are happily running with a broken
DB connection. Restart them so they reconnect:

```powershell
kubectl rollout restart deployment/taskflow-api deployment/taskflow-worker -n taskflow
kubectl rollout status deployment/taskflow-api -n taskflow
```

```powershell
kubectl port-forward -n taskflow svc/taskflow-api 8080:80
```

```powershell
# the database should now be reachable
curl.exe -s http://localhost:8080/api/config
# "db":{"host":"postgres", ..., "reachable":true}

# list tasks (empty)
curl.exe -s http://localhost:8080/api/tasks

# create one
curl.exe -s -X POST http://localhost:8080/api/tasks -H "Content-Type: application/json" -d '{\"title\":\"write the Kubernetes docs\"}'

# create a couple more
curl.exe -s -X POST http://localhost:8080/api/tasks -H "Content-Type: application/json" -d '{\"title\":\"review the NetworkPolicy step\"}' | Out-Null
curl.exe -s -X POST http://localhost:8080/api/tasks -H "Content-Type: application/json" -d '{\"title\":\"practice the rollback\"}' | Out-Null

# read them back
curl.exe -s http://localhost:8080/api/tasks
```

And confirm the worker consumed the notifications those writes enqueued:

```powershell
curl.exe -s http://localhost:8080/api/queue
kubectl logs -n taskflow -l app.kubernetes.io/name=taskflow-worker --tail=10 --prefix
```

**The full stack is now working end to end: Ingress → Service → 3 API Pods →
Postgres + Redis, with the worker draining the queue.**

---

## 4. Prove the data survives (the important part)

### 4a. Delete the Pod, keep the data

```powershell
# look inside the database via the exact replica, by its stable DNS name
kubectl run psql --rm -it --image=postgres:16-alpine -n taskflow -- `
  psql -h postgres-0.postgres -U taskflow -d taskflow -c "SELECT id, title, done FROM tasks ORDER BY id;"
```

```powershell
kubectl delete pod postgres-0 -n taskflow
kubectl get pod postgres-0 -n taskflow -w        # same name comes back
```

```powershell
kubectl run psql --rm -it --image=postgres:16-alpine -n taskflow -- `
  psql -h postgres-0.postgres -U taskflow -d taskflow -c "SELECT count(*) FROM tasks;"
```

**The row count is the same.** Stronger still: `postgres-0` mounts the *same PVC*
(`data-postgres-0`), because the claim is bound to the ordinal. Compare this with the
bare Pod from step 03, where the IP changed every time and nothing was retained.

### 4b. Where does the data physically live?

```powershell
kubectl get pvc data-postgres-0 -n taskflow -o jsonpath='{.spec.volumeName}'
kubectl get pv <that-name> -o yaml
```

Note `persistentVolumeReclaimPolicy`. If it is `Delete` (the default for most dynamic
provisioners), then **deleting the PVC deletes the disk**. That is exactly why
`Retain` is used for anything you cannot recreate.

### 4c. Scale down and up: the PVCs do not leave

```powershell
kubectl scale statefulset postgres -n taskflow --replicas=0
kubectl get pvc -n taskflow                     # data-postgres-0 STILL THERE
kubectl scale statefulset postgres -n taskflow --replicas=1
kubectl get pods -n taskflow -w
```

The PVC outlives the StatefulSet's Pods. That is deliberate data protection — and it
is also why clusters mysteriously accumulate disks nobody is using.

---

## 5. Break it

### 5a. Delete the StatefulSet — the data stays

```powershell
kubectl delete statefulset postgres -n taskflow
kubectl get pvc -n taskflow
```

`data-postgres-0` is still `Bound`. Kubernetes will not let a controller delete your
data as a side effect of deleting a workload object. Now re-apply and check that the
data is *still* there:

```powershell
kubectl apply -f labs/07-postgres.yaml
kubectl rollout status statefulset/postgres -n taskflow --timeout=180s
kubectl run psql --rm -it --image=postgres:16-alpine -n taskflow -- `
  psql -h postgres-0.postgres -U taskflow -d taskflow -c "SELECT count(*) FROM tasks;"
```

### 5b. Now delete the PVC — the data does not

```powershell
kubectl delete pvc data-postgres-0 -n taskflow
kubectl get pv
```

This is the destructive one, and it is worth doing once in a lab so you feel how easy
it is. In production the equivalent is a well-meaning cleanup script. **A PVC is a
disk, not a backup.** You need `pg_dump`/Velero *and* a rehearsed restore.

Recreate the data path afterwards:

```powershell
kubectl delete job taskflow-migrate -n taskflow --ignore-not-found
kubectl apply -f labs/07-postgres.yaml
kubectl rollout status statefulset/postgres -n taskflow --timeout=180s
kubectl wait --for=condition=complete job/taskflow-migrate -n taskflow --timeout=180s
kubectl rollout restart deployment/taskflow-api -n taskflow
```

### 5c. Why a Deployment would have been wrong here

Two replicas of Postgres as a Deployment would mean two processes initialising the
same volume, or two independent databases behind one load-balanced Service — with
writes going to whichever replica the connection happened to land on. There is no
"scaling Postgres horizontally" by editing a number.

```
[ ] A StatefulSet gives identity + storage.
[ ] It does NOT give replication, failover, or backups.
[ ] replicas: 3 of Postgres with no replication config = 3 unrelated databases.
```

---

## 6. Exercises

1. **Ordered creation.** Scale to 2 and watch: `postgres-0` becomes Ready *before*
   `postgres-1` is created. Then scale down and confirm `postgres-1` is deleted first.
2. **`podManagementPolicy: Parallel`.** Change it and scale to 2. What changes, and
   what would break with a real database?
3. **The `partition` canary.** With `replicas: 1` this is trivial, but write out the
   `updateStrategy.rollingUpdate.partition` patch you *would* use for a 3-replica
   StatefulSet, and explain the order the Pods update in.
4. **`PGDATA`.** Delete the `PGDATA` env var, delete the PVC, and re-apply. Read the
   error in the logs. Then restore it and explain why a subdirectory is needed.
5. **Missing `fsGroup`.** Remove `securityContext.fsGroup`, wipe the PVC, and watch
   the Pod fail. This is one of the most common "Kubernetes broke my database"
   tickets, and it has nothing to do with Kubernetes.
6. **Readiness probe.** Probes are `exec: pg_isready` here. Why is there **no**
   liveness probe on Postgres? (See the comment in the manifest, then read
   [probes](../concepts/11-probes-resources-and-scaling.md#2-probes).)
7. **A backup.** Run `pg_dump` from a throwaway Pod into a file on your machine and
   restore it into a new database. Without a tested restore you do not have a backup:
   ```powershell
   kubectl run pgdump --rm -it --image=postgres:16-alpine -n taskflow -- `
     pg_dump -h postgres-0.postgres -U taskflow -d taskflow > taskflow.sql
   ```
8. **Operator vs primitive.** Explain in two sentences why you would use CloudNativePG
   or the Zalando operator instead of this StatefulSet for a production database.
   ("Three am" should feature in your answer.)

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| PVC stuck `Pending` | no default StorageClass, or `WaitForFirstConsumer` waiting for a Pod | `kubectl describe pvc data-postgres-0`; `kubectl get storageclass` |
| Pod stuck `ContainerCreating`, "volume node affinity conflict" | PV in a different zone | use a `WaitForFirstConsumer` class (cloud) |
| `initdb: could not change permissions of directory` | missing `fsGroup` | set `securityContext.fsGroup: 999` |
| `initdb: directory "..." exists but is not empty` | `PGDATA` is the volume root and a `lost+found` exists | keep `PGDATA` in a subdirectory (as configured) |
| Migration Job `Failed`, `connection refused` | Postgres was not ready yet | wait for the StatefulSet, re-apply the Job |
| Migration Job succeeds but the API still 503s | the API Pods started before the DB | `kubectl rollout restart deployment/taskflow-api -n taskflow` |
| `psql: could not translate host name` | wrong DNS name | `postgres-0.postgres` from inside the namespace |
| Data gone after a redeploy | the PVC was deleted, or you edited the claim name | restore from backup; the PVC name is a stable contract |

---

## 8. Clean up

Keep the database and its data — steps 09, 10 and 12 all use it.

```powershell
kubectl get statefulset,pvc,jobs -n taskflow
```

---

**Checkpoint:** you can explain why `postgres-0` keeps its name and disk, why a
headless Service is required, why deleting a StatefulSet does not delete the data but
deleting the PVC does, and why a database should not be a Deployment.

**Next:** [Lab 08 — DaemonSet](08-daemonset.md) →
