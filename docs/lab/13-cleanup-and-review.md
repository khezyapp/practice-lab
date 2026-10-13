# Lab 13 — Cleanup, review, and what to learn next

**Goal:** verify the whole system against the acceptance test, consolidate what you
built into a mental model you can explain out loud, tear it down cleanly, and know what
to study next.

**Time:** ~45 minutes.

---

## 1. The acceptance test

Run this against your cluster. Every line must pass. This is the definition of done from
[lab 00 §6](00-scenario-and-requirements.md#6-definition-of-done), and being able to run
it from memory is the actual outcome of the lab.

```powershell
# --- the inventory ---------------------------------------------------------
kubectl get deploy -n taskflow          # taskflow-api 3/3, taskflow-worker 2/2, redis 1/1
kubectl get statefulset -n taskflow     # postgres 1/1
kubectl get daemonset -n node-agents    # node-log-agent, DESIRED == node count
kubectl get pvc -n taskflow             # data-postgres-0 Bound
kubectl get svc -n taskflow             # taskflow-api, redis, postgres (headless)
kubectl get ingress -n taskflow         # ADDRESS present, both hosts
kubectl get hpa -n taskflow             # TARGETS is a number, not <unknown>
kubectl get pdb -n taskflow             # ALLOWED DISRUPTIONS >= 1
kubectl get networkpolicies -n taskflow # the full posture
kubectl get jobs -n taskflow            # taskflow-migrate Complete

# --- north-south traffic ---------------------------------------------------
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/tasks
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/whoami
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/config

# --- writes, the queue, and egress ----------------------------------------
curl.exe -s -X POST -H "Host: taskflow.local" -H "Content-Type: application/json" `
  -d '{\"title\":\"verify the lab\"}' http://localhost:8080/api/tasks
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/queue
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/egress

# --- self-healing ---------------------------------------------------------
kubectl delete pod -n taskflow -l app.kubernetes.io/name=taskflow-api
kubectl get pods -n taskflow -w        # three Pods come back
```

### The three destructive proofs

These are the ones worth doing even now, because they are what makes the abstractions
real:

```powershell
# 1. data survives a Pod deletion
kubectl delete pod postgres-0 -n taskflow
kubectl run psql --rm -it --image=postgres:16-alpine -n taskflow -- `
  psql -h postgres-0.postgres -U taskflow -d taskflow -c "SELECT count(*) FROM tasks;"

# 2. a deploy does not drop traffic
kubectl set image deploy/taskflow-api -n taskflow api=taskflow-api:1.0.0
kubectl rollout status deploy/taskflow-api -n taskflow

# 3. a bad deploy does not take you down
kubectl set image deploy/taskflow-api -n taskflow api=taskflow-api:9.9.9
kubectl get deploy taskflow-api -n taskflow     # still 3/3 AVAILABLE
kubectl rollout undo deploy/taskflow-api -n taskflow
```

---

## 2. The inventory: what you built, and why each piece exists

Fill this in from memory first, then check yourself against the table.

| Object | Name | Why it exists | What breaks without it |
|---|---|---|---|
| Namespace | `taskflow` | scope for names, policies, quotas | everything lands in `default`; no RBAC or quota boundary |
| Namespace | `node-agents` | a home for privileged infrastructure, because `baseline` forbids `hostPath` | the node agent cannot run at all |
| ServiceAccount | `taskflow-api` | identity with the API token switched off | Pods silently get the broad `default` SA |
| ResourceQuota | `taskflow-quota` | hard ceiling on namespace consumption | one runaway workload takes the node pool |
| LimitRange | `taskflow-defaults` | default/min/max resources per container | Pods with no requests are evicted first |
| ConfigMap | `taskflow-config` | environment config out of the image | a rebuild per environment; credentials in images |
| Secret | `taskflow-secret` | credentials, separate RBAC and handling | passwords in ConfigMaps or in the image |
| Deployment | `taskflow-api` | stateless API: healing, scaling, rollouts | a bare Pod with no supervisor |
| Deployment | `taskflow-worker` | long-running queue consumer | jobs never processed |
| Deployment | `redis` | cache/queue | API and worker lose the queue |
| StatefulSet | `postgres` | identity + its own disk | data loss, or two writers on one volume |
| PVC / PV | `data-postgres-0` | durable storage | every restart is a factory reset |
| Service (ClusterIP) | `taskflow-api` | stable address for ephemeral Pods | nothing can find the API |
| Service (ClusterIP) | `redis` | internal address for the queue | worker cannot resolve Redis |
| Service (headless) | `postgres` | per-Pod DNS for one replica | cannot address `postgres-0` specifically |
| Ingress | `taskflow` | one front door, host + path routing, TLS later | one NodePort/LoadBalancer per service |
| DaemonSet | `node-log-agent` | one agent per Node, including future Nodes (in `node-agents`, because it needs `hostPath`) | no node-level visibility |
| Job | `taskflow-migrate` | work that must run once and finish | slow, racing migrations inside app startup |
| CronJob | `taskflow-cleanup` | scheduled retention work | unbounded data growth |
| HPA | `taskflow-api` | capacity follows demand | manual scaling, or fixed over-provisioning |
| PDB | `taskflow-api` | survivable node drains and upgrades | upgrades take all replicas down |
| NetworkPolicy | 9 of them | documented, enforced egress/ingress posture | every Pod can reach everything |

---

## 3. Explain it out loud

The real test of understanding is explanation, not recognition. Do these five, aloud, in
under three minutes each. If you stumble, you have found your next revision topic.

1. **Trace a request.** Browser → `taskflow.local` → DNS → the ingress controller pod →
   the `taskflow-api` Service → iptables DNAT → a Ready Pod → the container → Postgres →
   back. Name what breaks at each hop and what error each break produces.
2. **Trace `kubectl apply -f 04-deployment.yaml`.** Authentication → RBAC → mutating
   admission (defaults, LimitRange) → validation → etcd → the Deployment controller →
   a ReplicaSet → Pods → the scheduler → the kubelet → image pull → CNI → container
   start → probes → status. Which failures are visible only in Pod Events?
3. **Explain why the API is a Deployment and Postgres is a StatefulSet.** Identity, DNS,
   storage, ordering, and the fact that a StatefulSet gives you none of replication,
   failover or backup.
4. **Explain the difference between a container, a Pod, a ReplicaSet and a Deployment.**
   Include who restarts what when each one dies.
5. **Explain your network posture in one paragraph.** What can enter the namespace, what
   can leave it, why DNS needs its own rule, why the kubelet needed an `ipBlock`, and why
   the cleanup job needed two policies instead of one.

---

## 4. Self-assessment

Score yourself honestly. Anything below 5 is your revision list.

```
[ ] 1. A Pod is Pending. I can name the four possible causes and the one command.
[ ] 2. A Pod is CrashLoopBackOff. I know why `logs --previous` matters.
[ ] 3. A Pod is Running 0/1. I know which probe is involved and where the symptom appears.
[ ] 4. A Service does not work. I check Endpoints first, and I know why.
[ ] 5. A rollout is stuck. I can tell an ImagePullBackOff stall from a probe stall.
[ ] 6. I can explain requests vs limits, and CPU throttling vs OOMKilled.
[ ] 7. I can explain why liveness must never check a dependency.
[ ] 8. I can explain why a PDB can block a node drain, and why that is correct.
[ ] 9. I can explain why deleting a StatefulSet keeps the data but deleting the PVC does not.
[ ] 10. I can explain why `port-forward` proving nothing about a Service matters.
[ ] 11. I can write a NetworkPolicy that allows exactly one external destination.
[ ] 12. I can explain what the outside world sees as my egress source IP, and why.
[ ] 13. I can explain why a ConfigMap change does not restart my Pods.
[ ] 14. I can explain why a Job must be idempotent.
[ ] 15. I can trace a request and a `kubectl apply` from memory.
```

---

## 5. Teardown

### Option A — keep everything (recommended)

Just stop the cluster; your data and objects survive:

```powershell
minikube stop --profile taskflow
```

Return later with `minikube start --profile taskflow` and
`kubectl config use-context taskflow`.

### Option B — remove the lab, keep the cluster

```powershell
# remove the broken-workload experiments if you left them behind
kubectl delete -f labs/11-broken-workloads.yaml --ignore-not-found

# remove the whole application
kubectl delete namespace taskflow

# and the node agents, which live in their own namespace (step 08)
kubectl delete namespace node-agents
```

> **Before you delete the namespace**, decide whether you care about the data. Deleting
> the namespace garbage-collects every namespaced object in it — including
> `data-postgres-0`. That is the destructive action from step 07's "break it" section,
> performed on purpose this time. This is the same `kubectl delete` that ends careers
> when the context is `prod` and nobody checked. Practise it here so you respect it there.

Check for leftovers, and understand why they exist:

```powershell
kubectl get pv
kubectl get pvc -A
```

On this lab's local cluster the default StorageClass uses `reclaimPolicy: Delete`, so the
volume goes away with the claim. In cloud clusters you very often find volumes left in
`Released`/`Failed` instead: the claim was deleted, but the policy was `Retain`, so the disk
survived. That is `Retain` doing its job — and it is also how storage bills grow quietly.
Delete the ones you do not want:

```powershell
kubectl get pv -o name | Select-String taskflow
# then, for each one you are sure about:
# kubectl delete pv <name>
```

### Option C — remove everything

```powershell
minikube delete --profile taskflow
```

If you made the optional 3-node cluster in step 08:

```powershell
minikube delete --profile taskflow3
minikube profile list
```

And remember to remove the hosts-file entries you added, as Administrator:

```
192.168.49.2  taskflow.local api.taskflow.local
```

---

## 6. Rebuild it from scratch (the best revision)

The single most effective thing you can do after finishing is **build the whole thing
again without the docs**. Not the concepts — the cluster.

```powershell
minikube start --profile taskflow --cpus=4 --memory=5120 --driver=docker
minikube addons enable ingress --profile taskflow
minikube addons enable metrics-server --profile taskflow
kubectl config use-context taskflow

kubectl create namespace taskflow
# ...then write the YAML yourself, from the concepts, in dependency order
```

Keep a list of everything you had to look up. That list is your actual study plan — far
better than any generic roadmap, because it is derived from your own gaps.

An intermediate version: apply the manifests from `labs/` in the right order, from
memory, and check with `kubectl get all -n taskflow` after each one.

---

## 7. What to learn next

You now have the core. These are the natural next steps, roughly in order of value for
a production role.

| Topic | Why it is next | Where to start |
|---|---|---|
| **Observability** | You were debugging blind: logs and `describe` only. Real clusters need metrics, dashboards and alerts on the *right* signals (RED/USE), not just up/down | kube-prometheus-stack (Prometheus + Grafana + Alertmanager), Loki for logs. Alert on error rate and saturation, not CPU |
| **Helm / Kustomize** | You wrote 12 YAML files by hand for one app in one environment. Multiply by 4 environments | Kustomize overlays first (no templating language); Helm for third-party charts |
| **GitOps** | The cluster should be reconciled from Git, not from your terminal. You already have the declarative mindset | Argo CD or Flux: point at a repo, and drift becomes visible |
| **Gateway API** | It is the successor to Ingress: `GatewayClass`/`Gateway`/`HTTPRoute`, traffic splitting, header matching, TCP/UDP support, and proper role separation | `HTTPRoute` is nearly a drop-in for the Ingress concepts you just used |
| **Operators** | You learned that a StatefulSet does not give you failover or backups. An operator encodes that operational knowledge | CloudNativePG for Postgres; read how a `Cluster` CR reconciles into StatefulSets, Services and backups |
| **Security** | Pod Security Admission, RBAC least privilege, encryption at rest, image signing, runtime policy | Kyverno or OPA Gatekeeper; `kubectl auth can-i --list --as=` |
| **Service mesh** | mTLS, L7 authorization, retries, and real traffic shifting — the things a NetworkPolicy cannot do | Linkerd (simpler) before Istio (more capable) |
| **Cluster operations** | Upgrades, etcd backup and **restore**, node pools, capacity planning | `etcdctl snapshot save`, and actually test a restore |
| **Node autoscaling** | You saw HPA produce `Pending` Pods. Cluster Autoscaler / Karpenter is the other half | Karpenter on a cloud cluster, or `minikube node add` to feel it locally |
| **Certification** | If you want the credential, you are most of the way there | CKA is the practical one that matches this lab |

### Three things to do this month

1. **Put a real application in a real cluster.** Your own side project, one namespace,
   with tests that assert the probes and resources are set.
2. **Write down your incident runbook.** You have the structure from step 11: symptom →
   command → cause. Add to it every time something surprises you.
3. **Break things on purpose, weekly.** The lab has six broken workloads; write ten more.
   The skill you built here is diagnosis, and it only compounds.

---

## 8. Where each thing lives

```
docs/
  README.md                     index, learning path, progress checklist
  getting-started/              local cluster, kubectl, the mental model
  concepts/                     13 lessons, one per Kubernetes object
  architecture/                 request lifecycle, what happens on apply
  lab/                          the 13 steps you just finished
  reference/                    kubectl cheatsheet, glossary, interview questions
labs/
  README.md                     apply order, teardown
  app/                          the TaskFlow source and Dockerfile
  0X-*.yaml                     one manifest per lab step
sample.yaml                     your first Pod - still valid, still a bad idea in production
```

---

**Well done.** You have built, broken, and rebuilt a production-shaped application on
Kubernetes — and more importantly, you have a repeatable method for finding out why it
is not working.

**Next:** [Interview questions](../reference/interview-questions.md) — test yourself on
the whole picture.
