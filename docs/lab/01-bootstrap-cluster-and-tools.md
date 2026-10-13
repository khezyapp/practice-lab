# Lab 01 — Bootstrap the cluster

**Goal:** a working cluster with an Ingress controller and metrics-server, plus a
`kubectl` that is pointed at it and defaults to the `taskflow` namespace.

**Before you start:** [getting-started/01-local-cluster-setup.md](../getting-started/01-local-cluster-setup.md)
has the full detail for both minikube and Docker Desktop. This page is the lab
version: do it, verify it, and check it off.

**Concept you are exercising:** [What is Kubernetes](../concepts/01-what-is-kubernetes.md) —
the control plane, Nodes and add-ons you are about to look at.

---

## 1. Start the cluster

```powershell
minikube start --profile taskflow --cpus=4 --memory=5120 --disk-size=20g --driver=docker
```

- `--driver=docker` reuses the Docker Desktop engine that is already running, so no
  extra VM software is needed.
- `--memory=5120` (5 GiB) is a safe value here: this machine's Docker Desktop has
  about 7.6 GiB available. **Never set the cluster's memory above Docker Desktop's
  total**, or the cluster will fail to start with a confusing error.

If you prefer Docker Desktop's built-in Kubernetes instead:

1. Docker Desktop → Settings → Kubernetes → **Enable Kubernetes** → Apply & Restart
2. `kubectl config use-context docker-desktop`
3. Install ingress-nginx and metrics-server as described in the setup doc.

---

## 2. Point kubectl at it

```powershell
kubectl config get-contexts
kubectl config use-context taskflow          # or: docker-desktop
kubectl config set-context --current --namespace=taskflow
```

The second command saves you from typing `-n taskflow` for the rest of the lab.

> The namespace does not exist yet — that is fine and expected. It is created in
> step 02. Until then, `kubectl get pods` will report `No resources found in
> taskflow namespace`, which is the correct answer.

---

## 3. Turn on the two add-ons the lab needs

```powershell
minikube addons enable ingress        --profile taskflow
minikube addons enable metrics-server --profile taskflow
```

| Add-on | What it gives you | Used in |
|---|---|---|
| **ingress-nginx** | the proxy that makes `Ingress` objects do anything at all | step 06 |
| **metrics-server** | `kubectl top` and the CPU data the HPA scales on | step 10 |

Docker Desktop users: install them with the two `kubectl apply -f https://...`
commands in the [setup doc](../getting-started/01-local-cluster-setup.md#option-b--docker-desktop-kubernetes),
plus the `--kubelet-insecure-tls` patch for metrics-server.

---

## 4. Verify — the checkpoint

Every command must succeed. Do not continue until they do.

```powershell
kubectl version                    # client AND server versions
kubectl get nodes -o wide          # at least one node, STATUS=Ready
kubectl get pods -A                # control plane + add-ons
kubectl get storageclass           # one class marked (default)  <- needed in step 07
kubectl get ingressclass           # nginx present               <- needed in step 06
kubectl get apiservices | Select-String metrics    # metrics.k8s.io/v1beta1
kubectl top nodes                  # may take 1-2 minutes to start working
kubectl auth can-i create deployments              # yes
```

Expected `kubectl get nodes -o wide` output:

```
NAME       STATUS   ROLES           AGE   VERSION   INTERNAL-IP    OS-IMAGE
taskflow   Ready    control-plane   2m    v1.3x.x   192.168.49.2   Ubuntu ...
```

Expected `kubectl get pods -A` — you should see Pods for `kube-apiserver`,
`etcd`, `kube-scheduler`, `kube-controller-manager`, `coredns`, `kube-proxy`,
`storage-provisioner`, the `ingress-nginx-controller`, and `metrics-server`
(some may still be `ContainerCreating` for a minute).

### Find the Ingress address now, so step 06 is painless

```powershell
kubectl get svc -n ingress-nginx
minikube ip --profile taskflow
```

Record both. The `ingress-nginx` add-on normally binds the controller to the node's port
80, so a working Ingress will report `ADDRESS 192.168.49.2`.

**Test that this IP is actually reachable from your host before you rely on it** — on
Windows with the `--driver=docker` driver it usually is not, because the node lives inside
Docker's network:

```powershell
curl.exe --max-time 5 -H "Host: taskflow.local" http://192.168.49.2/
# exit 28 / a timeout means: not routable from here.
```

Either way, this is the method the lab uses, and it works everywhere:

```powershell
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80
# in another terminal:
curl.exe -s -H "Host: taskflow.local" http://localhost:8080/api/tasks
```

> Use `curl.exe`, not `curl`: in Windows PowerShell 5.1 `curl` is an alias for
> `Invoke-WebRequest`, and its flags are different.

### Optional: a real hostname

As Administrator, add to `C:\Windows\System32\drivers\etc\hosts`:

```
127.0.0.1  taskflow.local api.taskflow.local
```

Then, with the port-forward running, `http://taskflow.local:8080` works in a browser.
(If your node IP turned out to be reachable, you can point the entry at it instead and
drop the port.)

> This file edit needs Administrator rights, so it must be done by you. It is optional —
> the `Host:` header above is enough for the entire lab.

---

## 5. Look at what you just built

This is worth 5 minutes, because these are the components from the concept lesson,
running on your machine. The control plane is not abstract — it is a set of
containers.

```powershell
kubectl get pods -n kube-system
kubectl describe pod -n kube-system -l component=kube-apiserver | Select-String -Pattern "Image|Command" -Context 0,3
kubectl get --raw /healthz
kubectl get --raw /readyz
kubectl cluster-info
kubectl api-resources | Select-Object -First 20
```

Note what you find in `kube-system`: **the control plane runs as Pods on your
single Node.** In a cloud cluster those components live on machines you never see.
Same architecture, different packaging.

---

## 6. Exercises

1. `kubectl get nodes --show-labels`. Which labels exist by default, and which
   would you use in a `nodeSelector`?
2. `kubectl describe node taskflow | Select-String -Pattern "Allocated resources" -Context 0,12`.
   How much CPU/memory is already requested *before* you deploy anything? That
   number is your real headroom.
3. `kubectl get storageclass -o yaml`. What is the provisioner, and what is the
   `volumeBindingMode`? Why does the answer matter for step 07?
4. `kubectl get events -A --sort-by=.lastTimestamp | Select-Object -Last 15`.
   This is the cluster telling you what it has been doing.
5. `kubectl explain pod.spec.containers.livenessProbe` — the offline documentation
   for the version you are actually running.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `The connection to the server localhost:8080 was refused` | no current context | `kubectl config use-context taskflow` |
| `minikube start` hangs or fails immediately | Docker Desktop not running, or memory above Docker's total | start Docker Desktop; lower `--memory` |
| `kubectl top nodes` → `Metrics API not available` | metrics-server still starting, or TLS issue | wait 2 min, then check its logs |
| `kubectl get storageclass` is empty | no default provisioner | re-create the cluster, or install a storage provisioner |
| `ingressclass` missing | add-on not enabled | `minikube addons enable ingress --profile taskflow` |
| Cluster feels sluggish | 5 GiB split across control plane + add-ons | stop other containers, or accept it |

---

## 8. Clean up

Nothing to clean up. Keep the cluster running — it stays up between sessions.

To stop it later without losing anything:

```powershell
minikube stop --profile taskflow      # data preserved
minikube delete --profile taskflow    # everything gone
```

---

**Checkpoint:** `kubectl get nodes` shows `Ready`, `kubectl get ingressclass`
shows `nginx`, and `kubectl top nodes` returns numbers.

**Next:** [Lab 02 — Namespace, ConfigMap, Secret](02-namespace-config-secrets.md) →
