# labs/ — the manifests you actually apply

These are the real, runnable YAML files for the guided lab in
[`../docs/lab/`](../docs/lab/). One file per lab step, plus the sample application.

The lab docs explain *why*; these files are the *what*. Read the comments in the
files — they are written as teaching material, not as noise.

---

## Apply order

| Lab step | File | Creates |
|---|---|---|
| 01 | *(none — cluster setup)* | cluster, ingress controller, metrics-server |
| 02 | `01-namespace.yaml` | Namespace `taskflow` (PTA baseline) + Namespace `node-agents` (PSA privileged), ServiceAccount, ResourceQuota, LimitRange |
| 02 | `02-config-secret.yaml` | ConfigMap, Secret |
| 03 | `03-pod.yaml` | a bare Pod (learning + debugging only) |
| 04 | `04-deployment.yaml` | the `taskflow-api` Deployment (3 replicas) |
| 05 | `05-service.yaml` | `taskflow-api` + `redis` Services, redis Deployment, worker Deployment |
| 06 | `06-ingress.yaml` | Ingress for `taskflow.local` and `api.taskflow.local` |
| 07 | `07-postgres.yaml` | headless Service, Postgres StatefulSet + PVC, migration Job |
| 08 | `08-daemonset.yaml` | node-log-agent DaemonSet — **in `node-agents`, not `taskflow`** |
| 09 | `09-networkpolicy.yaml` | default-deny + the allow rules (DNS, probes, ingress, data tier, egress) |
| 10 | `10-hpa-pdb.yaml` | HorizontalPodAutoscaler, PodDisruptionBudget |
| 11 | `11-broken-workloads.yaml` | **deliberately broken** workloads for debugging practice |
| 12 | `12-job-cronjob.yaml` | cleanup CronJob (+ a NetworkPolicy exercise) |

> **Why two namespaces?** `hostPath` volumes are forbidden by the `baseline` Pod
> Security level, and Pod Security Admission is enforced per namespace with no per-Pod
> exemption. The node agent needs host access, so it gets `node-agents` (enforced at
> `privileged`); the application stays hardened in `taskflow` (enforced at `baseline`).
> Step 08 has you prove this by trying to move it back.

Steps 01–06 work without a database. The API degrades gracefully: `/healthz`,
`/readyz`, `/api/whoami` and `/api/config` respond, while `/api/tasks` returns a
`503` explaining that the database is not reachable yet. That is intentional — it
lets you learn Pods, Deployments, Services and Ingress before any stateful
dependency exists.

---

## Apply everything at once

Once you have done the lab at least once, this is the fast path — the files are
numbered in dependency order, so a single pass works:

```powershell
kubectl apply -f labs/01-namespace.yaml
kubectl apply -f labs/02-config-secret.yaml
kubectl apply -f labs/04-deployment.yaml
kubectl apply -f labs/05-service.yaml
kubectl apply -f labs/06-ingress.yaml
kubectl apply -f labs/07-postgres.yaml
kubectl rollout status statefulset/postgres -n taskflow --timeout=180s
kubectl apply -f labs/08-daemonset.yaml
kubectl apply -f labs/09-networkpolicy.yaml
kubectl apply -f labs/10-hpa-pdb.yaml
```

Then wait for the migration Job and restart the API so it picks up the database:

```powershell
kubectl wait --for=condition=complete job/taskflow-migrate -n taskflow --timeout=180s
kubectl rollout restart deployment/taskflow-api deployment/taskflow-worker -n taskflow
```

> `03-pod.yaml` and `11-broken-workloads.yaml` are **not** part of a normal full
> install — one is a teaching example you delete immediately, the other is
> intentionally broken. Apply them only when the matching lab step asks for it.

---

## The sample application

`app/` contains the source for `taskflow-api:1.0.0`:

| File | Purpose |
|---|---|
| `app.py` | the whole service: HTTP API, worker, migration and cleanup modes |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | the container image |

Build and load it into your cluster (detailed in
[lab step 04](../docs/lab/04-deployment-and-image.md)):

```powershell
docker build -t taskflow-api:1.0.0 labs/app

# minikube: the node cannot see your local Docker daemon, so load the image in
minikube image load taskflow-api:1.0.0

# Docker Desktop: nothing to do — the cluster shares your Docker daemon
```

---

## Full teardown

```powershell
kubectl delete namespace taskflow
kubectl delete namespace node-agents
```

That removes every object in the namespace, **except** you should check for
leftover PersistentVolumes, because StatefulSet PVCs are deliberately not
garbage-collected with the StatefulSet:

```powershell
kubectl get pv
kubectl get pvc -n taskflow        # gone with the namespace
```

If you want the data kept for inspection, back the namespace up first, or use
`kubectl delete -f labs/ --ignore-not-found` selectively instead of deleting the
whole namespace.
