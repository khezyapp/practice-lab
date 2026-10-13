# Getting started 01 — Run a cluster on your laptop

**Goal:** a working single-node Kubernetes cluster, a `kubectl` that talks to it, an
Ingress controller, and metrics-server — all in under 20 minutes.

You have two good options on this machine. Pick one, do not do both at the same time
(two clusters on one machine is a great way to waste an afternoon debugging the wrong one).

---

## Option A — minikube (recommended for this lab)

Why: disposable profiles, one-command add-ons, easy to throw away and start over, and it
supports multi-node later (`--nodes=3`) which is useful for the DaemonSet step.

### A1. Start the cluster

Give it a bit more than the defaults, because the lab runs Postgres, Redis, the API,
the worker and an ingress controller.

```powershell
minikube start --profile taskflow --cpus=4 --memory=5120 --disk-size=20g --driver=docker
kubectl config use-context taskflow
kubectl get nodes -o wide
```

Expected: one node named `taskflow` with `STATUS  Ready` and `ROLES  control-plane`.

> **Size the cluster to fit Docker Desktop.** `--memory=5120` (5 GiB) works on this
> machine because Docker Desktop has about 7.6 GiB to hand out, and 4 CPUs out of 8.
> Check yours with `docker info --format '{{.MemTotal}}'` — if you ask minikube for
> more memory than the Docker engine has, it fails to start with an error that does
> not obviously say so. Running the lab comfortably needs at least 4 GiB.

> `--driver=docker` reuses the Docker Desktop engine you already have running, so no
> extra VM software is needed.

### A2. Turn on the two add-ons the lab needs

```powershell
minikube addons enable ingress          --profile taskflow
minikube addons enable metrics-server   --profile taskflow
kubectl get pods -n ingress-nginx
kubectl top nodes
```

- **ingress-nginx** = the actual reverse proxy that reads `Ingress` objects.
  Without it, `Ingress` resources do nothing at all (they are just inert config).
- **metrics-server** = feeds `kubectl top` and the Horizontal Pod Autoscaler.
  `kubectl top nodes` will error until it is ready — give it a minute.

### A3. Find out how to reach the Ingress — then test it, do not assume

The add-on's exposure varies by minikube version and driver, so **look at it instead of
guessing**:

```powershell
kubectl get svc -n ingress-nginx
minikube ip --profile taskflow     # e.g. 192.168.49.2
```

The `ingress-nginx` add-on typically binds the controller to the Node's port 80, so the
Ingress `ADDRESS` becomes the node IP:

```powershell
kubectl get ingress -A             # ADDRESS: 192.168.49.2
```

**But on Windows with the `--driver=docker` driver, that IP is usually NOT reachable from
your host machine.** It lives inside Docker's network, so `curl http://192.168.49.2/...`
hangs until it times out. Verify before you build on the assumption:

```powershell
curl.exe --max-time 5 -H "Host: taskflow.local" http://192.168.49.2/api/tasks
# exit code 28 (timeout) = not routable from here. Use A4 instead.
```

Three ways to get host access, in order of how much I recommend them for this lab:

| Approach | Command | Cost |
|---|---|---|
| **Port-forward (A4)** | `kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80` | none; works on every driver and OS. This is what the lab uses |
| `minikube tunnel` | `minikube tunnel --profile taskflow`, then browse port 80 | needs an **Administrator** terminal, and it keeps running |
| Node IP directly | use `minikube ip` | only if the test above succeeds |

### A4. Port-forward the Ingress controller (the reliable method)

This works identically on minikube, kind and Docker Desktop, on any OS, with no
privileges. Pass the `Host` header yourself and the Ingress rules behave exactly as they
would in production:

```powershell
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
```

```powershell
# in another terminal:
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/tasks
```

> Use `curl.exe`, not bare `curl`: in Windows PowerShell 5.1, `curl` is an alias for
> `Invoke-WebRequest` and the flags behave differently.

Two request/response cycles are all it takes to prove the Ingress is working: a matching
`Host` returns your app's response, an unmatched `Host` returns the controller's own 404.

### A5. Optional: a real hostname in the browser

With the port-forward running, add this to your hosts file so the *name* resolves too.
This requires an **Administrator** terminal — run it yourself:

```powershell
notepad C:\Windows\System32\drivers\etc\hosts     # opened as Administrator
```

```
127.0.0.1  taskflow.local api.taskflow.local
```

Then `http://taskflow.local:8080` works in a browser — the port-forward listens on
localhost, and the `Host` header is now the real hostname.

If your node IP *is* reachable (test A3), you can instead point the entry at it
(`192.168.49.2 taskflow.local`) and browse `http://taskflow.local` with no port suffix,
but `127.0.0.1` plus the port-forward is the more reliable choice on Windows.

---

## Option B — Docker Desktop Kubernetes

Why: zero extra setup, the same Docker engine builds your images and runs them, and
locally-built images are visible to the cluster without any load step.

1. Open **Docker Desktop → Settings → Kubernetes → Enable Kubernetes → Apply & Restart**.
2. Switch context and verify:

```powershell
kubectl config use-context docker-desktop
kubectl get nodes -o wide
```

3. Install the Ingress controller yourself (Docker Desktop has no add-ons):

```powershell
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
kubectl get pods -n ingress-nginx --watch
```

Pin a released version (for example `controller-v1.12.0`) instead of `main` in anything
resembling a real environment; `main` is only convenient here. Check the current version
at <https://github.com/kubernetes/ingress-nginx/releases>.

4. Install metrics-server for HPA:

```powershell
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

Docker Desktop's kubelet uses a self-signed certificate, so metrics-server usually needs
this patch to stop crash-looping:

```powershell
kubectl patch deployment metrics-server -n kube-system --type=json `
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
kubectl rollout status deployment/metrics-server -n kube-system
kubectl top nodes
```

5. Ingress is reachable on `localhost:80` directly, so the hosts entry is:

```
127.0.0.1  taskflow.local
```

> **Important:** Docker Desktop's Kubernetes is single-node and the node's port 80 is
> bound by the ingress controller. There is no `minikube ip`, and no `--nodes=3`, so the
> DaemonSet step will simply show one Pod. That is correct, not a bug.

---

## Verify the environment

Run all of these. Every one must succeed before you start the lab.

```powershell
kubectl version                                  # client + server versions printed
kubectl get nodes                                # at least one node, Ready
kubectl get pods -A                              # control-plane + add-on pods Running
kubectl get storageclass                         # exactly one marked (default)
kubectl get ingressclass                         # nginx present
kubectl top nodes                                # metrics flowing (may need 1-2 min)
kubectl auth can-i create deployments            # yes
```

`kubectl get storageclass` matters for lab step 07: the Postgres PVC uses the default
StorageClass, which is why we never hard-code one in the manifests.

---

## Cluster hygiene

| Task | Command |
|---|---|
| Stop the cluster, keep data | `minikube stop --profile taskflow` |
| Delete the cluster entirely | `minikube delete --profile taskflow` |
| See all minikube clusters | `minikube profile list` |
| Switch between clusters | `kubectl config use-context <name>` |
| Free disk space fast | `minikube delete --all --purge` (destructive) |

Docker Desktop: toggle Kubernetes off in Settings, or use **Troubleshoot → Clean / Purge
data**. Never do that while you expect your PVC data to survive.

---

## Troubleshooting the setup

| Symptom | Cause | Fix |
|---|---|---|
| `The connection to the server localhost:8080 was refused` | No current context | `kubectl config use-context taskflow` |
| Pods stuck in `Pending` | Not enough CPU/memory in the node | Delete and recreate minikube with more `--memory`/`--cpus` |
| `Ingress` has no `ADDRESS` | No Ingress controller installed | Do A2 or B3; check `kubectl get pods -n ingress-nginx` |
| `kubectl top` → `Metrics API not available` | metrics-server not ready or TLS error | Wait 2 minutes; then check `kubectl logs -n kube-system deploy/metrics-server` |
| PVC stuck in `Pending` | No default StorageClass | `kubectl get storageclass`; enable one or set `storageClassName` explicitly |
| `curl http://192.168.49.2/...` hangs and exits 28 | the node IP is not routable from the host (usual on Windows + the docker driver) | use the A4 port-forward instead |
| `http://taskflow.local` doesn't resolve | hosts entry missing/wrong IP | fix the hosts file (`127.0.0.1` + the port-forward), or just use the `Host:` header |
| `curl` rejects flags like `-H` | in Windows PowerShell 5.1 `curl` is an alias for `Invoke-WebRequest` | use `curl.exe` |
| Images `ErrImagePull` / `ImagePullBackOff` | Image built locally but not in the cluster | Lab step 04 covers loading images |

---

**Next:** [kubectl essentials](02-kubectl-essentials.md) →
