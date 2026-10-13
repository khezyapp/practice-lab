# Lab 02 — Namespace, ConfigMap, Secret

**Goal:** the `taskflow` namespace exists with guard rails, and the application's
configuration lives in Kubernetes objects — **not** in the image.

**Read first:** [ConfigMaps & Secrets](../concepts/09-configmaps-and-secrets.md),
[Namespaces, RBAC & Quotas](../concepts/12-namespaces-rbac-and-quotas.md).

**Time:** ~45 minutes.

---

## 1. Create the namespace and its guard rails

```powershell
kubectl apply -f labs/01-namespace.yaml
```

```powershell
kubectl get namespace taskflow --show-labels
kubectl describe resourcequota taskflow-quota
kubectl describe limitrange taskflow-defaults
kubectl get serviceaccount -n taskflow
```

### Expected output, and what it means

`kubectl describe resourcequota taskflow-quota` shows the quotas you just created and
their current usage. Notice that `requests.cpu` is `0/4` — you have 4 CPU to spend on
this namespace, and everything you deploy from here on eats into it. Watch that
column as the lab progresses:

```
Resource         Used  Hard
--------         ----  ----
limits.cpu       0     8
limits.memory    0     16Gi
pods             0     30
requests.cpu     0     4
requests.memory  0     8Gi
```

`kubectl get serviceaccount -n taskflow` shows `default` (created automatically with
every namespace) and `taskflow-api`. Check the token setting:

```powershell
kubectl get serviceaccount taskflow-api -n taskflow -o yaml | Select-String automountServiceAccountToken
# -> automountServiceAccountToken: false
```

The Pods in this lab can therefore **not** read Secrets or anything else through the
Kubernetes API. Least privilege applied before anything is deployed.

### Why the namespace has Pod Security labels

```powershell
kubectl get namespace taskflow -o jsonpath='{.metadata.labels}' | ConvertFrom-Json
```

`pod-security.kubernetes.io/enforce: baseline` means the API server will **reject** a
Pod that asks for `privileged: true`, `hostNetwork: true`/`hostPID: true`, or a
`hostPath` volume — the known privilege-escalation paths. `warn: restricted` produces a
warning (not a failure) for anything that would also fail the stricter profile, so you
get a running commentary on what a fully hardened namespace would reject. You will see
those warnings as you apply the manifests in the next few steps; read them, they are
informative rather than noise.

This file also creates a second namespace:

```powershell
kubectl get namespace node-agents --show-labels
```

`node-agents` enforces `privileged`, and it exists because `baseline` forbids
`hostPath` — which a node agent (the DaemonSet in step 08) needs by definition. Pod
Security Admission is enforced per namespace and has no per-Pod exemption, so
privileged infrastructure gets its own namespace. Step 08 proves this experimentally.

---

## 2. Create the configuration

```powershell
kubectl apply -f labs/02-config-secret.yaml
```

```powershell
kubectl get configmap taskflow-config -n taskflow -o yaml
kubectl get secret taskflow-secret -n taskflow
kubectl describe secret taskflow-secret -n taskflow
```

**Look carefully at that last command: the secret's values are not shown.** That is
the one real thing a Secret gives you — it is kept out of casual output and out of
`kubectl describe`. It is *not* encrypted; the very next command proves that.

### Decode it (and understand why that is the point)

```powershell
# one key, decoded
kubectl get secret taskflow-secret -n taskflow -o jsonpath='{.data.DB_PASSWORD}' |
  ForEach-Object { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }

# every key, decoded
kubectl get secret taskflow-secret -n taskflow -o json |
  ConvertFrom-Json |
  Select-Object -ExpandProperty data |
  ForEach-Object { $_.PSObject.Properties } |
  ForEach-Object { "$($_.Name) = $([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_.Value)))" }
```

Anything that can `get` a Secret in this namespace can read every credential in it.
That is why:
- RBAC on `secrets` is effectively "read all credentials" — grant it to nobody by default,
- encryption at rest must be configured on the API server, or an etcd backup contains
  all of it in plaintext,
- production uses an external store (Vault / cloud secret manager) with a syncing operator.

Anyone with cluster access is *already* able to read this one. That is fine — it is a
lab credential. The lesson is that `base64` is encoding, not protection.

---

## 3. Break it — watch the isolation you did *not* get

Namespaces are a naming and policy scope, not a security boundary. Prove it:

```powershell
# a Pod in the DEFAULT namespace, using the fully-qualified DNS name
kubectl run intruder --rm -it --restart=Never --image=busybox:1.36 -- sh
# inside:
nslookup taskflow-config        # no such host? ConfigMaps have no DNS
nslookup kubernetes.default     # but the API server is reachable...
exit
```

Now confirm the reverse: nothing in `taskflow` is isolated from `default` by default.

```powershell
kubectl get networkpolicies -n taskflow
# No resources found  ->  nothing is isolated. ANY Pod in this cluster can reach
# ANY Pod in this cluster. Step 09 is where that changes.
```

No NetworkPolicy means no restriction. Namespaces alone do not isolate traffic.

---

## 4. Break it — exhaust the namespace quota

Create a Deployment that asks for more than the namespace allows:

```powershell
@"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: quota-hog
  namespace: taskflow
spec:
  replicas: 3
  selector:
    matchLabels: { app: quota-hog }
  template:
    metadata:
      labels: { app: quota-hog }
    spec:
      containers:
        - name: hog
          image: busybox:1.36
          command: ["sh","-c","sleep 3600"]
          resources:
            requests: { cpu: "2", memory: "1Gi" }   # 3 x 2 CPU = 6 > the 4 CPU quota
            limits:   { cpu: "2", memory: "1Gi" }
"@ | kubectl apply -f -
```

```powershell
kubectl get deploy,rs,pods -n taskflow
kubectl describe rs -n taskflow -l app=quota-hog | Select-String -Pattern "quota" -Context 0,3
kubectl get events -n taskflow --field-selector reason=FailedCreate
```

Expected: the Deployment exists, the ReplicaSet is created, **two Pods run and the
third is refused** with a message like:

```
Error creating: pods "quota-hog-..." is forbidden:
exceeded quota: taskflow-quota, requested: requests.cpu=2, used: requests.cpu=4, limited: requests.cpu=4
```

Three things to take from this:

1. The API server **rejects** the create — it does not queue it. Quotas are hard limits.
2. The failure surfaces on the **ReplicaSet**, not on the Deployment. When you see a
   Deployment stuck below its replica count, `kubectl describe rs` is where the reason is.
3. Without a quota, that same Deployment would happily consume the whole node.

Clean up before continuing:

```powershell
kubectl delete deployment quota-hog -n taskflow
```

---

## 5. Exercises

1. Add a quota for `count/deployments.apps: "5"`. Then try to create a sixth
   Deployment. What error do you get, and where?
2. Change `limitrange max.memory` to `1Gi` and apply. Does it retroactively affect
   running Pods? (No — limits are set at admission time. Which Pods would now fail to
   be recreated?)
3. Create a second ConfigMap with `--from-literal=EXTRA=1`, and mount it as a volume
   in a throwaway Pod. Confirm each key becomes a file:
   `kubectl run cfg --rm -it --image=busybox:1.36 --restart=Never --overrides='{"spec":{"volumes":[{"name":"c","configMap":{"name":"taskflow-config"}}],"containers":[{"name":"cfg","image":"busybox:1.36","command":["sh"],"stdin":true,"tty":true,"volumeMounts":[{"name":"c","mountPath":"/etc/cfg"}]}]}}' -- ls -l /etc/cfg`
4. Why does `taskflow-config` include `READINESS_REQUIRES_DB`? Which step will flip it,
   and what will happen? (Step 10 — the readiness probe starts failing when the
   database is unreachable, and Pods drop out of the Service.)
5. Grant a Role that can read ConfigMaps but not Secrets in `taskflow`, then prove it
   with `kubectl auth can-i --as=`:

```powershell
@"
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: { name: config-reader, namespace: taskflow }
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get","list","watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: { name: config-reader, namespace: taskflow }
subjects:
  - { kind: ServiceAccount, name: taskflow-api, namespace: taskflow }
roleRef: { kind: Role, name: config-reader, apiGroup: rbac.authorization.k8s.io }
"@ | kubectl apply -f -

kubectl auth can-i get configmaps --as=system:serviceaccount:taskflow:taskflow-api -n taskflow
# yes
kubectl auth can-i get secrets    --as=system:serviceaccount:taskflow:taskflow-api -n taskflow
# no
```

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: the path "labs/01-namespace.yaml" does not exist` | wrong working directory | run commands from the repository root |
| Namespace stuck `Terminating` | a finalizer is waiting on a resource | `kubectl get namespace taskflow -o yaml`, look at `spec.finalizers` and `status.conditions` |
| LimitRange "must be less than or equal to cpu limit" | container limit above `max` | lower the container limit or raise `max` in the LimitRange |
| `exceeded quota: ... requested: requests.cpu` | the behaviour you just demonstrated | raise the quota, or lower the requests |
| ConfigMap edits do not reach running Pods | env vars are fixed at container start | restart the Pods (see step 09 of the concept doc) |

---

## 7. Clean up

Keep everything. `taskflow` namespace, `taskflow-config` and `taskflow-secret` are
needed by every subsequent step. Delete only your experiments:

```powershell
kubectl delete deployment quota-hog -n taskflow --ignore-not-found
```

---

**Checkpoint:**

```powershell
kubectl get ns taskflow
kubectl get cm,secret -n taskflow
kubectl describe resourcequota taskflow-quota
```

**Next:** [Lab 03 — Your first Pod](03-pods-first-contact.md) →
