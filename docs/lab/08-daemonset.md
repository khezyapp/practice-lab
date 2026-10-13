# Lab 08 — DaemonSet

**Goal:** run exactly one Pod on every Node, automatically, and understand the
`hostPath` / `tolerations` / `nodeSelector` trio that node agents depend on.

**Read first:** [DaemonSet](../concepts/08-daemonset.md).

**Time:** ~45 minutes.

---

## 1. Apply it

> **Namespace note — and it is a lesson, not a detail.** This DaemonSet lives in
> `node-agents`, **not** `taskflow`. `hostPath` volumes are forbidden by the
> `baseline` Pod Security level that the application namespace enforces, and Pod
> Security Admission has no per-Pod exemption. A node agent needs host access by
> definition, so privileged infrastructure gets its own namespace. Every command in
> this step therefore uses `-n node-agents`.
>
> If you want to see the rejection for yourself, copy the manifest, change
> `namespace:` back to `taskflow`, and apply it:
> `violates PodSecurity "baseline:latest": hostPath volumes`.

```powershell
kubectl apply -f labs/08-daemonset.yaml
kubectl get daemonset -n node-agents
kubectl get pods -n node-agents -o wide -l app.kubernetes.io/name=node-log-agent
```

```
NAME             DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR
node-log-agent   1         1         1       1            1           kubernetes.io/os=linux
```

**`DESIRED` is not a number you chose.** It is the number of eligible Nodes. Compare
that with the Deployments you have been writing, where `replicas` is your decision.

```powershell
kubectl get nodes --show-labels
```

The DaemonSet's `nodeSelector` (`kubernetes.io/os: linux`) plus its `tolerations` are
what make `DESIRED` come out to 1 here. Change either and watch `DESIRED` change:

```powershell
# a selector that matches nothing
kubectl patch daemonset node-log-agent -n node-agents --type json `
  -p '[{"op":"replace","path":"/spec/template/spec/nodeSelector","value":{"kubernetes.io/os":"windows"}}]'
kubectl get daemonset -n node-agents       # DESIRED 0, no Pods anywhere

kubectl apply -f labs/08-daemonset.yaml    # restore
```

`DESIRED 0` is a real and easily-missed outage: your node agents are simply not
running, and nothing is red. Meaningful for `node-exporter` (the metrics you alert
on) and for the CNI (Pod networking).

---

## 2. See what a node agent sees

```powershell
kubectl logs -n node-agents -l app.kubernetes.io/name=node-log-agent --tail=20
```

```
[agent] starting node=taskflow pod=node-log-agent-xxxxx
[agent] 2026-09-20T10:14:02 node=taskflow visible-log-entries=12
```

The agent **knows which Node it is on**, because the Pod template injects
`spec.nodeName` and `status.hostIP` via the downward API. That is standard practice
for DaemonSets and it is why DaemonSets are easy to reason about operationally: one
Pod, one Node, labelled with which one.

```powershell
kubectl exec -n node-agents $(kubectl get pod -n node-agents -l app.kubernetes.io/name=node-log-agent -o jsonpath='{.items[0].metadata.name}') -- ls /host/var/log | Select-Object -First 15
```

You are reading the **Node's** `/var/log` from inside a container. That is possible
only because of the `hostPath` volume — and it is mounted `readOnly: true`, which is
the difference between an observability agent and a Node compromise.

```powershell
kubectl get pod -n node-agents -l app.kubernetes.io/name=node-log-agent -o jsonpath='{.items[0].spec.volumes}' | ConvertFrom-Json
```

---

## 3. Prove "one per Node", including Nodes that join later

On a single-node cluster you cannot see the interesting behaviour. If you have the
resources, create a 3-node cluster and apply the same file:

```powershell
minikube start --profile taskflow3 --nodes=3 --cpus=4 --memory=5120
kubectl --context taskflow3 apply -f labs/01-namespace.yaml
kubectl --context taskflow3 apply -f labs/08-daemonset.yaml
kubectl --context taskflow3 get pods -n node-agents -o wide -l app.kubernetes.io/name=node-log-agent
# -> three Pods, three different NODE values
```

Then add a fourth Node and watch a Pod appear without you doing anything:

```powershell
minikube node add --profile taskflow3
kubectl --context taskflow3 get pods -n node-agents -o wide -l app.kubernetes.io/name=node-log-agent -w
```

**No `replicas` field was involved.** Now delete a DaemonSet Pod and notice it *is*
recreated — because unlike the bare Pod in step 03, this one has a controller:

```powershell
kubectl delete pod -n node-agents -l app.kubernetes.io/name=node-log-agent
kubectl get pods -n node-agents -l app.kubernetes.io/name=node-log-agent -w
```

When you are done with the extra cluster:

```powershell
minikube delete --profile taskflow3
kubectl config use-context taskflow
```

---

## 4. Understand the tolerations

```powershell
kubectl describe node taskflow | Select-String -Pattern "Taints"
kubectl get daemonset node-log-agent -n node-agents -o jsonpath='{.spec.template.spec.tolerations}' | ConvertFrom-Json
```

Your only Node is the control plane, and control-plane Nodes carry a
`node-role.kubernetes.io/control-plane:NoSchedule` taint. Without the toleration in
the manifest, `DESIRED` would be **0** and you would have no agent at all — a very
common surprise on single-node clusters.

Prove it:

```powershell
kubectl patch daemonset node-log-agent -n node-agents --type json -p '[{"op":"remove","path":"/spec/template/spec/tolerations"}]'
kubectl get daemonset,pods -n node-agents      # DESIRED 0
kubectl apply -f labs/08-daemonset.yaml        # restore
```

Note the asymmetry, because it matters operationally: DaemonSet Pods **automatically**
tolerate the node-condition taints (`not-ready`, `unreachable`, `disk-pressure`,
`memory-pressure`) so an agent keeps running when its Node is unhealthy — which is
exactly when you need it. They do **not** automatically tolerate role taints.

---

## 5. Break it

### 5a. Node drain and DaemonSets

```powershell
kubectl drain taskflow --ignore-daemonsets --delete-emptydir-data
```

The `--ignore-daemonsets` flag is required, and now you know why: DaemonSet Pods are
not evictable, because *every Node is supposed to have one*. Draining them would be
logically inconsistent. (Also note the drain may hang on your Postgres/API Pods —
check `kubectl get pdb -n taskflow` once you have done step 10. On a single-node
cluster a PDB with `minAvailable` will block the drain entirely and the message tells
you so.)

```powershell
kubectl uncordon taskflow
kubectl get nodes
```

### 5b. Too many resources on every Node

```powershell
kubectl patch daemonset node-log-agent -n node-agents --type json `
  -p '[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"1","memory":"1Gi"},"limits":{"cpu":"2","memory":"2Gi"}}}]'
kubectl get pods -n node-agents -o wide -l app.kubernetes.io/name=node-log-agent
```

With `requests: 1 CPU` on a 4-CPU node that already hosts your whole application, the
Pod may become unschedulable on some Nodes, and `DESIRED`/`READY` diverge. On a
DaemonSet this is worse than on a Deployment: the cost is multiplied by the number of
Nodes, and a node agent competing with your application for CPU is a self-inflicted
outage. Restore:

```powershell
kubectl apply -f labs/08-daemonset.yaml
```

### 5c. Pod Security — the experiment that explains the namespace layout

This is the most valuable minute in the step. Look at the two namespaces the lab uses:

```powershell
kubectl get namespace taskflow   --show-labels
kubectl get namespace node-agents --show-labels
```

```
taskflow    pod-security.kubernetes.io/enforce=baseline  warn=restricted  audit=restricted
node-agents pod-security.kubernetes.io/enforce=privileged warn=restricted  audit=restricted
```

Now try to put the agent into the hardened application namespace. Copy the manifest,
change `namespace: node-agents` to `namespace: taskflow`, and apply it:

```powershell
(Get-Content labs/08-daemonset.yaml) -replace 'namespace: node-agents','namespace: taskflow' |
  Set-Content labs/.08-daemonset-in-taskflow.yaml
kubectl apply -f labs/.08-daemonset-in-taskflow.yaml
```

```
Error from server (Forbidden): error when creating "...": pods "node-log-agent-xxxxx"
is forbidden: violates PodSecurity "baseline:latest": hostPath volumes (volume "host-log")
```

**`hostPath` is forbidden at `baseline`, not only at `restricted`** — and because Pod
Security Admission is enforced per *namespace*, there is no per-Pod exemption. A node
agent needs host access by definition, so privileged infrastructure gets its own
namespace. That is why this DaemonSet lives in `node-agents`, and it is the standard
pattern rather than a workaround.

The alternative — weakening `taskflow` to `privileged` so the agent fits — would mean
any application Pod in that namespace could also mount the host filesystem. Split the
namespace instead.

```powershell
Remove-Item labs/.08-daemonset-in-taskflow.yaml
```

Now the other direction: tighten `taskflow` and see what your own application would
have to change to survive it.

```powershell
kubectl label namespace taskflow pod-security.kubernetes.io/enforce=restricted --overwrite
kubectl delete pod -n taskflow -l app.kubernetes.io/name=taskflow-api
kubectl get pods -n taskflow -l app.kubernetes.io/name=taskflow-api
kubectl get events -n taskflow --field-selector reason=FailedCreate | Select-Object -First 3
```

The API Pods are **rejected**, because `restricted` requires
`allowPrivilegeEscalation: false` and `capabilities.drop: ["ALL"]` — exactly the
warnings printed when you applied step 04's manifests. The fix is two lines in the
container's `securityContext`, and it is a good exercise: make the change, re-apply
04-deployment.yaml under `restricted`, and confirm the Pods come up.

Restore the namespace afterwards:

```powershell
kubectl label namespace taskflow pod-security.kubernetes.io/enforce=baseline --overwrite
kubectl apply -f labs/04-deployment.yaml
kubectl rollout status deployment/taskflow-api -n taskflow
```

---

## 6. Exercises

1. **When not to use a DaemonSet.** You want metrics from 3 specific Nodes, or one
   cluster-wide singleton. What do you use instead? (A Deployment with `replicas: 3` +
   `nodeSelector`, and a Deployment with `replicas: 1`.)
2. **Real agents.** Swap the image for `prometheus/node-exporter:v1.8.2` with
   `hostNetwork: true` and `hostPID: true`. What does it expose, and what did you have
   to give up (Pod Security level, port conflicts)? Then swap for
   `fluent/fluent-bit` and read its config for tailing `/var/log/containers/*.log`.
3. **`updateStrategy`.** Change `maxUnavailable` to `"50%"` and roll out a new image
   tag. Why is a conservative `maxUnavailable` more important here than for a
   Deployment? (Imagine it is the CNI plugin.)
4. **`priorityClassName`.** DaemonSets in `kube-system` usually carry
   `system-node-critical`, so the kubelet evicts application Pods before them. Explain
   why that ordering is right.
5. **DaemonSets you already have:**
   ```powershell
   kubectl get daemonset -A
   ```
   Identify each one and what it does. In a cloud cluster you would also see the CNI,
   the CSI node plugin and the cloud-controller node agent here.
6. **Logs on the Node.** Read `/host/var/log/pods` from the agent Pod and find the
   directory for one of your own application Pods. This is the mechanism every log
   collector uses — including the one you would deploy in production.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DESIRED 0` | `nodeSelector`/affinity matches no Node | `kubectl get nodes --show-labels` |
| `DESIRED 1` but `CURRENT 0`, no Pods at all | the controller tried and was **rejected** — the DaemonSet itself shows no error | `kubectl get events -n node-agents --field-selector reason=FailedCreate` |
| `DESIRED 1`, `READY 0`, Pod `Pending` | no Node tolerates it / insufficient resources | check taints and `kubectl describe pod` |
| Pod `MISSCHEDULED` | the DaemonSet controller could not place it | usually a taint or resource issue |
| Pod `CrashLoopBackOff` | the agent's command exits | `kubectl logs`, check the agent's config |
| Drain refuses to continue | DaemonSet Pods present | add `--ignore-daemonsets` |
| `hostPath` reads are empty | wrong path, or the Node genuinely has no such directory | `type: Directory` makes it fail loudly instead of silently |
| Pod rejected: `hostPath volumes` | `baseline` (and `restricted`) forbid `hostPath` | run agents in their own namespace with `enforce: privileged` |

---

## 8. Clean up

Optional — keep the DaemonSet if you want it running for the rest of the lab. It is in
`node-agents`, so the `taskflow` NetworkPolicies from step 09 do not touch it (and it
does not need the network at all: its only outbound "traffic" is reading the Node's
filesystem).

To remove it:

```powershell
kubectl delete -f labs/08-daemonset.yaml
```

The empty `node-agents` namespace can stay — it costs nothing and you will want it if
you redo the lab.

---

**Checkpoint:** you can explain why a DaemonSet has no `replicas` field, why node
agents need `hostPath` and tolerations, why `--ignore-daemonsets` exists on
`kubectl drain`, and **why a hostPath workload cannot live in a `baseline`
namespace**.

**Next:** [Lab 09 — Egress & NetworkPolicy](09-egress-and-network-policies.md) →
