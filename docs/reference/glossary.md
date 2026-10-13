# Reference — Glossary

Terms used across these docs, in the sense this lab uses them. Short, and aimed at
"I need to be precise about this in a conversation".

---

## Core objects

| Term | Meaning |
|---|---|
| **Pod** | The smallest deployable unit. One or more containers sharing a network namespace, IP and volumes, always scheduled to the same Node. Ephemeral. |
| **Container** | An isolated process, run by the container runtime (containerd/CRI-O) on the kubelet's instruction. Not scheduled on its own. |
| **Node** | A machine (VM or physical) in the cluster. Runs the kubelet, the container runtime and kube-proxy. |
| **Cluster** | A control plane plus a set of Nodes. |
| **Namespace** | A naming and policy scope for objects. **Not** a network or security boundary by itself. |
| **Label** | A key/value pair on an object, used by selectors to *find* things. The wiring of Kubernetes. |
| **Annotation** | Non-identifying metadata (controller configuration, tooling hints). Not selectable. |
| **Selector** | A label query. Used by Services, Deployments, NetworkPolicies, HPAs, PDBs. |
| **Manifest** | A YAML/JSON file declaring one or more objects. |

## Controllers (workloads)

| Term | Meaning |
|---|---|
| **Controller** | A process running a reconciliation loop for one kind of object. |
| **Deployment** | Manages ReplicaSets; gives self-healing, scaling, rolling updates and rollback for stateless workloads. |
| **ReplicaSet** | Ensures N identical Pods exist. Managed by a Deployment; you rarely touch it directly. |
| **StatefulSet** | Pods with stable ordinal identity, per-Pod DNS and per-replica PVCs, created and updated in order. |
| **DaemonSet** | Exactly one Pod per eligible Node, including Nodes added later. |
| **Job** | Runs Pods to completion, with retries and a completion condition. |
| **CronJob** | Creates Jobs on a schedule. |
| **HPA** | HorizontalPodAutoscaler — adjusts `replicas` based on metrics (usually CPU). |
| **VPA** | VerticalPodAutoscaler — adjusts requests/limits instead of replica count. |
| **PDB** | PodDisruptionBudget — caps how many Pods can be *voluntarily* disrupted at once. |
| **Operator** | A CRD + controller encoding operational knowledge ("how to run Postgres properly"). |

## Networking

| Term | Meaning |
|---|---|
| **Service** | A stable virtual IP + DNS name that load-balances across Pods matching a selector. |
| **ClusterIP** | The internal-only default Service type. Not routable from outside the cluster. |
| **NodePort** | Opens the same port on every Node (`30000-32767`). |
| **LoadBalancer** | NodePort plus a cloud load balancer. Stays `<pending>` without a cloud controller. |
| **ExternalName** | A DNS alias to an external host. No proxy, no IP. |
| **Headless Service** | `clusterIP: None` — DNS returns one A record per Pod instead of a VIP. Required by StatefulSets. |
| **Endpoints / EndpointSlice** | The live list of Ready Pod IPs behind a Service. Empty endpoints = "the Service is broken". |
| **Ingress** | A *routing rule* (host + path → Service). Inert without a controller. |
| **Ingress controller** | The proxy Pod (ingress-nginx, Traefik…) that implements Ingress objects. |
| **IngressClass** | Names which controller should handle an Ingress. |
| **Gateway API** | The successor to Ingress: `GatewayClass`, `Gateway`, `HTTPRoute`. Richer matching, TCP/UDP, role separation. |
| **Egress** | Traffic leaving a Pod. Unrestricted by default. |
| **NetworkPolicy** | Pod-level L3/L4 firewall. Additive allow-rules only; needs a CNI that enforces it. |
| **CNI** | Container Network Interface plugin — assigns Pod IPs and (sometimes) enforces NetworkPolicy. |
| **kube-proxy** | Programs the Service virtual IPs into iptables/IPVS rules (or replaced by eBPF). |
| **CoreDNS** | Cluster DNS: `service.namespace.svc.cluster.local`. |
| **SNAT** | Source NAT. Where the outside world sees your egress traffic coming from a Node/NAT IP, not a Pod IP. |
| **ipBlock** | A NetworkPolicy selector for literal CIDRs — the only way to allow an external host. |
| **pathType** | Ingress path matching: `Prefix` (element-aware), `Exact`, or `ImplementationSpecific` (regex, for nginx). |

## Storage

| Term | Meaning |
|---|---|
| **Volume** | Storage declared in a Pod spec and mounted into a container. |
| **emptyDir** | Scratch space that lives and dies with the Pod. |
| **hostPath** | A path on the *Node*. Node-local, unmovable, a security consideration. For agents only, never for app data. |
| **PV** | PersistentVolume — the actual disk, cluster-scoped. |
| **PVC** | PersistentVolumeClaim — a namespaced *request* for storage ("2Gi, ReadWriteOnce"). |
| **StorageClass** | Describes a class of storage + its provisioner. Enables dynamic provisioning. |
| **CSI** | Container Storage Interface — the driver that talks to the actual storage backend. |
| **Access mode** | `RWO` (one Node), `ROX`, `RWX` (many Nodes; needs shared storage), `RWOP` (one Pod). |
| **Reclaim policy** | What happens to the PV when the PVC is deleted: `Delete` or `Retain`. |
| **volumeBindingMode** | `Immediate` or `WaitForFirstConsumer` (needed for topology-constrained cloud disks). |

## Configuration

| Term | Meaning |
|---|---|
| **ConfigMap** | Non-sensitive configuration, injected as env vars or mounted files. |
| **Secret** | Base64-encoded credentials. **Not** encrypted at rest unless you configure it. |
| **`stringData`** | Plain-text Secret values; the API server base64-encodes them on write. |
| **Downward API** | Exposes Pod/Node metadata (name, IP, node) as env vars or files. |
| **`subPath`** | Mounts a single key as a file. Never updates after container start. |

## Health and resources

| Term | Meaning |
|---|---|
| **request** | What the scheduler reserves on a Node. |
| **limit** | The runtime cap enforced by cgroups. CPU throttles; memory OOM-kills. |
| **QoS class** | `Guaranteed` (requests == limits), `Burstable`, `BestEffort`. Determines eviction order. |
| **startupProbe** | Runs before liveness/readiness arm. For slow boots. |
| **readinessProbe** | Controls Service endpoints. Failure removes traffic; no restart. |
| **livenessProbe** | Failure restarts the container. Must never check a dependency. |
| **OOMKilled** | Exit code 137 — the process exceeded its memory limit. |
| **Throttling** | CPU usage capped at the limit; latency rises, nothing crashes. |
| **Thundering herd** | What happens when a dependency blips and every replica restarts at once. |

## Control plane mechanics

| Term | Meaning |
|---|---|
| **kube-apiserver** | The only component that talks to etcd. AuthN, AuthZ, admission, validation. |
| **etcd** | The cluster's only state store. Back it up. |
| **kube-scheduler** | Picks a Node for each unscheduled Pod (filter, then score, then bind). |
| **kube-controller-manager** | Runs the built-in controllers (Deployment, ReplicaSet, Node, Job…). |
| **kubelet** | Node agent. Starts containers, mounts volumes, runs probes, reports status. |
| **cloud-controller-manager** | Talks to the cloud API for load balancers, routes, node lifecycle. |
| **Admission controller** | Hook between authorization and persistence; defaults, validates, mutates. |
| **Pod Security Admission** | Namespace-labelled enforcement of `privileged` / `baseline` / `restricted`. |
| **RBAC** | Role/ClusterRole (permissions) + RoleBinding/ClusterRoleBinding (grants). |
| **ServiceAccount** | The identity a Pod uses to call the API server. `automountServiceAccountToken: false` is the safe default. |
| **ownerReferences** | The ownership graph (Pod → ReplicaSet → Deployment) that drives garbage collection. |
| **Finalizer** | A hook that blocks deletion until something cleans up. The usual cause of a namespace stuck `Terminating`. |

## Declarative concepts

| Term | Meaning |
|---|---|
| **Declarative** | You state the desired end state; the system converges to it. |
| **Reconciliation** | The loop comparing desired vs actual state and acting on the difference. |
| **Level-triggered** | Reacting to *current state*, not to the event that changed it. Why self-healing works. |
| **Idempotent** | Running it twice is the same as running it once. Required for Jobs and `apply`. |
| **Immutable** | Cannot be changed after creation (a Pod's `spec` fields, a Deployment's `selector`, an immutable ConfigMap). |
| **Drift** | Reality diverging from Git. What GitOps exists to detect. |
| **Golden signals** | Latency, traffic, errors, saturation — what to alert on. |
| **Expand/contract** | A migration pattern: add, use, then remove. Required when old and new code run simultaneously. |

## Failure vocabulary (what each status actually means)

| Status | Layer | One-line meaning |
|---|---|---|
| `Pending` | scheduling | no Node accepted it (resources, selector, taint, PVC) |
| `ContainerCreating` | kubelet | volumes/CNI/image still in progress |
| `ImagePullBackOff` | image | the runtime cannot fetch the image |
| `CreateContainerConfigError` | config | env/volume references could not be resolved |
| `Running 0/1` | application | not Ready — usually a failing readiness probe |
| `CrashLoopBackOff` | application | the process exits; the kubelet retries with backoff |
| `Completed` | Job | it finished on purpose |
| `Terminating` (stuck) | lifecycle | grace period, finalizer, or an unresponsive runtime |
| `Evicted` | node | the node was under pressure and needed the room |
| `OOMKilled` | resources | memory limit exceeded (exit 137) |
| `Unknown` | node | the control plane cannot reach the kubelet |
