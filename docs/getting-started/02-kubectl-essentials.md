# Getting started 02 — kubectl essentials

`kubectl` is a thin REST client for the Kubernetes API server. Everything you do in
Kubernetes is one of five verbs against an object. Learn the shape once and you can drive
any object in the system.

```
kubectl <VERB> <KIND> <NAME> [flags]
        └─ get | describe | logs | exec | apply | delete | edit | scale | rollout
```

Short version of what you need for the lab: [kubectl cheatsheet](../reference/kubectl-cheatsheet.md).

---

## 1. The two styles: imperative vs declarative

**Imperative** — you tell Kubernetes what to do, right now. Great for throwaway
experiments and for *generating* YAML.

```powershell
kubectl create deployment web --image=nginx:1.27
kubectl expose deployment web --port=80
kubectl scale deployment web --replicas=3
kubectl delete deployment web
```

**Declarative** — you describe the *desired end state* in a file and let Kubernetes work
out how to get there. This is how real teams work: files live in Git, and the cluster is
reconciled towards them.

```powershell
kubectl apply -f labs/04-deployment.yaml
kubectl apply -f labs/            # the whole directory
```

`apply` is **idempotent**: run it once or fifty times, the result is the same. That is the
whole point. It is also why the lab uses `apply` for everything after step 03.

Useful flag: **generate YAML instead of guessing**, then edit and keep it.

```powershell
kubectl create deployment web --image=nginx:1.27 --dry-run=client -o yaml > web.yaml
```

---

## 2. Seeing what exists

```powershell
kubectl get pods                      # current namespace
kubectl get pods -A                   # all namespaces
kubectl get pods -n taskflow          # a specific namespace
kubectl get pods -o wide              # + IP, node, image
kubectl get pod taskflow-api-abc -o yaml   # the full object as stored
kubectl get all -n taskflow           # the common kinds at once
kubectl get pods --show-labels        # see the labels selectors match on
kubectl get pods -l app.kubernetes.io/name=taskflow-api    # filter by label
kubectl get pods --watch              # live updates while you break things
```

Output formats worth knowing:

| Flag | Use |
|---|---|
| `-o wide` | extra columns (IP, node, image) |
| `-o yaml` / `-o json` | the raw object — best for debugging |
| `-o name` | just `pod/foo` — handy for piping into other commands |
| `-o jsonpath='{.status.podIP}'` | extract one field |
| `-o custom-columns=...` | build your own table |

> Tip: `kubectl get pods -o yaml | Select-String -Pattern "reason" -Context 2` is a fast
> way to find why a Pod is unhappy on Windows.

---

## 3. The three debugging commands

These three, in this order, solve about 90% of Kubernetes problems.

### `describe` — the object's story, including events

```powershell
kubectl describe pod taskflow-api-7d9f8b6c4-abcde -n taskflow
```

Read the bottom first. The **Events** section records scheduling failures, image pull
errors, probe failures, OOM kills. `describe` is where you find *why*, not just *what*.

### `logs` — what the process said

```powershell
kubectl logs taskflow-api-7d9f8b6c4-abcde -n taskflow
kubectl logs <pod> -c <container>          # multi-container Pod
kubectl logs <pod> --previous              # the container that just crashed
kubectl logs -l app.kubernetes.io/name=taskflow-api --tail=50 --prefix
kubectl logs -f deploy/taskflow-api        # stream from a Deployment
```

`--previous` is the one people forget. `CrashLoopBackOff` means the *current* container is
restarting, so the interesting logs are from the previous run.

### `exec` — get inside

```powershell
kubectl exec -it <pod> -n taskflow -- sh
kubectl exec -it <pod> -- env              # see injected ConfigMap/Secret values
kubectl exec -it <pod> -- printenv DB_HOST
```

From another Pod's shell, DNS resolution is the thing to verify:

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -- sh
# inside:
nslookup taskflow-api
wget -qO- http://taskflow-api/
```

`kubectl run ... --rm -it --restart=Never` is the single most useful debugging trick in
this whole document. Keep it in your notes.

---

## 4. Changing things

```powershell
kubectl apply -f labs/05-service.yaml       # create or update (preferred)
kubectl edit deployment taskflow-api -n taskflow   # live edit — do NOT do this in prod
kubectl scale deployment taskflow-api -n taskflow --replicas=5
kubectl set image deployment/taskflow-api api=taskflow-api:1.1.0 -n taskflow
kubectl delete -f labs/05-service.yaml
kubectl delete pod <name> --grace-period=0 --force   # last resort
```

### Rollouts

```powershell
kubectl rollout status deployment/taskflow-api -n taskflow
kubectl rollout history deployment/taskflow-api -n taskflow
kubectl rollout undo deployment/taskflow-api -n taskflow
kubectl rollout restart deployment/taskflow-api -n taskflow
```

### Safe previews before you touch the cluster

```powershell
kubectl apply -f labs/ --dry-run=client      # syntax/parse check only, offline
kubectl apply -f labs/ --dry-run=server      # real API validation + admission, no change
kubectl diff -f labs/04-deployment.yaml      # what would actually change
```

`--dry-run=server` is the one that catches things client-side validation cannot: missing
CRDs, invalid field combinations, admission webhook rejections.

---

## 5. Reading the docs from the terminal

No internet needed, and always the exact version your cluster runs:

```powershell
kubectl explain pod
kubectl explain pod.spec
kubectl explain deployment.spec.strategy.rollingUpdate
kubectl explain ingress.spec.rules.http.paths.backend
kubectl api-resources                                  # every kind + its short name
kubectl api-versions                                   # available API groups
```

`kubectl explain` understands the field path, which makes it the fastest way to find a
field name you half-remember.

---

## 6. Namespaces, contexts and the kubeconfig

```powershell
kubectl config get-contexts
kubectl config use-context taskflow
kubectl config view --minify                      # where is my config pointing?
kubectl config set-context --current --namespace=taskflow   # stop typing -n
```

Once you set the default namespace, `kubectl get pods` is implicitly scoped to
`taskflow`. That is convenient and also the reason people occasionally apply things to
the wrong cluster — always check `kubectl config current-context` before a big change.

Namespaces are **not** a security boundary on their own; they are a naming/organisation
scope plus a policy attachment point (quotas, RBAC, NetworkPolicy). See
[concepts/12](../concepts/12-namespaces-rbac-and-quotas.md).

---

## 7. Events: the cluster's own log stream

When `describe` is not enough, look at the whole cluster's events:

```powershell
kubectl get events -n taskflow --sort-by=.lastTimestamp
kubectl get events -A --field-selector type=Warning
```

Almost every "why is Kubernetes doing this?" question is answered here.

---

## 8. Habits to build now

1. **Always pass `-n <namespace>`** (or set the context default). Guessing wrong wastes
   real time.
2. **`describe` before `delete`.** Deleting a broken Pod with a Deployment behind it just
   recreates it and you lose the evidence.
3. **Label everything.** Selectors are how Services, Deployments, NetworkPolicies and HPA
   find Pods. Bad labels are the number one cause of "my Service has no endpoints".
4. **`--dry-run=server` before `apply` on anything shared.**
5. **Prefer `kubectl run tmp --rm -it --image=busybox --restart=Never -- sh`** for
   network debugging instead of guessing.
6. **Use `-o wide`** so you can *see* the Pod IP and node — the mental model gets much
   clearer when IPs are visible.

---

**Next:** [the mental model](03-mental-model.md) →
