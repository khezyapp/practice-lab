# Concept 08 — DaemonSet

> **In one sentence:** A DaemonSet runs exactly **one copy of a Pod on every Node** (or on
> every Node that matches a selector), automatically including Nodes added later — the
> controller for node-level agents.

Estimated read: 10 minutes. Prerequisite: [Deployment](03-deployment.md).

---

## 1. The problem it solves

Some software is not "a service with N replicas". Its job is to be present **on the machine
itself**:

- read that machine's log files,
- report that machine's CPU/disk/network metrics,
- attach that machine's network interfaces (CNI),
- mount that machine's disks (CSI node plugin),
- scan that machine for security issues.

For these, "three replicas somewhere in the cluster" is wrong — you need *one on each Node*,
and you need one automatically when a new Node joins the cluster.

A Deployment cannot express that. You can approximate it with `replicas: <node count>` plus
anti-affinity, but it does not track Node membership: add a Node and you still have N Pods;
remove one and you have a stranded Pod.

| | Deployment | DaemonSet |
|---|---|---|
| Count | `replicas` you choose | derived: one per matching Node |
| New Node joins | nothing happens | Pod is created automatically |
| Node leaves | Pod rescheduled elsewhere | Pod is deleted, not replaced |
| Field `replicas` | required | **not allowed** |
| Typical content | your application | agents, exporters, plugins |

---

## 2. The YAML

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-log-agent
  namespace: node-agents
  labels:
    app.kubernetes.io/name: node-log-agent
    app.kubernetes.io/component: agent
    app.kubernetes.io/part-of: taskflow
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: node-log-agent
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1              # or a percentage, e.g. "10%"
  template:
    metadata:
      labels:
        app.kubernetes.io/name: node-log-agent
    spec:
      # Optional: only run on Nodes with this label
      nodeSelector:
        kubernetes.io/os: linux
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule        # required to run on control-plane Nodes
      containers:
        - name: agent
          image: busybox:1.36
          command: ["sh","-c","while true; do echo \"$(date) $(hostname) collecting node logs\"; sleep 30; done"]
          env:
            - name: NODE_NAME
              valueFrom:
                fieldRef: { fieldPath: spec.nodeName }
            - name: POD_NAME
              valueFrom:
                fieldRef: { fieldPath: metadata.name }
          resources:
            requests: { cpu: 50m,  memory: 64Mi }
            limits:   { cpu: 200m, memory: 128Mi }
          volumeMounts:
            - name: host-logs
              mountPath: /host/var/log
              readOnly: true
      volumes:
        - name: host-logs
          hostPath:
            path: /var/log
            type: Directory
```

Note what a DaemonSet does **not** have: no `replicas`, no `strategy.rollingUpdate.maxSurge`.
It also does not need a Service — nothing load-balances to it; it is a node agent.

---

## 3. How the DaemonSet Pod gets onto each Node

Modern Kubernetes (1.12+) does not bypass the scheduler for DaemonSet Pods. Instead the
controller creates a Pod **already pinned** to a Node, by injecting node affinity on the
Node's name:

```yaml
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchFields:
              - key: metadata.name
                operator: In
                values: [node-name]
```

Consequences you should internalise:

- The Pod is still subject to **taints** and **resource pressure**. If no Node can take it,
  the DaemonSet reports `MISSCHEDULED` rather than `Pending`.
- `nodeSelector`/`affinity` on the DaemonSet's Pod template reduces which Nodes it covers —
  that is how you scope an agent to GPU Nodes or to Linux only.

```powershell
kubectl get daemonset -n node-agents
# DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR
#   1         1         1         1            1       kubernetes.io/os=linux
```

`DESIRED` is literally "how many eligible Nodes exist". If `DESIRED` is 0, your
`nodeSelector` matches nothing.

### Default tolerations you inherit

A DaemonSet Pod automatically tolerates the ordinary Node-condition taints
(`not-ready`, `unreachable`, `disk-pressure`, `memory-pressure`, `pid-pressure`,
`unschedulable`) so that an agent keeps running while its Node is unhealthy — which is
exactly when you need the agent.

It does **not** automatically tolerate role taints. To run on a control-plane Node you must
add the toleration yourself, as shown above.

### Verifying "one per Node"

```powershell
kubectl get pods -n node-agents -o wide -l app.kubernetes.io/name=node-log-agent
# every NODE column value should appear exactly once
kubectl get nodes -o name | Measure-Object -Line   # node count
```

On a single-node cluster (this lab) you will see exactly one Pod. To see the real behaviour,
recreate minikube with `minikube start --nodes=3` or drain/uncordon a Node in a multi-node
cluster — the count follows the Node count automatically.

---

## 4. Node access: `hostPath`, `hostNetwork`, `hostPID`, `privileged`

> **Consequence you must plan for:** Pod Security Admission is enforced **per
> namespace**, and `hostPath` volumes, `hostNetwork` and `hostPID` are all
> forbidden at the `baseline` level (not just `restricted`). A node agent needs
> host access by definition, and there is **no per-Pod exemption** — so agents
> belong in their own namespace with their own, looser level. That is why this
> lab's DaemonSet lives in `node-agents` rather than in the `taskflow` namespace
> with the rest of the application. Namespace splitting for privileged
> infrastructure is the standard pattern, not a workaround.

Agents usually need to see the Node's filesystem or network namespace. These are the tools,
in increasing order of danger:

| Mechanism | Gives access to | Danger |
|---|---|---|
| `hostPath` (read-only) | a directory on the Node, e.g. `/var/log` | medium — read-only reduces it a lot |
| `hostPath` (read-write) | and lets you modify it | **high** — a container can alter the Node |
| `hostNetwork: true` | the Node's network namespace (ports, interfaces) | high — port conflicts with the Node; sees all traffic |
| `hostPID: true` | the Node's process namespace | high — can inspect/kill host processes |
| `securityContext.privileged: true` | effectively root on the Node | **critical** — treat as a full Node compromise if abused |

Rules of thumb:

1. Prefer read-only `hostPath` and nothing else.
2. `privileged: true` only for CNI/CSI-type plugins, and pin those to a trusted namespace
   with a Namespace-level Pod Security Admission label (`pod-security.kubernetes.io/enforce:
   privileged`).
3. **Do not** give an application DaemonSet host access. If your *business* code needs it,
   that is a design smell.
4. Remember that `hostPath` Pods that are rescheduled see a *different* Node's files — so
   never treat `hostPath` as storage for data you care about.

---

## 5. Comparing with the alternatives

| Need | Use |
|---|---|
| One agent per Node, always | **DaemonSet** |
| N copies of a service, spread out | Deployment + `topologySpreadConstraints` / `podAntiAffinity` |
| One copy *cluster-wide* (a singleton leader) | Deployment with `replicas: 1` (+ `Recreate` strategy) or a leader-elected controller |
| One copy on *specific* Nodes | DaemonSet + `nodeSelector` / node affinity |
| Run something once per Node, then stop | A Job per Node is awkward; DaemonSet + a sentinel file, or a Kubernetes `Job` with one Pod per Node via a controller |

System DaemonSets you already have in any cluster: `kube-proxy`, the CNI agent, the CSI node
plugin, and (on cloud) the cloud-controller node agent. Run
`kubectl get daemonset -A` to see them.

---

## 6. Updating a DaemonSet

```powershell
kubectl rollout status daemonset/node-log-agent -n node-agents
kubectl set image daemonset/node-log-agent agent=busybox:1.37 -n node-agents
kubectl rollout undo daemonset/node-log-agent -n node-agents
```

`RollingUpdate` with `maxUnavailable: 1` updates Nodes one at a time — deliberately
conservative, because upgrading every node agent at once is how you take a whole cluster out.
`type: OnDelete` means "wait for me to delete each Pod", used when the agent must be
upgraded in a specific order (very common for CNI plugins where the dataplane must stay up).

---

## 7. Common mistakes

1. **Expecting `replicas`.** DaemonSets ignore/forbid it — the Node count is the replica
   count.
2. **No `tolerations`** and then wondering why the control-plane Node has no agent.
3. **`nodeSelector` that matches nothing** → `DESIRED 0`. Check labels with
   `kubectl get nodes --show-labels`.
4. **No resource requests.** An agent with no requests can be starved on a busy Node; agents
   with huge requests can make the Node unschedulable. Both hurt.
5. **`privileged: true` "just to make it work".** Every privileged Pod is a Node-level
   security hole.
6. **Assuming DaemonSet Pods carry application traffic.** They are not behind a Service and
   are not load-balanced; they are per-Node processes.
7. **Writing application state to `hostPath`.** It is Node-local, unbacked and unmovable.
8. **Blocking the cluster with a bad CNI update.** Always canary a networking DaemonSet, and
   remember a broken CNI DaemonSet can make Nodes `NotReady` — including the one you need to
   fix it.

---

## 8. Check yourself

1. How many Pods does a DaemonSet with `replicas` unset create in a 5-node cluster? → *5, one
   per Node*
2. What happens when you add a Node? → *the DaemonSet controller creates a Pod for it
   automatically*
3. How do you make a DaemonSet run only on GPU Nodes? → *`nodeSelector` or node affinity on
   the Pod template*
4. Why does a DaemonSet need a toleration to reach control-plane Nodes? → *role taints are
   not among the taints DaemonSet Pods tolerate by default*
5. Where does `DESIRED` come from in `kubectl get ds`? → *the number of Nodes matching the
   Pod template's node selector/affinity (and tolerations)*
6. Why is it dangerous to update a CNI DaemonSet with `maxUnavailable: 100%`? → *you can lose
   Pod networking cluster-wide, including the control plane's ability to talk to Nodes*
7. Name three real DaemonSets you already have. → *kube-proxy, the CNI agent, the CSI node
   plugin*

---

**Practice it:** [Lab step 08 — DaemonSet](../lab/08-daemonset.md) ·
**Next lesson:** [ConfigMaps & Secrets](09-configmaps-and-secrets.md)
