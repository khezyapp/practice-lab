# Lab 04 — Build the image, deploy it

**Goal:** package TaskFlow as an immutable image, run it as a Deployment with three
replicas, watch self-healing happen, perform a rolling update and roll it back.

**Read first:** [Deployment](../concepts/03-deployment.md).

**Time:** ~90 minutes. This is the longest and most important step.

---

## 1. Build the image

Look at the application first — it is short, and every later step depends on knowing
what it does.

```powershell
Get-Content labs/app/app.py | Select-Object -First 60
Get-Content labs/app/Dockerfile
```

Build it:

```powershell
docker build -t taskflow-api:1.0.0 labs/app
docker images taskflow-api
```

You now have a **tagged, immutable artifact**. Two properties matter from here on:

1. **The same image runs everywhere.** All environment differences arrive at runtime
   via the ConfigMap and Secret from step 02 — never as a rebuild.
2. **The tag is the version.** `1.0.0` is what you deploy, document, and roll back
   to. `latest` would destroy all three properties.

---

## 2. Make the image visible to the cluster

This is the step that catches everyone. The cluster does not use your Docker CLI's
image list — it has its own container runtime.

### minikube

```powershell
minikube image load taskflow-api:1.0.0 --profile taskflow
```

This copies the image into the node's runtime. It takes 10–60 seconds for a
Python-sized image and prints no progress — be patient, and verify with:

```powershell
minikube image ls --profile taskflow | Select-String taskflow-api
```

**Alternative** (faster for repeated builds): build *inside* the node's daemon by
pointing your Docker CLI at it:

```powershell
& minikube -p taskflow docker-env --shell powershell | Invoke-Expression
docker build -t taskflow-api:1.0.0 labs/app      # now builds inside the node
# ...and to go back to your normal daemon:
Remove-Item Env:DOCKER_HOST, Env:DOCKER_CERT_PATH, Env:DOCKER_TLS_VERIFY -ErrorAction SilentlyContinue
```

### Docker Desktop Kubernetes

Nothing to do. The cluster shares your Docker daemon, so the image is already there.

> If you skip this step you will get `ErrImagePull` / `ImagePullBackOff`, because the
> cluster will try (and fail) to pull `taskflow-api:1.0.0` from Docker Hub — it is
> not a public image.

---

## 3. Deploy it

```powershell
kubectl apply -f labs/04-deployment.yaml
kubectl get deploy,rs,pods -n taskflow -w
```

Stop the watch (`Ctrl+C`) once all three Pods are `Running` and `READY 1/1`.

```
NAME                            READY   UP-TO-DATE   AVAILABLE
deployment.apps/taskflow-api     3/3     3            3
NAME                                       DESIRED   CURRENT   READY
replicaset.apps/taskflow-api-7d9f8b6c4      3         3         3
NAME                                READY   STATUS    RESTARTS
pod/taskflow-api-7d9f8b6c4-abcde     1/1     Running   0
pod/taskflow-api-7d9f8b6c4-fghij     1/1     Running   0
pod/taskflow-api-7d9f8b6c4-klmno     1/1     Running   0
```

Three names, three IPs, one owner. **Look at the ownership chain** — this is the
object graph from the concept lesson, on your cluster:

```powershell
kubectl get pod -n taskflow -l app.kubernetes.io/name=taskflow-api -o jsonpath='{.items[0].metadata.ownerReferences}' | ConvertFrom-Json
# kind: ReplicaSet, name: taskflow-api-7d9f8b6c4  (the hash is the Pod template hash)

kubectl get rs taskflow-api-7d9f8b6c4 -n taskflow -o jsonpath='{.metadata.ownerReferences}' | ConvertFrom-Json
# kind: Deployment, name: taskflow-api
```

Pod → ReplicaSet → Deployment. You never created the ReplicaSet; that is the point.

---

## 4. Talk to the application

There is no Service yet (that is step 05), so use `port-forward` for now:

```powershell
kubectl port-forward -n taskflow deploy/taskflow-api 8080:8080
```

In another terminal:

```powershell
curl.exe -s http://localhost:8080/healthz
curl.exe -s http://localhost:8080/readyz
curl.exe -s http://localhost:8080/api/whoami
curl.exe -s http://localhost:8080/api/config
curl.exe -s http://localhost:8080/api/tasks
```

### What you should see, and why it matters

**`/healthz`** → `{"status":"ok","pod":"taskflow-api-...","uptime_s":...}` — this is
what the liveness probe calls. It checks **only the process**.

**`/readyz`** → `{"pod":...,"db_ok":false,...}` with HTTP **200**. The database does
not exist yet, and the Pod is *still Ready on purpose*. Read the explanation in
[the concept lesson](../concepts/11-probes-resources-and-scaling.md#2-probes): a
readiness probe that requires a dependency means one dependency blip removes **every**
replica from the Service at once. Step 10 makes you flip
`READINESS_REQUIRES_DB` to `true` and watch the trade-off.

**`/api/config`** → your ConfigMap and Secret values, as the container sees them.
Nothing here is baked into the image. Change a value in the ConfigMap and rebuild
nothing.

**`/api/tasks`** → `503` with:

```json
{ "error": "database unavailable",
  "detail": "OperationalError: connection failed: ...",
  "hint": "is postgres running and is the taskflow-secret correct?" }
```

**That 503 is the application working correctly.** It is telling you exactly what is
missing, which is what you want a real service to do. Step 07 fixes it.

---

## 5. Prove self-healing

```powershell
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api -o wide
kubectl delete pod <one-of-the-pod-names> -n taskflow
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api -w
```

A **new, differently-named Pod with a new IP** appears within seconds. Compare with
step 03, where deleting a Pod meant permanent loss.

Now do the same thing at the Deployment level and watch the count reconcile:

```powershell
kubectl scale deployment taskflow-api -n taskflow --replicas=5
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api -w
kubectl scale deployment taskflow-api -n taskflow --replicas=3
```

That is `spec.replicas` versus `status.replicas` — desired versus actual, converging.
You are watching the reconciliation loop from
[concept 01](../concepts/01-what-is-kubernetes.md#2-the-core-idea-declarative-desired-state).

---

## 6. Rolling update and rollback

Now practise the real deploy workflow: build a new version, ship it, verify it, and
be ready to undo it.

### Add a version endpoint, so the change is visible

Edit `labs/app/app.py` and add this route (anywhere above `if __name__ == "__main__"`):

```python
@app.get("/api/version")
def version():
    return jsonify(image_version="1.1.0", release=RELEASE, pod=POD_NAME)
```

Build and load it as **1.1.0**:

```powershell
docker build -t taskflow-api:1.1.0 labs/app
minikube image load taskflow-api:1.1.0 --profile taskflow     # Docker Desktop: skip
```

### Roll it out

```powershell
# in one terminal, watch the pods
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api -w
```

```powershell
# in another, trigger the rollout
kubectl set image deployment/taskflow-api -n taskflow api=taskflow-api:1.1.0
kubectl rollout status deployment/taskflow-api -n taskflow
```

Watch the sequence. With `maxSurge: 1, maxUnavailable: 0` you should briefly see
**4** Pods: the new ReplicaSet scales to 1 and waits for it to be **Ready** before the
old ReplicaSet scales down. One at a time, no dip in capacity.

```powershell
kubectl get rs -n taskflow                       # TWO ReplicaSets now
kubectl describe deploy taskflow-api -n taskflow | Select-String -Pattern "Replicas|Image" -Context 0,0
kubectl rollout history deployment/taskflow-api -n taskflow
```

Verify the new code is live:

```powershell
kubectl port-forward -n taskflow deploy/taskflow-api 8080:8080
curl.exe -s http://localhost:8080/api/version     # {"image_version":"1.1.0", ...}
```

> Why does `RELEASE` in the ConfigMap still say `1.0.0` while the image is `1.1.0`?
> Because those are **two different things** — the running image and the runtime
> configuration — and conflating them is a classic mistake. Fix it properly by
> updating the ConfigMap *and* restarting, and notice that the restart is what
> actually applies the change:
>
> ```powershell
> kubectl patch configmap taskflow-config -n taskflow --type merge -p '{"data":{"RELEASE":"1.1.0"}}'
> kubectl rollout restart deployment/taskflow-api -n taskflow    # env vars are fixed at container start
> ```

### Roll it back

```powershell
kubectl rollout undo deployment/taskflow-api -n taskflow
kubectl rollout status deployment/taskflow-api -n taskflow
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8080/api/version   # 404 -> back on 1.0.0
```

`rollout undo` does not "re-deploy an old image" — it scales the **previous
ReplicaSet** back up. That is why the old ReplicaSet is kept at 0 replicas, and why
`revisionHistoryLimit` exists.

---

## 7. Break it

### 7a. A tag that does not exist

```powershell
kubectl set image deployment/taskflow-api -n taskflow api=taskflow-api:9.9.9
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api -w
kubectl describe pod <new-pod> -n taskflow | Select-String -Pattern "Events" -Context 0,10
```

`ImagePullBackOff`. Now — **this is the interesting part** — what happened to your
availability?

```powershell
kubectl get deploy taskflow-api -n taskflow
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api
```

With `maxUnavailable: 0`, the rollout **stops**: the 3 old Pods keep serving traffic
and the new Pod never becomes Ready. A bad image cannot take your service down. That
is what those two fields are for.

Recover:

```powershell
kubectl rollout undo deployment/taskflow-api -n taskflow
```

### 7b. A broken readiness probe

```powershell
kubectl patch deployment taskflow-api -n taskflow --type json `
  -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/does-not-exist"}]'
kubectl rollout status deployment/taskflow-api -n taskflow --timeout=60s
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api
```

The new Pods are `Running` but `0/1` — **not Ready**, therefore never added to a
Service's endpoints, therefore receiving no traffic. Liveness never fires, so nothing
is restarted. Silent from the Pod's perspective, total outage from the user's.

This is the failure mode you will meet most often in production. Put it back:

```powershell
kubectl rollout undo deployment/taskflow-api -n taskflow
```

---

## 8. Exercises

1. Delete the whole Deployment with `--cascade=orphan`:
   `kubectl delete deploy taskflow-api -n taskflow --cascade=orphan`
   What happens to the Pods? (`kubectl get pods`) Now delete the ReplicaSet. What
   still owns the Pods? This shows `ownerReferences` and garbage collection directly.
2. `kubectl rollout pause` a rollout after one new Pod is Ready, verify with
   `port-forward`, then `resume`. That is a canary release with one command.
3. Set `progressDeadlineSeconds: 30` and roll out a broken image. How long until the
   Deployment reports failure, and where does that appear?
4. Change `maxUnavailable` to `1` and `maxSurge` to `0`, then roll out `1.1.0` while
   polling `/healthz` in a loop. Can you observe a gap? This is why `maxUnavailable: 0`
   matters — and why probes matter even more.
5. Add `topologySpreadConstraints` (see the concept lesson) and see where the Pods
   land on a multi-node cluster.
6. `kubectl get pod <pod> -o yaml` and find `status.conditions` — `PodScheduled`,
   `Initialized`, `ContainersReady`, `Ready`. The readiness you rely on is literally
   the `Ready` condition.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ErrImagePull` / `ImagePullBackOff` on your own image | the node cannot see your local image | `minikube image load ...`, or build with `minikube image build` |
| Pods `Pending` with "Insufficient cpu" | 3 × 100m plus everything else does not fit | lower requests, or resize the cluster |
| Rollout stuck at 1/3 forever | new Pods never become Ready | `kubectl describe pod` → probe events; check `/readyz` by hand |
| `RELEASE` still shows the old value | ConfigMap change needs a restart | `kubectl rollout restart` |
| Rollout created 0 new Pods | the Pod template did not change | only template changes create a new ReplicaSet |
| Pods land on the same node | single-node cluster, or `preferred` anti-affinity | expected locally; use a multi-node cluster to see spreading |

---

## 10. Clean up

Keep everything. Leave the API at a healthy 3 replicas running `1.0.0`:

```powershell
kubectl get deploy taskflow-api -n taskflow
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api
```

---

**Checkpoint:** you can delete a Pod and lose nothing, scale up and down, ship a new
image without downtime, and recover from a bad one. And you know that a Deployment
does not run Pods — it manages ReplicaSets that manage Pods.

**Next:** [Lab 05 — Service](05-service.md) →
