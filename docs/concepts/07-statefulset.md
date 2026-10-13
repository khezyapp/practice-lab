# Concept 07 — StatefulSet

> **In one sentence:** A StatefulSet is the controller for Pods that need a **stable name**,
> **stable storage** and **ordered** startup/shutdown — databases, queues, and anything else
> where two replicas are *not* interchangeable.

Estimated read: 12 minutes. Prerequisites: [Deployment](03-deployment.md), [Service](04-service.md).

---

## 1. What a Deployment cannot do

A Deployment produces anonymous, interchangeable replicas. That is a feature for a stateless
API — and a showstopper for Postgres:

| Requirement | Deployment | StatefulSet |
|---|---|---|
| Stable Pod name | ❌ random suffix, changes every rollout | ✅ `postgres-0`, `postgres-1` — stable forever |
| Stable DNS per replica | ❌ only a load-balanced Service name | ✅ `postgres-0.postgres.taskflow.svc.cluster.local` |
| Own persistent disk per replica | ❌ shared or none | ✅ `volumeClaimTemplates` → one PVC per Pod |
| Ordered startup | ❌ all at once | ✅ `0`, then `1`, then `2` |
| Ordered shutdown | ❌ all at once | ✅ highest ordinal first |
| Rolling update one at a time | ✅ (but arbitrary) | ✅ in reverse ordinal order, with `partition` for canaries |

A StatefulSet is what makes `postgres-0` a *meaningful address*: the primary, always,
even after a reschedule to another Node.

> **Critical mindset shift:** a StatefulSet gives identity and storage. It does **not** give
> you replication, automatic failover, backups or split-brain prevention. Those are the
> database's job, and you must configure them. Kubernetes will not save your data.

---

## 2. The four things a StatefulSet adds

### 1. Ordinal identity

Pods are named `<statefulset>-<ordinal>`: `postgres-0`, `postgres-1`. The ordinal is part of
the Pod's identity, so if `postgres-1` dies, it comes back as `postgres-1` — with the same
PVC and the same DNS name.

### 2. A headless Service for per-Pod DNS

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: taskflow
spec:
  clusterIP: None              # headless -> DNS returns one A record per Pod
  selector:
    app.kubernetes.io/name: postgres
  ports:
    - { name: pg, port: 5432, targetPort: 5432 }
```

```powershell
nslookup postgres.taskflow.svc.cluster.local      # cluster-wide name (all Pod IPs)
nslookup postgres-0.postgres.taskflow.svc.cluster.local   # one exact replica
```

Consumers then say "connect to `postgres-0`" for writes and "connect to `postgres`" for
reads. That per-Pod DNS record is precisely what a normal ClusterIP cannot offer.

### 3. `volumeClaimTemplates` — storage per replica

Instead of a shared volume, each replica gets **its own PVC, generated from a template**:

```yaml
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests: { storage: 2Gi }
        # no storageClassName -> uses the cluster default (lab-friendly)
```

The resulting objects are `data-postgres-0`, `data-postgres-1`, … and they are **bound to
that ordinal**, not to the Pod. Reschedule, restart, scale down and up again — `postgres-0`
always remounts `data-postgres-0`.

> Gotcha worth memorising: **deleting a StatefulSet does not delete its PVCs.** This is
> deliberate protection. It also means you must clean up PVCs yourself, or your cluster will
> quietly accumulate "orphaned" disks. Scale-down likewise keeps the PVC.

### 4. Ordered operations

- **Create/scale-up:** `0` must be `Ready` before `1` is created (`podManagementPolicy:
  OrderedReady`, the default).
- **Scale-down/delete:** highest ordinal first, and the ordinal is **never reused** unless
  you scale back up to it.
- **Rolling update:** highest ordinal first, one at a time.

`podManagementPolicy: Parallel` skips the ordering — appropriate only when replicas are
effectively independent (some caches, some distributed systems that find peers themselves).

---

## 3. The YAML

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: taskflow
  labels:
    app.kubernetes.io/name: postgres
    app.kubernetes.io/part-of: taskflow
spec:
  serviceName: postgres              # REQUIRED: the headless Service that governs DNS
  replicas: 1
  podManagementPolicy: OrderedReady
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      partition: 0                   # only Pods with ordinal >= partition are updated
  selector:
    matchLabels:
      app.kubernetes.io/name: postgres
  template:
    metadata:
      labels:
        app.kubernetes.io/name: postgres
    spec:
      securityContext:
        fsGroup: 999                 # postgres image's group, so it can write the volume
      containers:
        - name: postgres
          image: postgres:16-alpine
          ports: [{ name: pg, containerPort: 5432 }]
          env:
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata   # subdir avoids lost+found conflicts
            - name: POSTGRES_DB
              value: taskflow
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: { name: taskflow-secret, key: DB_PASSWORD }
          resources:
            requests: { cpu: 250m, memory: 512Mi }
            limits:   { cpu: "1",  memory: 1Gi }
          readinessProbe:
            exec:
              command: ["sh","-c","pg_isready -U taskflow -d taskflow"]
            initialDelaySeconds: 10
            periodSeconds: 5
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests: { storage: 2Gi }
```

Two details that save hours:

- **`PGDATA` in a subdirectory.** Some volume plugins leave a `lost+found` directory at the
  mount root, and Postgres refuses to initialise a data directory that is not empty.
- **`fsGroup`.** Without it, a non-root container gets `permission denied` writing to a
  freshly mounted volume.

---

## 4. Rolling updates with `partition`

`partition: 0` updates everything. Set `partition: 2` on a 3-replica StatefulSet and only
`postgres-2` is updated — a **canary** for stateful workloads:

```powershell
kubectl patch statefulset postgres -n taskflow -p '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":2}}}}'
kubectl get pods -n taskflow -l app.kubernetes.io/name=postgres -o jsonpath="{range .items[*]}{.metadata.name}{' '}{.spec.containers[0].image}{'\n'}{end}"
# verify, then:
kubectl patch statefulset postgres -n taskflow -p '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":0}}}}'
```

`updateStrategy.type: OnDelete` means nothing happens until you delete a Pod yourself — the
manual mode used by database operators that need to orchestrate failover.

---

## 5. Deployment vs StatefulSet, in one table

| | Deployment | StatefulSet |
|---|---|---|
| Pod name | random hash | ordinal, stable |
| DNS | Service VIP only | `pod-0.svc.ns.svc.cluster.local` |
| Storage | shared/PVC attached to the workload | PVC per replica, follows the ordinal |
| Start order | parallel | sequential (default) |
| Use for | APIs, workers, frontends, anything stateless | databases, Kafka, Zookeeper, Elasticsearch, Redis with persistence |
| Scaling | trivial and always safe | often requires app-level rebalancing |

**Rule of thumb:** if losing one replica and getting a differently-named one back is
harmless, use a Deployment. If a replica's *name* or *disk* matters, use a StatefulSet.

---

## 6. When a StatefulSet is still the wrong tool

Managed databases exist for a reason. Running Postgres in Kubernetes means you own backups,
PITR, replication, failover, upgrades and split-brain. Consider instead:

| Situation | Better option |
|---|---|
| Production database with an SLA | Managed DB (RDS/Cloud SQL/Aurora) + `ExternalName` Service |
| You must self-host, but want fewer mistakes | An **operator**: CloudNativePG, Zalando Postgres Operator, Percona — a CRD + controller that encodes the operational knowledge |
| Dev/CI ephemeral database | A StatefulSet is fine (this lab), or even a plain Pod with `emptyDir` |
| Read replicas / sharding | Operator, or a purpose-built distributed system (Vitess, CockroachDB) |

A StatefulSet is the *primitive*. An operator is the *product*. Know both, and be honest
about which one you are qualified to operate at 3am.

---

## 7. Commands

```powershell
kubectl apply -f labs/07-postgres-statefulset.yaml
kubectl get statefulset,pods,pvc -n taskflow
kubectl get pvc -n taskflow                        # data-postgres-0, Bound
kubectl describe statefulset postgres -n taskflow
kubectl rollout status statefulset/postgres -n taskflow
kubectl scale statefulset postgres --replicas=0 -n taskflow   # PVCs REMAIN
kubectl get pvc -n taskflow                        # proof: still there
```

Prove the identity and the storage:

```powershell
# connect to the exact replica by DNS from a throwaway Pod
kubectl run psql --rm -it --image=postgres:16-alpine -n taskflow -- `
  psql -h postgres-0.postgres -U taskflow -d taskflow -c "\dt"

# delete the Pod and watch the PVC reattach to the new one
kubectl delete pod postgres-0 -n taskflow
kubectl get pod postgres-0 -n taskflow -w
```

---

## 8. Common mistakes

1. **Using a Deployment for a database.** Multiple replicas writing one volume is data
   corruption waiting to happen.
2. **Forgetting `serviceName`** (or pointing it at a non-headless Service) → no per-Pod DNS,
   and clients cannot address a specific replica.
3. **Deleting PVCs and expecting the data back.** `kubectl delete statefulset` keeps the
   PVCs; `kubectl delete pvc --all` does not.
4. **Assuming the StatefulSet gives HA.** It does not. `replicas: 3` of Postgres without
   replication config is three unrelated databases.
5. **No `fsGroup` / wrong `PGDATA`** → permission errors and "data directory not empty" on
   first boot. Very common first-time failure.
6. **`ReadWriteOnce` with multiple Pods on different Nodes.** RWO is per-Node; two Pods on
   different Nodes cannot share it. Postgres does not want a shared volume anyway.
7. **No resource requests on a database.** It gets evicted under node pressure at the worst
   possible moment.
8. **Forgetting that scale-down keeps the PVC** → "why is my storage bill growing?"
9. **Not testing restore.** You have no backups until you have restored one.

---

## 9. Check yourself

1. What does a StatefulSet give you that a Deployment cannot? → *stable Pod identity,
   per-Pod DNS, per-replica PVCs, ordered rollout*
2. Does a StatefulSet make Postgres highly available? → *No. Replication, failover and
   backups are yours to configure.*
3. You `kubectl delete statefulset postgres`. What happens to the data? → *It survives; the
   PVCs are retained by design.*
4. Why is `serviceName` mandatory, and what type must that Service be? → *It governs per-Pod
   DNS; the Service must be headless (`clusterIP: None`)*
5. How do you canary a StatefulSet image change? → *`updateStrategy.rollingUpdate.partition`*
6. Pod `postgres-1` is deleted. What name does its replacement get? → *`postgres-1`, same
   PVC, same DNS*
7. Which PVC does `postgres-0` mount after being rescheduled to another Node? → *`data-postgres-0`*

---

**Practice it:** [Lab step 07 — StatefulSet + Postgres](../lab/07-statefulset-postgres.md) ·
**Next lesson:** [DaemonSet](08-daemonset.md)
