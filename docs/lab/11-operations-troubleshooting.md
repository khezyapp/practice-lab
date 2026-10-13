# Lab 11 — Debugging like an operator

**Goal:** build a repeatable triage method and use it on six different broken workloads
that look superficially similar and have completely different causes.

**Read first:** [What happens on apply](../architecture/02-what-happens-on-apply.md) and
[the request lifecycle](../architecture/01-request-lifecycle.md).

**Time:** ~90 minutes.

---

## 1. The method

Do not improvise. Every Kubernetes problem is one of five layers, and there is one
command that identifies which:

| Layer | Question | Command that answers it |
|---|---|---|
| 1. Scheduling | did a Node accept the Pod? | `kubectl describe pod` → Events (`Pending` reason) |
| 2. Image | did the container runtime get the image? | `kubectl describe pod` → Events (`ErrImagePull`) |
| 3. Container creation | could the container be configured and started? | `kubectl describe pod` → container status (`CreateContainerConfigError`) |
| 4. Application | did the process stay up and pass probes? | `kubectl logs`, `--previous`, `RESTARTS`, `Unready` events |
| 5. Networking | can traffic reach it? | `kubectl get endpointslices`, `describe svc`, `describe ingress` |

**`kubectl describe pod` answers layers 1–4 in one command.** Read the Events section
bottom-up and the container state, and you will name the layer in seconds. Then:

```powershell
kubectl get events -n taskflow --sort-by=.lastTimestamp | Select-Object -Last 20
```

One habit worth building on day one: **`describe` before `delete`.** Deleting a broken
Pod that has a controller behind it destroys the evidence and recreates the same
problem — you will have learned nothing and lost the Events.

---

## 2. Apply the six problems

```powershell
kubectl apply -f labs/11-broken-workloads.yaml
Start-Sleep -Seconds 60
kubectl get deploy,pods -n taskflow
```

You will see something like:

```
NAME                              READY   UP-TO-DATE   AVAILABLE
deployment.apps/broken-config       0/1     1            0
deployment.apps/broken-image        0/1     1            0
deployment.apps/broken-probe        0/1     1            0
deployment.apps/broken-readiness    0/1     1            0     <-- and NO restarts
deployment.apps/broken-scheduling   0/1     1            0
deployment.apps/taskflow-api        3/3     3            3

NAME                                    READY   STATUS             RESTARTS
pod/broken-config-...                    0/1     CreateContainerConfigError   0
pod/broken-image-...                     0/1     ImagePullBackOff             0
pod/broken-probe-...                     0/1     CrashLoopBackOff             4
pod/broken-readiness-...                 0/1     Running                      0     <-- Running but not Ready
pod/broken-scheduling-...                0/1     Pending                      0
```

Five Deployments are stuck at `0/1`, and they are stuck for five different reasons. The
two that deserve attention before you read on:

- `broken-probe` is `CrashLoopBackOff` with **restarts**, so the container is starting and
  being killed.
- `broken-readiness` is `Running` with **zero restarts**, so the container is fine and
  something else is withholding your traffic.

> **Stop.** Before reading the next section, work out the cause of each one yourself,
> using only `kubectl describe`, `kubectl logs` and `kubectl get events`. The value of
> this exercise is entirely in doing the diagnosis. The answers are below.

---

## 3. Diagnose each one

### 3a. `broken-image` — the runtime never got the image

```powershell
kubectl describe pod -n taskflow -l app.kubernetes.io/name=broken-image
```

```
Events:
  Failed    Failed to pull image "taskflow-api:9.9.9": ... manifest unknown
  Normal    BackOff   Back-off pulling image "taskflow-api:9.9.9"
```

**Layer 2.** The tag does not exist. Nothing about your code, config or network is
involved. Note the state machine: `ErrImagePull` on the first failure, then
`ImagePullBackOff` while the kubelet retries with increasing delay.

```powershell
kubectl get pod -n taskflow -l app.kubernetes.io/name=broken-image -o jsonpath='{.items[0].status.containerStatuses[0].state}' | ConvertFrom-Json
```

Fix: use a tag that exists. **This is the most common failure in the whole list** —
usually because a locally built image was never loaded into the cluster
(`minikube image load`), which is a `docker images` versus cluster-runtime problem, not
a Kubernetes one.

### 3b. `broken-config` — the container cannot be created at all

```powershell
kubectl describe pod -n taskflow -l app.kubernetes.io/name=broken-config
kubectl logs -n taskflow -l app.kubernetes.io/name=broken-config
```

```
Warning  Failed  Error: couldn't find key THIS_KEY_DOES_NOT_EXIST in ConfigMap taskflow/...
```

**Layer 3.** `kubectl logs` returns *nothing*, and that is the diagnostic clue: if there
are no logs, the process never started, so the problem is upstream of the application.
`CreateContainerConfigError` means the kubelet could not assemble the container's
environment or mounts.

The usual causes, in order:

1. a `configMapKeyRef`/`secretKeyRef` key that does not exist,
2. a ConfigMap/Secret name that does not exist,
3. a missing volume,

```powershell
kubectl get configmap taskflow-config -n taskflow -o jsonpath='{.data}' | ConvertFrom-Json
```

Fix: the reference must match the object exactly. `optional: true` makes a missing key
non-fatal — appropriate for genuinely optional config, and a way to hide a bug
everywhere else.

### 3c. `broken-probe` — the app is fine, the probe is not

```powershell
kubectl get pod -n taskflow -l app.kubernetes.io/name=broken-probe
kubectl logs -n taskflow -l app.kubernetes.io/name=broken-probe --tail=5
kubectl logs -n taskflow -l app.kubernetes.io/name=broken-probe --previous --tail=5
kubectl describe pod -n taskflow -l app.kubernetes.io/name=broken-probe | Select-String -Pattern "Unhealthy" -Context 0,2
```

```
Warning  Unhealthy  Liveness probe failed: HTTP probe failed with statuscode: 404
Warning  Killing    Container api failed liveness probe, will be restarted
```

**Layer 4** — but caused by configuration, not by the application. The logs are clean:
gunicorn started, served requests, was killed. `RESTARTS` climbing with a healthy-looking
log is the signature of a failed **liveness** probe.

The trap: people "fix" this by deleting and redeploying, or by raising
`failureThreshold`, or by removing the probe. The correct fix is that the probe path
must exist and return 2xx:

```powershell
kubectl exec -n taskflow deploy/taskflow-api -- curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/healthz
```

Compare the probe path in `labs/11-broken-workloads.yaml` with `/healthz` here.

### 3d. `broken-readiness` — the silent one

```powershell
kubectl get pod -n taskflow -l app.kubernetes.io/name=broken-readiness
# Running, but READY 0/1

kubectl describe pod -n taskflow -l app.kubernetes.io/name=broken-readiness | Select-String -Pattern "Unhealthy" -Context 0,2
kubectl logs -n taskflow -l app.kubernetes.io/name=broken-readiness --tail=5
```

```
Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 404
```

**Layer 4 — and this is the hardest one to spot.**

- No restarts. No crash. No `Unhealthy` event on a *liveness* probe. Logs are clean.
- The Deployment shows `0/1` rather than `Ready` — but critically, the Pod itself is
  `STATUS: Running`. Nothing looks "broken", which is why people stare past it.
- Because the Pod never becomes Ready, it is **never added to a Service's endpoints**.

If this Deployment were behind a Service, users would get **503s while `kubectl get pods`
showed a cheerful `Running`**. This is the failure mode I would bet on meeting most often
in a real job, and it is the subject of the "probe mismatch" experiment in step 04.

```powershell
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=broken-service
```

Fix: make `/readyz` (or whatever your probe hits) return 200 only when the Pod can
actually serve traffic — and then make the *liveness* probe a separate, dependency-free
check.

### 3e. `broken-scheduling` — nothing is wrong with the Pod

```powershell
kubectl describe pod -n taskflow -l app.kubernetes.io/name=broken-scheduling
```

```
Events:
  Warning  FailedScheduling  0/1 nodes are available: 1 node(s) didn't match Pod's node affinity/selector.
```

**Layer 1.** The Pod was never given to a Node, so nothing was pulled, created or
started. Note that the container status is empty — there is nothing to inspect, which
tells you immediately that you are in the scheduling layer, not the container layer.

The four common reasons a Pod is `Pending`, all visible in that one Events line:

| Message | Cause |
|---|---|
| `Insufficient cpu` / `Insufficient memory` | no Node with enough free *requests* |
| `didn't match Pod's node affinity/selector` | `nodeSelector`/affinity matches nothing |
| `had untolerated taint` | the Node is tainted (very often control-plane) |
| `pod has unbound immediate PersistentVolumeClaims` | storage is not ready |

```powershell
kubectl get nodes --show-labels
kubectl describe node taskflow | Select-String -Pattern "Allocated resources" -Context 0,12
```

That second command shows you what is actually consumed on the Node, which is how you
tell "no capacity" from "wrong selector".

### 3f. `broken-service` — 100% valid, 0 endpoints

```powershell
kubectl describe svc broken-service -n taskflow | Select-String -Pattern "Selector|Endpoints"
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=broken-service
```

**Layer 5.** The Service is valid, has a ClusterIP, resolves in DNS, and routes nothing —
because its selector does not match any Pod's labels.

```powershell
kubectl get pods -n taskflow --show-labels | Select-String "broken"
```

The fix is one character. **The lesson is the habit:** when a Service does not work,
check Endpoints *before* the Ingress, the firewall, the app or anything else. Almost every
"is it down?" incident starts with an empty EndpointSlice.

---

## 4. The summary table you should be able to reproduce from memory

| Status / symptom | Layer | First command | Typical cause |
|---|---|---|---|
| `Pending` | scheduling | `describe pod` → `FailedScheduling` | resources, selector, taint, PVC |
| `ImagePullBackOff` | image | `describe pod` → `Failed` | wrong tag, private registry, arch mismatch |
| `CreateContainerConfigError` | creation | `describe pod` + no logs | missing ConfigMap/Secret/key |
| `CrashLoopBackOff` | application | `logs --previous` | app crash, bad config, failed liveness |
| `Running` + `0/1` | application | `describe pod` → `Unhealthy` | readiness probe failing |
| `Running` + `0/1` + no events | networking | `get endpointslices` | selector mismatch (`describe svc`) |
| `Terminating` forever | lifecycle | `describe pod` → finalizers | long grace period, stuck finalizer, unresponsive runtime |
| Works in the cluster, not outside | networking | `describe ingress` | 404 (no rule), 503 (no endpoints), 502 (bad port) |

Notice that **half of these are not application problems at all.** That is the point of
the exercise: "the app is broken" is a conclusion you earn after ruling out four other
layers, not a starting hypothesis.

---

## 5. Break the working system, then fix it without looking at this page

Tear down the training wheels and apply the method to the real app:

```powershell
# one of these at random - diagnose it with the five-layer method
kubectl scale statefulset postgres -n taskflow --replicas=0
# or
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"selector":{"app.kubernetes.io/name":"nope"}}}'
# or
kubectl patch deployment taskflow-api -n taskflow --type json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"taskflow-api:2.0.0"}]'
```

For each one, write down (before checking): which layer, which command proved it, and
what the fix is. Then verify.

```powershell
# restore everything
kubectl scale statefulset postgres -n taskflow --replicas=1
kubectl apply -f labs/05-service.yaml
kubectl apply -f labs/04-deployment.yaml
kubectl get pods -n taskflow -w
```

---

## 6. Exercises

1. **`kubectl get events` with a filter.** Find every `Warning` in the namespace in the
   last hour, sorted:
   `kubectl get events -n taskflow --field-selector type=Warning --sort-by=.lastTimestamp`
2. **A stuck `Terminating` Pod.** Create a Pod with a `preStop` that sleeps 300 and a
   `terminationGracePeriodSeconds` of 300, delete it, and watch it hang. Then force it:
   `kubectl delete pod <name> --grace-period=0 --force`. Explain why forcing is dangerous
   with a StatefulSet (a rescheduled Pod can end up with two writers on one volume).
3. **`kubectl debug`.** Attach an ephemeral container to a running Pod that has no shell:
   `kubectl debug -it <pod> --image=busybox:1.36 --target=<container>`. When is this
   better than `kubectl exec`? (Distroless images, crashed containers, no shell in the
   image.)
4. **`kubectl debug` a Node.** `kubectl debug node/taskflow -it --image=busybox:1.36`
   gives you a shell in a Pod with the Node's filesystem at `/host`. This is how you
   investigate kubelet-level problems.
5. **A NetworkPolicy that looks fine.** With step 09 applied, add a policy selecting
   `app.kubernetes.io/name: taskflow-api` with `policyTypes: [Ingress]` and no rules.
   Predict what happens, then verify. (The empty policy *isolates* the Pod for that
   direction — this is the "adding a policy is not always additive" trap in reverse.)
6. **`kubectl get --raw`.** Bypass kubectl's formatting and hit the API directly:
   `kubectl get --raw /api/v1/namespaces/taskflow/pods | ConvertFrom-Json`.
7. **Write your own runbook.** For each of the six broken workloads, write a two-line
   entry: symptom → command. That is the beginning of the on-call document you will
   actually use.

---

## 7. Clean up

```powershell
kubectl delete -f labs/11-broken-workloads.yaml
kubectl get pods -n taskflow
kubectl get deploy -n taskflow
```

Confirm only the real workloads remain: `taskflow-api`, `taskflow-worker`, `redis`,
`postgres-0` in `taskflow`, and `node-log-agent` in `node-agents`.

```powershell
kubectl get pods -n node-agents
```

---

**Checkpoint:** given an unfamiliar broken workload, you can name the layer within a
minute, using one command per layer, and you never `delete` before you `describe`.

**Next:** [Lab 12 — Job & CronJob](12-job-and-cronjob.md) →
