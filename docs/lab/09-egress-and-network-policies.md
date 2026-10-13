# Lab 09 — Egress & NetworkPolicy

**Goal:** take the namespace from "every Pod can reach everything" to a documented,
enforceable posture — and feel how instantly default-deny breaks everything when one
rule is missing.

**Read first:** [Egress (and NetworkPolicy)](../concepts/06-egress.md).

**Time:** ~90 minutes. Budget the time; this is the step where most people learn the
most.

---

## 1. Check that your CNI actually enforces policies

This matters more than anything else on this page. **A NetworkPolicy is a no-op
unless the CNI implements it.** Flannel does not; Calico, Cilium, Antrea and kindnet do.

```powershell
kubectl get pods -n kube-system
```

Look for `calico`, `cilium`, `antrea` or `kindnet`. Then prove enforcement rather
than trusting it — create two Pods, confirm they can talk, apply default-deny, confirm
they cannot:

```powershell
kubectl create namespace nptest
kubectl run npserver --image=nginx:1.27-alpine --port=80 -n nptest
kubectl run npclient --image=busybox:1.36 --restart=Never -n nptest --command -- sh -c "sleep 3600"
kubectl wait --for=condition=ready pod/npserver pod/npclient -n nptest --timeout=180s

$ip = kubectl get pod npserver -n nptest -o jsonpath='{.status.podIP}'
"--- BEFORE the policy (expect HTML) ---"
kubectl exec npclient -n nptest -- wget -qO- -T3 "http://$ip"

@'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: deny-all-ingress, namespace: nptest }
spec:
  podSelector: {}
  policyTypes: [Ingress]
'@ | kubectl apply -f -

Start-Sleep -Seconds 3
"--- AFTER the policy (expect a timeout) ---"
kubectl exec npclient -n nptest -- wget -qO- -T5 "http://$ip"
kubectl delete namespace nptest
```

If the second request **still succeeds**, your CNI is ignoring policies. Stop here and
recreate the cluster with a policy-capable CNI before continuing —
`minikube start ... --cni=calico`, for example. Continuing would give you a false
sense of security, which is worse than having no policy at all.

> On this lab's minikube cluster the CNI is **kindnet**, which *does* enforce
> NetworkPolicy — verified: the request changed from returning HTML to timing out.
> A **timeout** is the signature of a dropped packet; a **connection refused** would
> mean you reached something. Learn to tell those apart; they point at different layers.

---

## 2. Apply the policies

```powershell
kubectl apply -f labs/09-networkpolicy.yaml
kubectl get networkpolicies -n taskflow
```

Nine policies. Before reading further, re-read
[the concept lesson §4](../concepts/06-egress.md#4-the-patterns-in-the-order-you-should-apply-them)
— the file is written in the order the policies should be reasoned about, not the order
`kubectl get` returns them.

```
default-deny-all                  <podSelector: {}>            Ingress, Egress
allow-dns-egress                  <podSelector: {}>            Egress
allow-probes-from-node            backend, cache               Ingress
allow-ingress-from-controller     taskflow-api                 Ingress
allow-postgres-ingress            postgres                     Ingress
allow-redis-ingress               redis                        Ingress
allow-api-egress                  taskflow-api                 Egress
allow-worker-egress               taskflow-worker              Egress
allow-migrate-egress              taskflow-migrate             Egress
```

---

## 3. Prove that everything still works

Policies are additive, so the app should be completely unaffected. Verify, from the
outside and from the inside:

```powershell
# north-south: through the Ingress
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/tasks
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/whoami
```

```powershell
# east-west: API -> Postgres
curl.exe -s -X POST -H "Host: taskflow.local" -H "Content-Type: application/json" `
  -d '{\"title\":\"network policy is in effect\"}' http://localhost:8080/api/tasks

# egress: API -> the external HTTPS endpoint
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/egress
```

`/api/egress` should return something like
`{"url":"https://api.github.com/zen","status":200,"body":"...","elapsed_ms":180}` —
the API reaching the internet, through the one rule that allows it.

Now confirm the **worker** is explicitly *not* allowed out:

```powershell
kubectl exec -n taskflow deploy/taskflow-worker -- python -c "import requests; requests.get('https://api.github.com/zen', timeout=5)"
```

You should get a **timeout / connection error** after ~5 seconds. Nothing is wrong with
the worker or with Python — a policy is dropping the packets, and the error message
will not say so. This is the single most confusing thing about NetworkPolicy
debugging: **the failure is silent by design.**

---

## 4. Break it — remove the DNS rule and watch everything fail

This is the classic. Comment out (or delete) the `allow-dns-egress` policy:

```powershell
kubectl delete networkpolicy allow-dns-egress -n taskflow
```

Then:

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh -c "nslookup taskflow-api; nslookup postgres"
```

```
nslookup: can't resolve 'taskflow-api'
```

Every name in the cluster stops resolving — which means the API cannot find Postgres,
the worker cannot find Redis, and nothing works. And the error you would see in an app
log is something like `could not translate host name "postgres"`, which sounds like a
DNS server problem, or a typo, or anything except a NetworkPolicy you applied.

**This is why the DNS allowance goes in first, always.** Put it back:

```powershell
kubectl apply -f labs/09-networkpolicy.yaml
```

---

## 5. Break it — the kubelet's probes are not Pods

Delete the probe allowance and watch healthy Pods start dying:

```powershell
kubectl delete networkpolicy allow-probes-from-node -n taskflow
kubectl get pods -n taskflow -w
```

Within a minute or so you should see the API Pods go through:

```
taskflow-api-...   0/1   Running   1   (readiness probe failed)
```

Then, if a liveness probe fails too, restarts begin. **Your application is perfectly
healthy.** What happened is:

- `readinessProbe` and `livenessProbe` `httpGet` calls are made by the **kubelet**, from
  the **Node's** network namespace — so their source IP is the Node, not a Pod.
- No `podSelector` can ever allow them; only an `ipBlock` can.
- `exec` probes are unaffected (they use the kubelet's CRI stream, not the network),
  which is exactly why Postgres (`pg_isready`) sailed through this.

```powershell
kubectl describe pod -n taskflow -l app.kubernetes.io/name=taskflow-api | Select-String -Pattern "Unhealthy|probe failed" -Context 0,1
```

Restore, and confirm the Pods recover on their own:

```powershell
kubectl apply -f labs/09-networkpolicy.yaml
kubectl get pods -n taskflow -w
```

If your Pods *did not* break, the kubelet's probe traffic on your platform may not be
subject to the policy at all. Check what the source IP actually is and adjust — and
note that this is a genuine platform difference, not a mistake on your part. It is
worth knowing which one you are on.

---

## 6. Prove the deny is real (and where it stops)

```powershell
kubectl run netshoot --rm -it --image=nicolaka/netshoot --restart=Never -n taskflow -- bash
```

Inside that shell:

```sh
# DNS: allowed for every Pod in the namespace
nslookup kubernetes.default.svc.cluster.local        # works

# data tier: NOT allowed, because this Pod has no egress rule
nc -zv postgres 5432                                 # hangs -> dropped
nc -zv redis 6379                                    # hangs -> dropped

# internet: NOT allowed
curl -sS -m 5 https://api.github.com/zen             # times out

# but nothing stops it talking to a Pod that allows ingress from anywhere...
exit
```

Note that the throwaway Pod is itself covered by `default-deny-all`, because
`podSelector: {}` matches **every** Pod in the namespace **including ones created
later**. A deny rule cannot be bypassed by creating a new Pod — which is precisely the
property you want.

---

## 7. Understand the frame: what egress actually looks like from outside

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh
```

```sh
wget -qO- http://taskflow-api/api/config
exit
```

Now think about the other direction. When your API calls `https://api.github.com`, what
source IP does GitHub see?

```powershell
kubectl get nodes -o wide
kubectl run tmp --rm -it --image=nicolaka/netshoot --restart=Never -n taskflow -- sh -c "curl -s https://api.ipify.org; echo"
```

The answer is **not the Pod IP** (`10.244.x.x` is private and not routable). It is the
Node's IP locally, or a NAT gateway's static IP in a cloud cluster — because the packet
is **SNAT'd** on the way out.

Three consequences worth writing down:

1. A vendor asking "which IP should we allowlist?" gets a NAT gateway address, never a
   Pod CIDR.
2. You cannot allowlist *to* a Pod, and you cannot allowlist *by* a Pod.
3. NetworkPolicy selects by IP only. You cannot write "allow `api.github.com`" — that
   needs Cilium `toFQDNs`, an HTTP forward proxy, or a mesh `ServiceEntry`.

If you ever need a *stable, dedicated* egress IP (a partner who allowlists by IP, an
audit requirement), that is a NAT gateway with a static IP, a Cilium Egress Gateway, or
an Istio egress gateway — and it is a platform decision, not an application one.

---

## 8. Exercises

1. **Add a rule, break nothing.** Allow the worker to reach exactly one external host on
   443 (use `ipBlock` with an `except` list for the private ranges, as the API rule
   does). Then verify with `kubectl exec deploy/taskflow-worker -- python -c ...`.
2. **Postgres egress lockdown.** Postgres currently has *no* egress rules, so it can
   reach nothing at all. Is that right for your workload? (Usually yes — but it will
   break a logical-replication or webhook-based backup strategy, and you would find out
   in production unless you test it.)
3. **Namespace-based rule.** Allow anything in a namespace labelled
   `purpose: monitoring` to scrape the API on 8080. Create the namespace with the label
   and test. This is how you would let Prometheus in without knowing its Pod labels.
4. **The AND/OR trap.** Write the DNS rule with `namespaceSelector` and `podSelector` as
   two separate `to` items instead of one, then explain the difference in one sentence.
   (One is "kube-dns Pods in kube-system"; the other is "anything in kube-system OR any
   kube-dns Pod anywhere".)
5. **Egress is expensive.** In a cloud cluster, NAT gateway data processing is billed per
   GB. Which of your workloads would talk to the internet most, and what is the
   architectural fix? (Move the call to a service with caching, or use a VPC endpoint.)
6. **Audit.** `kubectl get networkpolicy -n taskflow -o yaml` and count the rules. Now
   write the one-paragraph answer to a security reviewer asking "what can leave this
   namespace?" — the fact that this is answerable in a paragraph is the point of the
   exercise.
7. **A policy that proves intent.**
   `kubectl delete networkpolicy default-deny-all -n taskflow` and re-run the `netshoot`
   test. Everything is allowed again, and your documented posture disappears with one
   delete. Store policies in Git alongside the workloads — they are not optional extras.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Policy applied, nothing is blocked | CNI does not enforce policies | recreate the cluster with Calico/Cilium |
| `no such host` everywhere | DNS egress rule missing | re-add `allow-dns-egress` |
| Healthy Pods go `0/1`, "probe failed" | kubelet probes blocked | allow the Node CIDR to the probe port |
| App cannot reach the DB but the DB is fine | the *egress* rule is missing (not just ingress) | both sides must allow |
| `postgres` unreachable from a new Job | the Job's labels are not covered by an egress rule | this is the step 12 exercise |
| Connections hang instead of failing fast | that is what "dropped by policy" looks like | add `-m 5`/`-T 5` timeouts to your tests |
| Works from one Pod, not another | policies are per-Pod-selector | `kubectl get netpol -o yaml` and check which Pods are selected |

---

## 10. Clean up

Keep the policies — they are part of the target architecture. Make sure the temporary
namespaces are gone:

```powershell
kubectl get namespaces
kubectl get networkpolicies -n taskflow
```

---

**Checkpoint:** you can explain why there is no "deny" rule, why DNS must be allowed
first, why kubelet probes need an `ipBlock`, and what the outside world sees as the
source IP of your egress traffic.

**Next:** [Lab 10 — Probes, HPA, PDB, rollouts](10-probes-scaling-and-rollouts.md) →
