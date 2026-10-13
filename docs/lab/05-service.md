# Lab 05 — Service

**Goal:** give the API a stable address that survives every Pod being replaced, prove
load balancing works, prove DNS works, and understand why the worker deliberately has
no Service at all.

**Read first:** [Service](../concepts/04-service.md).

**Time:** ~60 minutes.

---

## 1. Create the Services (and two more workloads)

```powershell
kubectl apply -f labs/05-service.yaml
kubectl get svc,pods -n taskflow
```

```powershell
kubectl get deploy -n taskflow
kubectl get pods -n taskflow -o wide
```

You now have three new things: a `taskflow-api` Service, a Redis cache with its own
Service, and a 2-replica worker consuming the queue.

### The asymmetry is the lesson

```powershell
kubectl get svc -n taskflow
```

```
NAME           TYPE        CLUSTER-IP     PORT(S)    AGE
redis          ClusterIP   10.96.x.y      6379/TCP   30s
taskflow-api   ClusterIP   10.96.a.b      80/TCP     30s
```

**There is no `taskflow-worker` Service.** Nothing ever needs to *reach* the worker —
it pulls work from Redis. Adding a Service "just in case" is cargo cult; a Service is
an interface, and interfaces should exist because someone consumes them.

---

## 2. The most important debugging command in this step

```powershell
kubectl describe svc taskflow-api -n taskflow
```

Read the two lines that matter:

```
Selector:          app.kubernetes.io/name=taskflow-api
Endpoints:         10.244.1.5:8080,10.244.2.6:8080,10.244.3.7:8080
```

Now the modern equivalent, which also tells you the readiness state:

```powershell
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=taskflow-api -o yaml
```

**Endpoints are the join between "what I declared" and "what is actually ready".**

- Empty endpoints → the selector matches no Pod, or no Pod is Ready.
- Missing a Pod you expect → that Pod is failing its readiness probe.

Write this down: *"the Service is down" is almost always "the endpoints are empty".*
Everything else (Ingress, Ingress 503s, load balancer health) follows from it.

---

## 3. Prove DNS works

Cluster DNS is what lets `DB_HOST: postgres` in the ConfigMap resolve.

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh
```

Inside that shell:

```sh
nslookup taskflow-api              # short name: same namespace
nslookup taskflow-api.taskflow     # namespace-qualified
nslookup redis
nslookup postgres                  # no such host -> step 07 does not exist yet
cat /etc/resolv.conf               # see the search domains for yourself
exit
```

Note the search list in `resolv.conf`:
`taskflow.svc.cluster.local svc.cluster.local cluster.local`. That is why the short
name works inside the namespace and **would not** from another namespace — use the
FQDN in ConfigMaps so config never silently depends on where the Pod runs.

---

## 4. Prove load balancing

`/api/whoami` returns the Pod name of whichever replica served the request — the
application tells you who answered.

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh
```

```sh
for i in 1 2 3 4 5 6; do wget -qO- http://taskflow-api/api/whoami; echo; done
```

You should see **all three Pod names** appearing. That is round-robin across the
endpoints, and it is real load balancing — done in the client node's kernel by
kube-proxy/eBPF, with no proxy in the middle.

### Now compare with `port-forward`

```powershell
kubectl port-forward -n taskflow svc/taskflow-api 8080:80
# in another terminal, repeatedly:
curl.exe -s http://localhost:8080/api/whoami
```

**The same Pod name every time.** `port-forward` picks one Pod and stays with it, so
it tests the container, not the Service. If you ever claim "the Service works because
`port-forward` worked", that claim is wrong — and it is a very common interview trap.

---

## 5. Prove the queue works

Redis is up even though Postgres is not, so the queue path is testable now.

```powershell
kubectl port-forward -n taskflow svc/taskflow-api 8080:80
```

```powershell
# queue depth should be 0
curl.exe -s http://localhost:8080/api/queue

# push a job
curl.exe -s -X POST http://localhost:8080/api/queue -H "Content-Type: application/json" -d '{\"title\":\"hello queue\"}'

# watch it get consumed (the worker is polling Redis)
curl.exe -s http://localhost:8080/api/queue
```

And in the worker's logs:

```powershell
kubectl logs -n taskflow -l app.kubernetes.io/name=taskflow-worker --tail=20 --prefix
```

You should see `processing job ...` and `job complete ...` lines, plus the
`taskflow:processed` counter incrementing in `/api/queue`. **A real asynchronous
pipeline, running in two Deployments, connected by one ClusterIP Service.**

Note that `/api/tasks` still returns 503 — the API degrades gracefully: the queue
path works, the database path reports precisely why it does not.

---

## 6. Break it

### 6a. A selector typo

```powershell
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"selector":{"app.kubernetes.io/name":"taskflow-apii"}}}'
kubectl describe svc taskflow-api -n taskflow | Select-String -Pattern "Endpoints"
```

```
Endpoints:   <none>
```

The Service is perfectly valid YAML, has a valid ClusterIP, resolves in DNS — and
routes nothing. Now watch what the *caller* sees:

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh -c "wget -qO- -T3 http://taskflow-api/ 2>&1 | head -3"
```

A connection timeout/refusal, with no hint that a label is wrong. **This is why you
check endpoints first.** Fix it:

```powershell
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"selector":{"app.kubernetes.io/name":"taskflow-api"}}}'
kubectl describe svc taskflow-api -n taskflow | Select-String -Pattern "Endpoints"
```

### 6b. Break the readiness probe and watch endpoints shrink

```powershell
kubectl scale deployment taskflow-api -n taskflow --replicas=0
```

```powershell
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=taskflow-api
# the endpoint list is now empty, so the Service has nothing to send traffic to
```

```powershell
kubectl scale deployment taskflow-api -n taskflow --replicas=3
kubectl get endpointslices -n taskflow -l kubernetes.io/service-name=taskflow-api -w
```

The endpoints repopulate **as each Pod passes its readiness probe** — not as each Pod
starts. That gap is exactly where a missing readiness probe causes 502s during every
deploy.

### 6c. `targetPort` vs `port`

```powershell
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":9999,"protocol":"TCP"}]}}'
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -n taskflow -- sh -c "wget -qO- -T3 http://taskflow-api/ 2>&1 | head -2"
```

Endpoints still look healthy (they list Pod IPs), but every connection is refused,
because the Service forwards to a port nothing listens on. **Endpoints healthy +
connection refused = wrong targetPort or the app is not listening on that port.**

```powershell
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":"http","protocol":"TCP"}]}}'
```

---

## 7. Exercises

1. **NodePort.** Patch the Service to `type: NodePort` and try to reach it from your host:
   `kubectl get svc taskflow-api -n taskflow` (look at the `PORT(S)` column), then
   `curl.exe http://$(minikube ip):<nodePort>/api/whoami`. Note that on Windows with the
   `--driver=docker` driver this will very likely **time out**, because the node IP is not
   routable from the host (see [lab 01](01-bootstrap-cluster-and-tools.md#find-the-ingress-address-now-so-step-06-is-painless)).
   Either way, ask yourself why this is not how you want to expose things in production:
   one port per service, no host routing, no TLS termination, and the node IP published to
   clients.
2. **Headless.** Add `clusterIP: None` to a copy of the Service and compare
   `nslookup`. What does DNS return for a normal Service versus a headless one, and
   when is each appropriate?
3. **`sessionAffinity: ClientIP`.** Enable it, then run the load-balancing test again.
   All requests now hit one Pod. When is that the right choice, and why is it usually
   a smell?
4. **Service without Pods but with a different port name.** Try `targetPort: http`
   versus `targetPort: 8080` versus `targetPort: 9999`. Which one fails at apply time,
   and which fails silently at runtime?
5. **`externalTrafficPolicy`.** With a NodePort Service, compare `Cluster` and `Local`
   in terms of the source IP your app sees (`/api/whoami` does not show it, but the
   `X-Forwarded-For` header does once you are behind the Ingress).
6. **A second Deployment sharing a Service.** Give a dummy Deployment the label
   `app.kubernetes.io/name: taskflow-api` and watch its Pods join the endpoint list.
   That is how a Service's selector can accidentally include workloads you did not
   intend — and why labels are treated as an API.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Endpoints: <none>` | selector mismatch, or no Ready Pods | compare `-o yaml` selector with `kubectl get pods --show-labels` |
| Endpoints fine, connection refused | wrong `targetPort`, app bound to `127.0.0.1` | check the port and the app's bind address |
| Intermittent failures during deploys | no readiness probe | add one (step 10) |
| DNS name does not resolve | wrong name, wrong namespace, or CoreDNS down | use the FQDN; `kubectl get pods -n kube-system -l k8s-app=kube-dns` |
| `LoadBalancer` Service stuck `<pending>` | no cloud controller on a local cluster | use `minikube tunnel`, or an Ingress |
| Worker not consuming jobs | Redis unreachable, wrong queue name | `kubectl logs deploy/taskflow-worker -n taskflow`; check `REDIS_QUEUE` |

---

## 9. Clean up

Keep everything. Leave the worker running — you will check its logs again in step 09
when NetworkPolicies start restricting its traffic.

---

**Checkpoint:** you can explain why Pod IPs cannot be used, why endpoints were empty
when the selector was wrong, why the worker has no Service, and why `port-forward`
proving nothing about load balancing.

**Next:** [Lab 06 — Ingress](06-ingress.md) →
