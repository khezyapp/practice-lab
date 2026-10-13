# Reference — Interview questions with answers

Answers are written the way you should say them out loud: **short, with the "why"**.
Interviewers are not checking whether you memorised the YAML; they are checking whether
you understand the trade-off and whether you have ever debugged the thing.

Each answer points at the lab step where you actually did it — use that as your evidence
when you are asked "have you used this?".

> **How to answer Kubernetes questions**
> 1. Name the layer/object precisely.
> 2. State the mechanism (what actually happens).
> 3. Give the trade-off (why not the alternative).
> 4. If relevant, give a failure you have debugged.
> Never answer "because that's best practice" — say *what it prevents*.

---

## 1. Fundamentals and architecture

**1. What is Kubernetes, in one sentence?**
A control loop that keeps a fleet of machines running the workload you declared,
continuously reconciling actual state toward your desired state. It is not mainly about
running containers — Docker did that — it is about scheduling, self-healing, scaling and
declarative rollouts across many machines.

**2. What are the control plane components and what does each do?**
`kube-apiserver` (the only writer, does authn/authz/admission/validation), `etcd` (the
only state store), `kube-scheduler` (picks a Node for unscheduled Pods), `kube-controller-manager`
(runs the built-in controllers), and `cloud-controller-manager` (cloud load balancers,
routes, node lifecycle). The control plane does **not** run your containers.

**3. What runs on a worker node?**
The `kubelet` (starts containers, mounts volumes, runs probes, reports status), the
container runtime (containerd/CRI-O via CRI), `kube-proxy` (programs Service VIPs), and
the CNI plugin.

**4. The control plane goes down. Does your application stop serving?**
No. Pods keep running: the kubelet keeps supervising containers and kube-proxy's rules
keep routing. What stops is *change* — no new Pods, no rescheduling, no rollouts. This is
the most common question that separates people who have read about Kubernetes from people
who understand it.

**5. What is the difference between `spec` and `status`?**
`spec` is your declaration; `status` is the observed reality, written by controllers and
the kubelet. Controllers loop until they match.

**6. What is the scheduler's decision process?**
Filter (which Nodes *can* take this Pod: requests, affinity, taints/tolerations, volume
topology) then score (spread, preferences, image locality) then bind. A Pod with no Node
stays `Pending` — which means "unscheduled", not "broken".

**7. What happens when you run `kubectl apply`?**
Discovery → authentication → RBAC authorization → mutating admission and defaulting →
schema validation → validating admission (webhooks, PSA, ResourceQuota) → persist to etcd
→ controllers react → scheduler binds → kubelet pulls the image, sets up CNI, mounts
volumes, starts containers, runs probes, reports status. The API server never starts a
process; it only records intent.

**8. Difference between a CRD and an operator?**
A CRD adds a new *kind*. An operator is that CRD **plus a controller** that reconciles it
with real operational knowledge (backups, failover, upgrades). CRD = data model;
operator = behaviour.

**9. What is a namespace, and what does it *not* give you?**
A naming and policy scope, plus a DNS segment. It does **not** isolate network traffic,
CPU or memory by default, and it is not a security boundary. Isolation requires
NetworkPolicy + RBAC + ResourceQuota + Pod Security.

---

## 2. Pods

**10. Why is the Pod the unit of scheduling instead of the container?**
So that tightly-coupled containers (sidecars, init containers) can share a network
namespace, an IP, a port space and volumes, and be scheduled and lifecycle-managed
atomically. It also gives a home to infrastructure containers that support the main
process without being merged into its image.

**11. Two containers in one Pod — can both bind port 8080?**
No. They share the network namespace, so there is one port space. This is also why they
reach each other on `localhost`.

**12. What are init containers for?**
Setup that must complete *before* the app starts: wait for a dependency, clone a repo,
prepare files. They run in order, to completion, and their failure blocks the Pod.

**13. Name the three multi-container patterns.**
Sidecar (enhances the main container, same lifecycle), ambassador (proxies outbound
connections), adapter (normalises output).

**14. A Pod is deleted. Who recreates it?**
Only a controller — a Deployment/ReplicaSet, StatefulSet, DaemonSet or Job. A bare Pod is
gone forever. Conversely, if a *container* inside a Pod dies, the **kubelet** restarts it
per `restartPolicy`. Two different mechanisms; keep them straight.

**15. What does `CrashLoopBackOff` mean?**
The container keeps exiting and the kubelet is retrying with exponential backoff (10s →
5min cap). It is not a diagnosis; the diagnosis is in `kubectl logs --previous`.

**16. What happens between `kubectl delete pod` and the container stopping?**
The Pod is marked `Terminating` and removed from Service endpoints **first**, then
`preStop` runs, then `SIGTERM`, then the app drains, then `SIGKILL` after
`terminationGracePeriodSeconds`. Endpoints-before-signal is what makes zero-downtime
possible.

**17. Why does a Pod's IP change?**
IPs are allocated by the CNI at Pod creation and Pods are immutable — so any replacement
is a new Pod with a new IP. That is the entire reason Services exist.

---

## 3. Deployments and rollouts

**18. How does a Deployment relate to a ReplicaSet and a Pod?**
Deployment → owns ReplicaSets → own Pods. A new ReplicaSet is created for every change to
the Pod template, which is exactly how rollback works: `rollout undo` scales the previous
ReplicaSet back up.

**19. You changed a ConfigMap the Deployment mounts. Do the Pods restart?**
No. Only Pod-template changes create a new ReplicaSet. ConfigMap changes need a
`kubectl rollout restart`, or an immutable name-versioned ConfigMap, or a checksum
annotation on the template.

**20. `maxSurge: 1, maxUnavailable: 0` with 3 replicas — peak Pod count and behaviour?**
4 briefly. One new Pod is added and must become **Ready** before an old one is removed, so
capacity never drops. It requires spare room on the Nodes.

**21. Does `maxUnavailable: 0` guarantee zero downtime?**
No. Without a correct readiness probe, a Pod counts as ready the moment its container
starts, so traffic is routed to an app that is still booting. The strategy and the probe
are one mechanism; either alone is not enough.

**22. How do you roll back?**
`kubectl rollout undo` (or `--to-revision=N`). It re-uses the old ReplicaSet. Bounded by
`revisionHistoryLimit`.

**23. When would you use `strategy: Recreate`?**
When two versions genuinely cannot coexist — a singleton holding an exclusive lock, or an
incompatible schema change. You accept downtime.

**24. A rollout is stuck. Walk me through it.**
`kubectl rollout status` names the reason; then `kubectl get rs` (new RS scaled up?),
`kubectl describe deploy` (events), `kubectl get pods` (image pull? Pending? not Ready?),
`kubectl logs <new-pod>`. Typical causes: `ImagePullBackOff`, a readiness probe that never
passes, insufficient resources, a bad ConfigMap key, `progressDeadlineSeconds` exceeded.

**25. Why `--cascade=orphan`?**
To delete a Deployment but keep its Pods, which is a way to *see* `ownerReferences` and
garbage collection in action. Deliberately orphaning workloads is rare in production.

---

## 4. Services and networking

**26. Why does a Service exist?**
Pod IPs are ephemeral. A Service gives a stable DNS name and virtual IP in front of
whatever Pods currently match a selector, load-balancing across the ones that are Ready.

**27. Where does Service load balancing happen?**
In each client Node's kernel — iptables/IPVS rules programmed by kube-proxy, or an eBPF
datapath. There is no central proxy, no single bottleneck, no single point of failure.

**28. What are Endpoints/EndpointSlices?**
The live list of Ready Pod IPs behind a Service, maintained by a controller from
*selector ∩ readiness*. **Empty endpoints is the answer to almost every "the Service is
broken" question.**

**29. `port` vs `targetPort`?**
`port` is what clients connect to; `targetPort` is the container's listening port (best
given as a named port). Confusing them gives "endpoints look fine but connections are
refused".

**30. Name the Service types.**
ClusterIP (internal only), NodePort (a port on every Node), LoadBalancer (NodePort + cloud
LB), ExternalName (DNS alias, no proxy). Plus headless (`clusterIP: None`), which is a
different thing entirely.

**31. What is a headless Service for?**
No VIP, no load balancing — DNS returns one A record per Pod. That gives each replica a
stable, individually addressable name, which is what a StatefulSet needs.

**32. What does `port-forward` prove?**
That *one* Pod is reachable. It does not exercise the Service, its endpoints or its load
balancing. Testing "the Service works" requires calling it from inside the cluster.

**33. How does DNS work inside a cluster?**
CoreDNS resolves `<svc>.<ns>.svc.cluster.local`. `resolv.conf` supplies search domains, so
`postgres` resolves inside its own namespace — which is why you should use FQDNs in config
so it does not silently depend on where the Pod runs.

**34. Why did you lose the client's source IP?**
With `externalTrafficPolicy: Cluster`, incoming traffic is SNAT'd to the receiving Node's
IP. Fixes: `externalTrafficPolicy: Local`, or trust `X-Forwarded-For` from the ingress
controller (and make sure your app only trusts the proxy).

---

## 5. Ingress

**35. What is the difference between an Ingress and an Ingress controller?**
The Ingress is a routing *rule* (a declaration). The controller is the running proxy that
reads those rules and forwards traffic. No controller → the Ingress object does absolutely
nothing, silently.

**36. Why an Ingress instead of a LoadBalancer per Service?**
Cost, one IP and one certificate instead of N, host/path-based L7 routing, and a single
place for TLS, auth, rate limiting and redirects. One front door instead of many.

**37. `pathType` values?**
`Exact`, `Prefix` (element-boundary aware: `/api` matches `/api/x` but not `/apifoo`), and
`ImplementationSpecific` — which is what you need for nginx regex paths.

**38. Traffic is 404. Why?**
No rule matched: wrong `Host` header, wrong path, or a `pathType`/regex mismatch. The
proxy's own 404 — your app never saw the request.

**39. Traffic is 503. Why?**
The rule matched but the backend Service has **no Ready endpoints**. Fix it in the Pods
(readiness probes), not in the Ingress.

**40. 502 vs 504?**
502 = reached the backend, the connection failed (wrong `targetPort`, crashed Pod). 504 =
reached the backend, it timed out (slow app, or the proxy timeout is too low).

**41. How would you expose a raw TCP service?**
An Ingress cannot — it is HTTP(S) only. Use a `LoadBalancer`/`NodePort` Service, the
ingress controller's TCP passthrough configmap, or Gateway API with `TCPRoute`.

**42. How does TLS work with an Ingress?**
`spec.tls[].secretName` references a `kubernetes.io/tls` Secret containing `tls.crt` and
`tls.key`; the controller terminates TLS. In practice cert-manager creates and rotates that
Secret from an annotation rather than a human.

**43. Ingress vs Gateway API?**
Gateway API is the successor: `GatewayClass`/`Gateway`/`HTTPRoute`, richer matching
(headers, methods, weights), TCP/UDP support, and role separation between platform and
app teams. The concepts map almost one-to-one.

---

## 6. Egress and NetworkPolicy

**44. Are Pods restricted from making outbound connections by default?**
No. With no policy, **all egress is allowed**. Most clusters have no egress control at all
until someone asks the question in a security review.

**45. What does applying a NetworkPolicy do to a Pod?**
If it selects the Pod for a direction, that direction becomes **deny-by-default** and only
explicitly allowed traffic passes. There are no deny rules — policies are additive allow
sets (the union of everything that selects the Pod).

**46. You apply default-deny and everything breaks. What did you forget?**
DNS. Port 53 (UDP and TCP) to kube-dns must be allowed, or name resolution fails and
everything that uses a Service name fails with confusing "no such host" errors.

**47. Your Pods go to `0/1` with "probe failed" right after default-deny. Why?**
`httpGet`/`tcpSocket` probes come from the **kubelet on the Node**, not from a Pod — so no
`podSelector` can allow them; you need an `ipBlock` for the node CIDR. `exec` probes are
unaffected because they go through the kubelet's CRI stream.

**48. Can you allow traffic to `api.github.com`?**
Not with core NetworkPolicy — it is L3/L4 and speaks CIDRs, not hostnames. Options: Cilium's
`toFQDNs`, an HTTP forward proxy, or a mesh `ServiceEntry`.

**49. Your Pod calls an external API. What source IP does the vendor see?**
Not the Pod IP (`10.244.x.x` is private). The packet is SNAT'd to the Node IP, or to a NAT
gateway's static IP in a cloud cluster. So "which IP should we allowlist?" is a platform
question, and if you need a *dedicated stable* egress IP you need an egress gateway.

**50. A connection has no error, it just hangs. What does that tell you?**
A **timeout with no refusal** is the signature of a dropped packet — a NetworkPolicy. A
*refusal* means you reached something and nothing was listening. Different layers entirely,
and this distinction saves a lot of time.

**51. NetworkPolicies are configured, but nothing is blocked. What now?**
Your CNI does not enforce them. Plain flannel accepts the objects and ignores them.
Verify enforcement empirically: two Pods, confirm they can talk, apply default-deny,
confirm they cannot.

**52. A new Job cannot reach the database, but the API can. Why?**
NetworkPolicies are scoped to pod *selectors*. A new workload needs its own egress rule
**and** the destination needs an ingress rule naming it. Allowing only one side fails
identically to allowing neither.

---

## 7. Configuration

**53. ConfigMap vs Secret?**
Intent, encoding and RBAC. A ConfigMap is plain text for non-sensitive config; a Secret is
base64 for credentials and gets separate RBAC and (optionally) encryption at rest. **Base64
is encoding, not encryption** — anyone who can `get secrets` can read every credential in
the namespace.

**54. How do you consume them, and how do the two differ on updates?**
`env`/`envFrom` (fixed at container start — change requires a restart) versus a volume
mount (updated in place within about a minute, provided the app re-reads the file). A
`subPath` mount never updates.

**55. How do you force a restart when config changes?**
`kubectl rollout restart`, or an annotation containing a checksum of the ConfigMap in the
Pod template (so a template change triggers a rollout automatically). The second is what
you want in GitOps.

**56. How do you handle secrets properly in production?**
Never in Git. External Secrets Operator or the Secrets Store CSI driver from Vault/ASM/GCP,
or Sealed Secrets/SOPS if it must live in Git. Plus encryption at rest on the API server so
etcd backups are not plaintext, minimal RBAC on `secrets`, and rotation you have actually
tested.

**57. Why avoid secrets in environment variables?**
They leak more easily: visible to every process in the container, in `/proc/1/environ`, in
crash dumps, inherited by children. Prefer mounted files with a restrictive `defaultMode`.

---

## 8. Storage

**58. What happens to data in a container's filesystem when it restarts?**
It is gone. The writable layer dies with the container. Only volumes persist.

**59. Explain PV, PVC, StorageClass.**
A PVC is a namespaced *request* ("2Gi, ReadWriteOnce"). A PV is the actual disk. A
StorageClass names a provisioner and its parameters, enabling dynamic provisioning so the
PV appears on demand. The indirection is the point: the same PVC binds to different
hardware on your laptop and in production.

**60. What does `ReadWriteOnce` actually restrict?**
One **Node**, not one Pod. Two Pods on the same Node can usually share it; Pods on
different Nodes cannot. `RWX` needs genuinely shared storage (NFS/EFS/CephFS), not a bigger
block volume.

**61. A PVC is stuck Pending. Why?**
No StorageClass or no default class; the provisioner is unhealthy; or
`WaitForFirstConsumer` is simply waiting for a Pod to consume it. `kubectl describe pvc`
Events names it.

**62. Pod stuck `ContainerCreating` with "volume node affinity conflict"?**
The PV was provisioned in one zone and the Pod was scheduled in another. Fix:
`volumeBindingMode: WaitForFirstConsumer` on the StorageClass, which is why most cloud
default classes set it.

**63. You delete a StatefulSet. What happens to the data?**
The PVCs are retained — deliberate protection. Delete the PVC and you delete the data,
subject to the reclaim policy (`Delete` is the common default; use `Retain` for databases).

**64. Is a PVC a backup?**
No. It is a disk. You need `pg_dump`/Velero and, critically, a **restore you have tested**.

---

## 9. StatefulSet

**65. What does a StatefulSet give you that a Deployment cannot?**
Stable ordinal identity (`postgres-0` survives rescheduling), per-Pod DNS via a headless
Service, a PVC per replica bound to the ordinal, and ordered create/update/delete.

**66. Does a StatefulSet make Postgres highly available?**
No. It gives identity and storage. Replication, failover, backups and split-brain
prevention are the database's — or an operator's — job. `replicas: 3` with no replication
config is three unrelated databases.

**67. Why is `serviceName` mandatory?**
It names the headless Service that governs per-Pod DNS. Without it there is no
`postgres-0.postgres...` record, so nothing can address a specific replica.

**68. How do you canary a StatefulSet?**
`updateStrategy.rollingUpdate.partition` — only Pods with an ordinal ≥ partition are
updated, and updates proceed in reverse ordinal order.

**69. Two "gotchas" when running Postgres in a StatefulSet.**
`PGDATA` must be a **subdirectory** (some volume plugins leave a `lost+found`, and initdb
refuses a non-empty directory), and the Pod needs `fsGroup` set to the postgres group or
the volume is not writable.

**70. Why would you not run your own database in Kubernetes?**
At 3am you own replication, failover, PITR, upgrades and split-brain. A managed database or
a proper operator (CloudNativePG, Zalando) is usually the right call; a hand-rolled
StatefulSet is a reasonable dev/CI choice and a serious production decision.

---

## 10. DaemonSet

**71. What is a DaemonSet for?**
One Pod per Node, automatically including Nodes added later: log shippers, node exporters,
CNI agents, CSI node plugins, security agents.

**72. Why does it have no `replicas` field?**
The Node count *is* the replica count. `DESIRED` in `kubectl get ds` is literally "how many
eligible Nodes exist" — if the `nodeSelector` matches nothing, `DESIRED` is 0 and your
agents are silently absent.

**73. Why does a DaemonSet need tolerations for control-plane Nodes?**
DaemonSet Pods automatically tolerate node-*condition* taints (not-ready, unreachable,
disk-pressure) so an agent keeps running when its Node is unhealthy. They do **not**
tolerate role taints, so on a single-node cluster you must add the control-plane toleration
or get nothing at all.

**74. Why `--ignore-daemonsets` on `kubectl drain`?**
Because DaemonSet Pods are not evictable — every Node is supposed to have one, so evicting
them is logically inconsistent with the controller's contract.

**75. What is dangerous about `hostPath` and `privileged: true`?**
`hostPath` (read-write) lets a container alter the Node; `privileged: true` is effectively
root on the Node. Agents legitimately need read-only host access, which is why the standard
practice is to run them in a **dedicated namespace with `enforce: privileged`** and keep
application namespaces at `baseline`/`restricted`. The namespace split is required, not
cosmetic: `hostPath` is forbidden at `baseline` too, and Pod Security Admission has no
per-Pod exemption.

**76. Why is a conservative `maxUnavailable` more important for a DaemonSet?**
Because if it is your CNI or your log agent, "all at once" means the whole cluster — including
the control plane's ability to talk to Nodes.

---

## 11. Jobs and CronJobs

**77. Why is a Deployment wrong for a migration?**
A Deployment is designed so its Pods never exit; an exited container is restarted in a
loop. A Job is designed to run to completion, with `backoffLimit` retries, `completions`
tracking and `activeDeadlineSeconds`.

**78. What `restartPolicy` values are valid for a Job?**
`Never` or `OnFailure`. `Always` is rejected.

**79. `completions: 10, parallelism: 3`?**
At most 3 Pods run at a time until 10 successful completions.

**80. What is `completionMode: Indexed` for?**
Giving each Pod a stable index (`JOB_COMPLETION_INDEX`) so work can be sharded — each Pod
handles a known slice.

**81. Which CronJob fields prevent which incidents?**
`concurrencyPolicy: Forbid` (a slow run overlapping itself), `startingDeadlineSeconds` (a
catch-up stampede after a controller outage), history limits (thousands of retained Jobs
that eventually stop the controller creating more), `timeZone` (a "2am" that is not your
2am), `activeDeadlineSeconds` (a hung Job blocking every future run forever).

**82. How do you trigger a CronJob without waiting?**
`kubectl create job --from=cronjob/<name> <job-name>`.

**83. Why must a scheduled job be idempotent?**
Because it will run more than once — retries, restarts, manual re-runs, overlapping
schedules. Assume at-least-once and design for it (`ON CONFLICT`, upserts, versioned
migrations).

**84. How do you deploy a schema change with rolling updates?**
Expand/contract: release N adds the new column (nullable, backwards-compatible), release
N+1 uses it, release N+2 removes the old one. Old and new code run simultaneously during a
rolling update, so the schema must serve both. Run the migration as a Job *before* the new
Deployment, not inside app startup.

---

## 12. Probes, resources and scaling

**85. requests vs limits?**
Requests are what the scheduler reserves (and the denominator for HPA utilization); limits
are the cgroup cap. CPU over the limit throttles you; memory over the limit gets you
`OOMKilled` (exit 137). That asymmetry is why many teams set CPU requests without CPU
limits, and always set memory limits.

**86. readiness vs liveness?**
Readiness controls **traffic** (removed from Service endpoints, no restart). Liveness
controls **restarts**. Confusing them causes outages in both directions.

**87. Why must a liveness probe never check a dependency?**
Because a dependency blip then restarts every replica simultaneously — a restart storm
that hammers an already-struggling dependency. That is a cascading failure, and it is the
classic self-inflicted outage. Liveness checks the process; readiness may check
dependencies (with the trade-off that a dependency blip then removes all your capacity).

**88. What is `startupProbe` for?**
Slow-booting applications. Until it passes, liveness and readiness are disabled — so you
can keep liveness tight without restarting a container that is still initialising.

**89. What are QoS classes and why do they matter?**
`Guaranteed` (requests == limits), `Burstable`, `BestEffort` (nothing set). Under node
memory pressure the kubelet evicts `BestEffort` first and `Guaranteed` last. Set requests
on everything; make databases `Guaranteed`.

**90. HPA shows `TARGETS: <unknown>`. Why?**
metrics-server is unavailable, or the target has no CPU **requests** — utilization is a
percentage *of the request*, so there is no denominator.

**91. HPA scaled to `maxReplicas` and Pods are Pending. What is missing?**
Node capacity: a **cluster autoscaler** (Cluster Autoscaler, Karpenter). HPA adds Pods; the
autoscaler adds Nodes. Two components, frequently confused.

**92. Why is CPU often the wrong scaling signal?**
Because it lags and it is a poor proxy for the real constraint. For a queue consumer, scale
on **queue depth** (KEDA) — a metric the HPA cannot see, which is why you need KEDA or the
Prometheus adapter. Scaling *to zero* is only safe if the work is durably queued.

**93. What does a PodDisruptionBudget protect, and not protect?**
It caps **voluntary** disruption (node drains, upgrades, `kubectl evict`). It does nothing
about crashes, OOM kills or node failures — those are handled by replica count and
anti-affinity.

**94. How can a PDB break a cluster upgrade?**
`minAvailable: 2` with `replicas: 2` means nothing can ever be evicted, so a drain hangs
forever and the upgrade stalls. Always leave headroom.

**95. Liveness probe keeps restarting a Pod that looks healthy in the logs. Why?**
The probe is misconfigured — too short an `initialDelaySeconds`, too low a
`failureThreshold`, or a path that returns non-2xx. `kubectl describe pod` shows the
`Unhealthy` events and the status code, which is usually a 404 you did not expect.

---

## 13. Security, RBAC and multi-tenancy

**96. Explain RBAC's four objects.**
Role/ClusterRole define permissions; RoleBinding/ClusterRoleBinding grant them. A
RoleBinding grants within one namespace and **may reference a ClusterRole** — which is the
normal pattern: define once, grant per namespace.

**97. How do you verify a permission before shipping it?**
`kubectl auth can-i <verb> <resource> --as=system:serviceaccount:<ns>:<sa>`. Impersonation
is how you test RBAC instead of hoping.

**98. Why `automountServiceAccountToken: false`?**
Most applications never call the API server, so the projected token is an unnecessary
credential — and by default it belongs to the over-privileged `default` ServiceAccount.

**99. What is Pod Security Admission?**
Namespace-labelled enforcement of `privileged`/`baseline`/`restricted` Pod hardening.
`restricted` requires non-root, dropped capabilities and a seccomp profile; **both**
`baseline` and `restricted` forbid `hostPath` volumes, `hostNetwork` and `hostPID`, which
is why node agents need their own namespace.
which is why node agents live in their own namespace at a looser level.

**100. How do you isolate two tenants in one cluster?**
Namespaces alone do not do it. You need NetworkPolicy (default-deny), RBAC least
privilege, ResourceQuota + LimitRange (so one tenant cannot starve the others), Pod
Security, and ideally separate clusters for genuinely hostile tenants. Namespaces are a
*soft* boundary.

**101. Is encryption at rest on by default?**
No. Configure `EncryptionConfiguration` on the API server, or an etcd backup contains every
Secret in plaintext. This is a favourite interview question because the answer is
surprising.

---

## 14. Troubleshooting scenarios

These are the questions that decide senior-level interviews. Answer with a **method**, not
a guess.

**102. A Pod is `Pending`. Diagnose it.**
Scheduling layer: `kubectl describe pod` → `FailedScheduling`. Four causes: insufficient
CPU/memory requests on all Nodes, unsatisfiable `nodeSelector`/affinity, an untolerated
taint, or an unbound PVC. Then confirm the Node side with
`kubectl describe node` → "Allocated resources".

**103. A Pod is `CrashLoopBackOff` with no useful logs.**
`kubectl logs --previous` first (the current container has barely started; the interesting
run already ended). Then check the exit code — 137 means `OOMKilled`, not a code bug. Then
check `describe` for `Last State` and for liveness-probe kills.

**104. A Pod is `Running` but `0/1`, and users report 503s.**
Readiness probe is failing. `kubectl describe pod` → `Unhealthy ... statuscode: 404`.
Check whether the Pod is in any EndpointSlice. If it is not, no traffic is being sent —
the fix is in the probe or in the endpoint the probe calls, not in the Service.

**105. A Service returns nothing. Walk me through it.**
`kubectl get endpointslices` first — empty means either a selector mismatch or no Ready
Pods. Compare `kubectl describe svc` (Selector) with `kubectl get pods --show-labels`. Only
if endpoints are healthy do you look at `targetPort`, then DNS, then NetworkPolicy.

**106. The Ingress returns 503. What is your first command?**
`kubectl get endpointslices` — 503 means the rule matched but the backend had no Ready
endpoints. The Ingress is innocent.

**107. Users report intermittent 502s during every deploy.**
No readiness probe (or a probe that passes too early), so not-yet-ready Pods receive
traffic. Also check `preStop`/grace period: without a small `preStop` sleep, endpoint
propagation lags and a few requests hit a shutting-down Pod.

**108. `kubectl top` does not work.**
metrics-server is missing, not Ready, or cannot reach the kubelets (on Docker Desktop it
usually needs `--kubelet-insecure-tls`). Check
`kubectl logs -n kube-system deploy/metrics-server`.

**109. Everything in a namespace stopped working at once, and it started right after a
NetworkPolicy change.**
Two usual suspects: DNS egress was not allowed, or kubelet probes are being blocked (look
for `Unhealthy` events). Both look like an application problem from inside the Pod.

**110. A PersistentVolumeClaim will not bind.**
`kubectl describe pvc` Events: no default StorageClass, a `storageClassName` that does not
exist, an unhealthy provisioner, or `WaitForFirstConsumer` waiting for a Pod.

**111. A namespace is stuck `Terminating`.**
A finalizer is waiting on a resource that cannot be deleted (often a CRD's resources, or an
unreachable API service). Inspect `spec.finalizers` and `status.conditions`, then remove
the blocking resource — not the namespace's finalizer, which is the "have you tried turning
it off" of Kubernetes and can orphan real resources.

**112. The cluster is fine but one application is slow, and nothing is erroring.**
Check CPU limits first — throttling looks like random slowness and never appears in logs.
`kubectl top pods --containers` versus the limits. Then check the app's own dependency
latency, and whether a readiness probe is flapping.

---

## 15. Design questions

**113. How would you design the rollout of a schema change with zero downtime?**
Expand/contract migrations, run as a Job before the new Deployment, gated on completion.
Backwards-compatible schema so old and new code coexist. Rolling update with
`maxUnavailable: 0` and correct readiness probes. `kubectl rollout undo` ready, and
migrations designed so a rollback does not require a schema rollback.

**114. How would you expose 20 microservices publicly?**
One Ingress (or Gateway API) with host/path routing, one certificate strategy (cert-manager),
Services as ClusterIP, TLS terminated at the controller, and NetworkPolicies so only the
controller can reach the apps. Never a LoadBalancer per service.

**115. How would you give a team their own space in a shared cluster?**
A namespace per team/environment, with ResourceQuota + LimitRange, a default-deny
NetworkPolicy plus explicit allows, RBAC Roles bound to their group (not cluster-admin), Pod
Security at `restricted`, and cluster-scoped resources (ingress controller, CSI, CNI) owned
by the platform team.

**116. A vendor asks which IP to allowlist for your egress traffic.**
The NAT gateway's static IP (or the egress gateway's dedicated IP) — never a Pod CIDR. If
those change per environment, that is the argument for a dedicated egress gateway. This is
also the moment to ask *whether* you should be calling that vendor directly at all.

**117. How do you know your application is healthy in production?**
Not by "the Pod is Running". Four layers: probes for correctness (liveness/readiness),
metrics on the golden signals (latency, traffic, errors, saturation), logs, and tracing
across service boundaries. Alerts fire on user-visible symptoms (error rate, latency), not
on CPU. And a dashboard that only says "up/down" is not monitoring.

**118. A dependency your service calls goes down. What should happen?**
The service must **degrade, not collapse**: timeouts on every outbound call, circuit
breaking, bounded retries with backoff and jitter, graceful degradation where possible, and
readiness probes that do not all flip at once. Your liveness probe must never depend on it,
or you will convert one outage into two.

**119. What would you change about this lab's design for production?**
TLS via cert-manager; managed Postgres instead of a single-replica StatefulSet; a real log
pipeline instead of the stand-in DaemonSet; GitOps (Argo CD) instead of hand-applied YAML;
Kustomize/Helm for the four environments; KEDA autoscaling the worker on queue depth;
admission policy (Kyverno) enforcing probes, resources and image provenance; multi-node
clusters with topology spread; and an etcd backup plus a tested restore.

**120. How do you keep the cluster in sync with what is in Git?**
GitOps: a controller (Argo CD/Flux) continuously reconciles the cluster to the repository,
and reports drift. It also inverts the deployment model — CI builds an image, but only Git
changes reach the cluster. Pair it with admission policy so a manual `kubectl edit` cannot
create unmanaged state, and with immutable, versioned manifests so rollbacks are revert
commits.

---

## 16. Rapid fire — one-line answers

| Question | Answer |
|---|---|
| Container vs Pod? | A container is a process; a Pod is the schedulable unit that owns one or more containers. |
| Pod vs ReplicaSet? | A Pod is the workload; a ReplicaSet keeps N of them alive. |
| ReplicaSet vs Deployment? | A Deployment adds rollouts, rollback and revision history on top of a ReplicaSet. |
| Deployment vs StatefulSet? | Interchangeable replicas vs stable identity, DNS and per-replica storage. |
| Deployment vs DaemonSet? | "N somewhere" vs "one per Node". |
| Job vs Deployment? | Runs to completion vs runs forever. |
| Service vs Ingress? | L4 stable address inside the cluster vs L7 routing to it from outside. |
| ClusterIP vs NodePort? | Internal VIP vs a port opened on every Node. |
| Headless vs ClusterIP? | Per-Pod DNS records vs one load-balanced VIP. |
| requests vs limits? | Scheduling reservation vs runtime cap. |
| readiness vs liveness? | Traffic vs restarts. |
| ConfigMap vs Secret? | Plain text config vs base64 credentials with separate RBAC. |
| PV vs PVC? | The disk vs the request for a disk. |
| Egress vs Ingress? | Outbound vs inbound. |
| NetworkPolicy default? | Allow everything until a policy selects the Pod. |
| Can a NetworkPolicy deny? | No — allow-list only. |
| What restarts a Pod? | A controller. (The kubelet restarts *containers*.) |
| What schedules a Pod? | kube-scheduler. |
| What runs a container? | The kubelet, via the CRI runtime. |
| What stores cluster state? | etcd. |
| Does the control plane carry traffic? | No. |
| Is base64 encryption? | No. |
| Does a StatefulSet give HA? | No — identity and storage only. |
