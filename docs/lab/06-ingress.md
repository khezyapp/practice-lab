# Lab 06 — Ingress

**Goal:** reach TaskFlow at a real hostname through one front door, understand the
difference between an `Ingress` and an Ingress *controller*, and read the difference
between 404, 502 and 503 from the proxy's point of view.

**Read first:** [Ingress](../concepts/05-ingress.md).

**Time:** ~60 minutes.

---

## 1. Confirm the controller exists

An `Ingress` object is **only a routing rule**. Without a controller, nothing reads
it and nothing happens — silently.

```powershell
kubectl get ingressclass
kubectl get pods  -n ingress-nginx
kubectl get svc   -n ingress-nginx
```

If `ingressclass` is missing, go back to [step 01](01-bootstrap-cluster-and-tools.md#3-turn-on-the-two-add-ons-the-lab-needs).

Record how the controller is exposed — it decides how you reach it:

```powershell
kubectl get svc -n ingress-nginx -o wide
kubectl get ingress -A                  # ADDRESS is empty until a controller exists
minikube ip --profile taskflow
```

| Controller Service type | How you reach it | Notes |
|---|---|---|
| `LoadBalancer` | the `EXTERNAL-IP` | cloud clusters |
| `NodePort` | `<node-ip>:<nodePort>` | check the `PORT(S)` column |
| bound to the node's port 80 | `<node-ip>` | what the minikube add-on usually does |
| **port-forward** | `localhost:8080` | **works everywhere — this is the lab's method** |

**Verify rather than assume.** On Windows with the `--driver=docker` driver the node IP is
usually *not* routable from your host, so `<node-ip>` will hang:

```powershell
curl.exe --max-time 5 -H "Host: taskflow.local" http://192.168.49.2/
# exit 28 = not reachable from here. Use the port-forward below.
```

---

## 2. Create the Ingress

```powershell
kubectl apply -f labs/06-ingress.yaml
kubectl get ingress -n taskflow
kubectl describe ingress taskflow -n taskflow
```

`kubectl describe` is the most useful of the three — it renders the rules as a table
and shows the resolved backend Service and port:

```
Rules:
  Host                Path  Backends
  ----                ----  --------
  taskflow.local
                      /api   taskflow-api:80 (10.244.1.5:8080,...)
                      /     taskflow-api:80
  api.taskflow.local
                      /     taskflow-api:80
```

If the `ADDRESS` column is empty and the backends show `(no endpoints)`, you have
either no controller or no Ready Pods behind the Service. `kubectl describe` tells you
which.

---

## 3. Route some traffic through it

### The `Host` header approach (works everywhere)

```powershell
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
```

In another terminal, use `curl.exe` (PowerShell 5.1's `curl` is an alias for
`Invoke-WebRequest`, which takes different flags):

```powershell
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/whoami
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/config
curl.exe -s -o NUL -w "%{http_code}`n" -H "Host: taskflow.local" http://localhost:8080/healthz
```

You are now going through the **full production path**:

```
you → ingress-nginx pod → Service taskflow-api → a Ready Pod → container
```

### Or with a real hostname

As Administrator, add to `C:\Windows\System32\drivers\etc\hosts`:

```
127.0.0.1  taskflow.local api.taskflow.local
```

Then, with the port-forward running, browse to `http://taskflow.local:8080` — the name
resolves to localhost and the `Host` header is the real hostname, which is exactly what
the Ingress matches on. (If your node IP *is* reachable, you can point the entry at it and
drop the port instead.)

### See the load balancing through the Ingress

```powershell
for ($i=1; $i -le 6; $i++) { curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/whoami; "" }
```

Different Pod names again — the controller is talking to the **Service**, so it gets
the Service's load balancing for free. This is why the Ingress backend points at a
Service and not at Pods.

### See the headers the proxy adds

```powershell
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller --tail=20
```

Each request produces a line with the resolved upstream and timings. Add a route that
echoes headers (`/api/config` includes the Pod and node) and note how the proxy
conveys the original client information via `X-Forwarded-For` / `X-Forwarded-Proto` —
this is how your app learns the real client IP and scheme.

---

## 4. Read the failure codes

These four codes are the language the Ingress speaks. Learn them once and you will
diagnose most ingress problems in seconds.

### 404 — no rule matched

```powershell
curl.exe -i -s -H "Host: nope.example.com" http://localhost:8080/api/whoami | Select-Object -First 5
curl.exe -i -s -H "Host: taskflow.local"   http://localhost:8080/does-not-exist | Select-Object -First 5
```

Both 404: the first has no matching **host**, the second no matching **path**. The
Ingress never forwards anything; this is the controller's own 404, not your app's.

Also note that a request without an explicit `Host` (`curl.exe http://localhost:8080/`)
sends `Host: localhost`, which matches no rule — the single most common "my Ingress
does not work" cause.

### 503 — rule matched, but no healthy backend

```powershell
kubectl scale deployment taskflow-api -n taskflow --replicas=0
curl.exe -i -s -H "Host: taskflow.local" http://localhost:8080/api/whoami | Select-Object -First 5
kubectl scale deployment taskflow-api -n taskflow --replicas=3
```

`503 Service Temporarily Unavailable` means the Ingress found your Service but the
Service had **no Ready endpoints**. In the real world this is nearly always a failing
readiness probe — and the fix is in the Pods, not in the Ingress.

### 502 — reached the backend, connection failed

```powershell
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":9999,"protocol":"TCP"}]}}'
curl.exe -i -s -H "Host: taskflow.local" http://localhost:8080/api/whoami | Select-Object -First 5
kubectl patch svc taskflow-api -n taskflow --type merge -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":"http","protocol":"TCP"}]}}'
```

`502 Bad Gateway`: endpoints existed, the proxy connected, and the connection failed —
wrong `targetPort`, or the app died between the endpoint update and the request.

### 504 — the backend was too slow

```powershell
kubectl patch deployment taskflow-api -n taskflow --type json `
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/slow-does-not-exist"}]'
kubectl rollout undo deployment/taskflow-api -n taskflow
```

`504` = the proxy timed out. Tune with
`nginx.ingress.kubernetes.io/proxy-read-timeout` — the annotation is already in the
manifest, commented into a sane value.

### Why this matters

| Code | The Ingress | Your app | Where to look |
|---|---|---|---|
| 404 | never matched a rule | never saw it | `kubectl describe ingress` — host, path, pathType |
| 503 | matched, no endpoints | never saw it | `kubectl get endpointslices` |
| 502 | matched, connection failed | maybe saw it | `targetPort`, Pod restarts, app crash |
| 504 | matched, timed out | saw it, was slow | app latency, timeouts |

---

## 5. Exercises

1. **Path rewriting.** Change the backend path so the app is reached at `/` while
   clients call `/api/tasks`. Use
   `nginx.ingress.kubernetes.io/rewrite-target: /$2` with
   `path: /api(/|$)(.*)` and `pathType: ImplementationSpecific`. Explain why the plain
   `rewrite-target: /` breaks multi-path Ingresses. Now revert — the app expects `/api/...`.
2. **Two services, one host.** Give the worker... nothing to serve. Instead, create a
   second Service pointing at Redis's Pods on port 6379 with a `http` port name, and
   add a path `/-/redis` to the Ingress. What error do you get? (An Ingress is HTTP
   only — Redis is not HTTP. This is why raw TCP needs a `LoadBalancer` Service, an
   ingress-nginx TCP configmap, or Gateway API.)
3. **`defaultBackend`.** Uncomment it in the manifest and re-test the
   `nope.example.com` request. It goes from 404 to whatever your default backend
   returns. When is that useful, and when does it hide problems?
4. **Host-based routing.** Point `api.taskflow.local` at a *different* Service (create
   a second Service with a wrong selector) and confirm only that hostname breaks. This
   is how you would run `admin.` and `api.` from a single IP and certificate.
5. **Find the generated config.**
   `kubectl exec -n ingress-nginx deploy/ingress-nginx-controller -- cat /etc/nginx/nginx.conf`
   and grep for `taskflow`. You are reading the nginx config the controller generated
   from your `Ingress` object — the fastest way to see what the controller *thinks*
   you asked for.
6. **TLS, on paper.** Write the `spec.tls` block and the
   `kubectl create secret tls` command you would use (see
   [the concept lesson](../concepts/05-ingress.md#4-tls)). You need a certificate +
   key pair; in production cert-manager creates and rotates it from a single
   annotation. What breaks first if the Secret is missing? (The controller serves its
   default self-signed certificate, or refuses the handshake.)
7. **Ingress vs Gateway API.** Read
   `kubectl api-resources | Select-String gateway`. If your cluster has the Gateway
   API CRDs, note how `GatewayClass`/`Gateway`/`HTTPRoute` map onto the Ingress
   concepts you just used.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No `ADDRESS` on the Ingress | no controller, or it is not ready | `kubectl get pods -n ingress-nginx` |
| 404 for everything | no rule matched | check the `Host` header, and `pathType` |
| 404 always, even for the right host | wrong `ingressClassName` | `kubectl get ingressclass`; the class must match |
| 503 | empty endpoints | `kubectl get endpointslices -n taskflow` |
| 502 | wrong `targetPort`, or the Pod restarted | check endpoints and `kubectl get pods` |
| Works with `-H "Host:"` but not in a browser | hosts file not updated / wrong IP | fix the hosts entry |
| Connection refused entirely | controller not exposed where you think | `kubectl get svc -n ingress-nginx`, use the port-forward fallback |
| Changes to the Ingress take a few seconds | the controller reloads nginx | wait, then re-check the generated config |

---

## 7. Clean up

Keep the Ingress. It is part of the target architecture, and step 09's NetworkPolicy
depends on the controller being able to reach the API.

If you made experimental changes, restore the file version:

```powershell
kubectl apply -f labs/06-ingress.yaml
kubectl get ingress -n taskflow
```

---

**Checkpoint:** you can explain the difference between an `Ingress` and an Ingress
controller, route by host and path, and tell the difference between 404 / 502 / 503 /
504 by looking at the endpoints.

**Next:** [Lab 07 — StatefulSet + Postgres](07-statefulset-postgres.md) →
