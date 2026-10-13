# Lab 10 — Probes, HPA, PDB, rollouts

**Goal:** audit the reliability of what you have built, watch the autoscaler react to
real load, and see a PodDisruptionBudget block a node drain.

**Read first:** [Probes, Resources & Scaling](../concepts/11-probes-resources-and-scaling.md).

**Time:** ~75 minutes.

---

## 1. Audit the workload you already have

Do not create anything yet. Instead, inspect what you deployed in step 04 and answer
each question from the cluster, not from the YAML.

```powershell
$p = kubectl get pod -n taskflow -l app.kubernetes.io/name=taskflow-api -o jsonpath='{.items[0].metadata.name}'
kubectl get pod $p -n taskflow -o jsonpath='{.status.qosClass}'
kubectl get pod $p -n taskflow -o jsonpath='{.spec.containers[0].resources}'
kubectl get pod $p -n taskflow -o jsonpath='{.status.conditions}'  | ConvertFrom-Json | Select-Object type,status
kubectl describe deploy taskflow-api -n taskflow | Select-String -Pattern "StrategyType|MaxUnavailable|MaxSurge"
```

The checklist from the concept lesson, answered by the cluster:

```
[x] requests AND limits on every container            -> Burstable, 100m/128Mi
[x] readinessProbe ...                                -> /readyz every 5s
[x] livenessProbe that checks ONLY the process        -> /healthz every 10s
[x] startupProbe                                      -> 30 x 2s = up to 60s to boot
[x] terminationGracePeriodSeconds + preStop           -> 30s + sleep 5
[x] replicas >= 2, spread across nodes                -> 3 replicas + preferred anti-affinity
[?] PodDisruptionBudget matching the replica count     -> not yet (this step)
[?] HPA                                                -> not yet (this step)
```

Notice `QoS: Burstable`. Now ask what it would take to be `Guaranteed`, and why a
database wants that (eviction order under node memory pressure):

```powershell
kubectl get pod postgres-0 -n taskflow -o jsonpath='{.status.qosClass}'
# Burstable -> requests != limits. A Guaranteed Postgres is a deliberate design choice.
```

---

## 2. Add the HPA and the PDB

```powershell
kubectl apply -f labs/10-hpa-pdb.yaml
```

```powershell
kubectl get hpa,pdb -n taskflow
kubectl describe hpa taskflow-api -n taskflow
```

Give it a minute, then check the targets:

```
NAME           REFERENCE                 TARGETS         MINPODS   MAXPODS   REPLICAS
taskflow-api   Deployment/taskflow-api   cpu: 3%/70%     2         6         3
```

`TARGETS` must be a real percentage. If it says `<unknown>`:

- metrics-server is not running or cannot reach the kubelets
  (`kubectl logs -n kube-system deploy/metrics-server`), **or**
- the target Deployment has no CPU **requests** — utilization is a percentage *of the
  request*, so with no request there is no denominator.

> A subtle interaction you should know about: the LimitRange from step 02 injects
> `requests: 100m` into any container that omits it. That means a Deployment with no
> resources in its YAML **still** gets a request, so the HPA works. The usual "HPA
> cannot scale, no requests" failure is masked here — and `--dry-run=server` will show
> you the injected values so you can see what the API server really stored.

---

## 3. Make it scale

HPA scales on **CPU**, and this API is very cheap per request, so a plain `curl` loop
will not move the needle. That is itself a lesson: **CPU is often the wrong signal.**
For the demo, apply load directly inside the containers.

```powershell
# start a CPU burner in every API replica (backgrounded, returns immediately)
kubectl exec -n taskflow deploy/taskflow-api -- sh -c "nohup python -c 'import time;[i*i for i in iter(int,1)]' >/dev/null 2>&1 &"

# watch usage rise toward the 500m limit
kubectl top pods -n taskflow -l app.kubernetes.io/name=taskflow-api --containers

# watch the HPA react
kubectl get hpa -n taskflow -w
```

Within a minute or two you should see the replica count climb from 3 toward 6. The new
Pods start clean (no burner inside them), so the average utilization falls as they come
up — which is exactly the feedback loop working: the HPA drives the *average* utilization
of the target Deployment back toward 70% of the 100m request.

```
NAME           TARGETS         MINPODS  MAXPODS  REPLICAS
taskflow-api   cpu: 480%/70%   2        6        3
taskflow-api   cpu: 480%/70%   2        6        4
taskflow-api   cpu: 480%/70%   2        6        6
```

Then watch the Deployment and the Pods:

```powershell
kubectl get deploy taskflow-api -n taskflow -w
kubectl get pods -n taskflow -o wide -l app.kubernetes.io/name=taskflow-api
```

Stop the burners by replacing the Pods, and watch scale-**down** be slow on purpose:

```powershell
kubectl rollout restart deployment/taskflow-api -n taskflow
kubectl get hpa -n taskflow -w      # takes ~5 min to come back to 3 (stabilizationWindowSeconds)
```

Two things to take away:

1. **Scale-up is fast, scale-down is deliberately slow.** The 300-second stabilization
   window exists so a 30-second traffic dip does not delete your capacity.
2. **The HPA scales replicas, not Nodes.** In a real cluster, if all Nodes are full the
   new Pods go `Pending` and a *cluster autoscaler* (Cluster Autoscaler / Karpenter) adds
   a Node. Two different components doing two different jobs — a very common interview
   question.

### Look at throttling while you are here

While the burner was running, the containers were pinned at the **CPU limit**:

```powershell
kubectl describe pod $p -n taskflow | Select-String -Pattern "Limits|Requests" -Context 0,2
kubectl top pod $p -n taskflow --containers
```

An app at exactly its limit is being **throttled** — it is not failing, it is getting
slow at an unpredictable and hard-to-debug rate. This is why many teams set CPU
requests but no CPU limits: requests give scheduling fairness, limits cause latency
cliffs. Memory is the opposite — always set a memory limit, because exceeding it is an
`OOMKilled`, not a slowdown, and without a limit one leaking Pod can take down a Node.

---

## 4. Break it — make readiness depend on the database

Step 02 left you a switch for exactly this. Flip it:

```powershell
kubectl patch configmap taskflow-config -n taskflow --type merge -p '{"data":{"READINESS_REQUIRES_DB":"true"}}'
kubectl rollout restart deployment/taskflow-api -n taskflow
kubectl rollout status deployment/taskflow-api -n taskflow
```

The API is Ready (the database is up). Now break the database:

```powershell
kubectl scale statefulset postgres -n taskflow --replicas=0
kubectl get pods -n taskflow -w
```

Watch the cascade, in order — this is the most valuable six lines in the whole lab:

1. `postgres-0` terminates; the `postgres` Service loses its endpoints.
2. Every API Pod's `/readyz` starts returning **503**.
3. Every API Pod becomes **`0/1`** — Running, but not Ready.
4. The `taskflow-api` **Service's endpoints go empty**.
5. The Ingress returns **503** to every user.
6. Nothing is restarted (no liveness involvement) — the Pods all *look* fine.

```powershell
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=taskflow-api
curl.exe -i -s -H "Host: taskflow.local" http://localhost:8080/api/tasks | Select-Object -First 3
```

**One dependency blip turned into a total outage, because readiness checked a
dependency.** This is not an argument against readiness probes that check dependencies
(it is a genuine trade-off, and many teams do it deliberately). It is an argument
*for* knowing which choice you made, and for the rule that **liveness must never check
a dependency**: if it did, all 3 replicas would now be restarting in a loop, hammering
a database that is already down. That is a cascading failure, and you have now seen
why people fear it.

Put it back:

```powershell
kubectl scale statefulset postgres -n taskflow --replicas=1
kubectl rollout status statefulset/postgres -n taskflow --timeout=180s
kubectl patch configmap taskflow-config -n taskflow --type merge -p '{"data":{"READINESS_REQUIRES_DB":"false"}}'
kubectl rollout restart deployment/taskflow-api -n taskflow
```

---

## 5. Break it — the PDB blocks maintenance

```powershell
kubectl get pdb -n taskflow
kubectl describe pdb taskflow-api -n taskflow
```

```
Min available:      2
Allowed disruptions: 1
```

Now try to drain the node:

```powershell
kubectl drain taskflow --ignore-daemonsets --delete-emptydir-data
```

Expect it to **block** with a message like:

```
Cannot evict pod as it would violate the pod's disruption budget.
error: unable to drain node "taskflow" ...
```

**That is the PDB working.** On a single-node cluster, evicting even one API Pod drops
you below `minAvailable: 2` with nowhere for the replacement to go — so Kubernetes
refuses to proceed rather than silently taking you below your stated availability.

Two operational lessons:

1. A PDB that cannot be satisfied **stalls cluster upgrades**. `minAvailable: 2` with
   `replicas: 2` means nobody can ever drain that node. Always leave headroom.
2. The PDB only constrains **voluntary** disruption (`kubectl drain`, upgrades,
   `kubectl evict`). It does nothing for crashes, OOM kills or node failures — those are
   handled by having enough replicas and anti-affinity.

```powershell
kubectl uncordon taskflow
kubectl get nodes
```

---

## 6. Exercises

1. **`kubectl diff` before you change anything.** Edit `labs/10-hpa-pdb.yaml`
   (`minReplicas: 3`) and run `kubectl diff -f labs/10-hpa-pdb.yaml` before applying.
   Then `kubectl apply -f labs/10-hpa-pdb.yaml`. This is the review workflow you want.
2. **The startup probe's real value.** Change the liveness `initialDelaySeconds` to 1
   and `failureThreshold` to 1, and set the image tag to `1.0.0` (a fresh pull). With no
   `startupProbe`, a slow boot would be killed in a loop. Explain the sequence — and why
   `startupProbe` lets you keep liveness tight *without* that risk.
3. **Scale on the queue, not on CPU.** The worker is the correct thing to autoscale, and
   the correct signal is Redis queue depth, which the HPA cannot see. Write out how you
   would do it with KEDA (`ScaledObject` with a Redis trigger, `minReplicaCount: 0`).
   Note that scale-**to-zero** is only safe because the queue is durable — with an
   ephemeral queue those jobs would be lost.
4. **`progressDeadlineSeconds`.** Set it to 30, roll out a broken image tag, and watch
   the Deployment report `ProgressDeadlineExceeded`. Where does that appear, and what
   does `kubectl rollout status` say?
5. **Resource pressure.** Set the API memory limit to `160Mi` and run a memory hog
   (`python -c "x='a'*10**6; y=[x]*2000"`) inside a Pod via `kubectl exec`. Observe
   `OOMKilled` (exit 137) and the restart. Then look at
   `kubectl describe pod | Select-String "Last State"`.
6. **Node eviction order.** With `postgres-0` as `Burstable` and the API as `Burstable`,
   which Pod does the kubelet evict first when a Node runs out of memory, and why?
   (Whichever exceeds its requests most, with `BestEffort` first and `Guaranteed` last.
   This is the argument for `Guaranteed` on databases.)
7. **The HPA and the quota.** The namespace quota is 4 CPU requests. The HPA can reach
   6 replicas × 100m = 600m, so it will not hit the wall — but what if `maxReplicas` were
   40? Where would you see the failure, and which object would report it?

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| HPA `TARGETS: <unknown>` | metrics-server down, or no CPU requests | check metrics-server; add requests |
| HPA will not scale above `minReplicas` | CPU is genuinely idle (this API is cheap) | use a real signal, or a load generator |
| HPA at `maxReplicas`, Pods `Pending` | no Node capacity | a cluster autoscaler is what you are missing |
| Pods restarting repeatedly, no app errors | liveness probe too aggressive | raise `failureThreshold` / use `startupProbe` |
| `OOMKilled`, exit code 137 | memory limit too low | raise the limit; check for a leak with `kubectl top` |
| `kubectl drain` hangs | PDB, or a DaemonSet without `--ignore-daemonsets` | `kubectl get pdb -n taskflow`; fix the budget |
| Trigger `configmap` change had no effect | env vars are fixed at container start | `kubectl rollout restart` |

---

## 8. Clean up

Everything here stays — but leave the namespace in a healthy state:

```powershell
kubectl get deploy,hpa,pdb -n taskflow
kubectl get pods -n taskflow
kubectl get nodes                  # must be Ready and NOT SchedulingDisabled
```

If you drained the node earlier and forgot to uncordon it, no new Pods can be
scheduled — a classic self-inflicted outage:

```powershell
kubectl uncordon taskflow
```

---

**Checkpoint:** you can explain requests vs limits, why CPU throttling differs from
OOMKilled, why liveness must never check a dependency, what a PDB actually protects
against, and why "HPA scaled but Pods are Pending" means you need a node autoscaler.

**Next:** [Lab 11 — Debugging like an operator](11-operations-troubleshooting.md) →
