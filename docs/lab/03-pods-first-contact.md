# Lab 03 — Your first Pod

**Goal:** run a Pod, see its IP and lifecycle, prove to yourself that it is
ephemeral and unsupervised, and learn the debugging commands you will use for the
rest of the lab.

**Read first:** [Pod](../concepts/02-pod.md).

**Time:** ~45 minutes.

---

## 1. The Pod you already have

Your repository already contains `sample.yaml` at the root. Read it:

```powershell
Get-Content sample.yaml
```

It is a bare Pod running `nginx:latest`. Do not apply it yet — the repo version of
this step uses a better image and a proper namespace:

```powershell
kubectl apply -f labs/03-pod.yaml
```

```powershell
kubectl get pod hello-pod -n taskflow -o wide
```

```
NAME        READY   STATUS    RESTARTS   AGE   IP           NODE       NOMINATED NODE
hello-pod   1/1     Running   0          12s   10.244.1.5   taskflow   <none>
```

**Write down that IP.** You are about to lose it.

---

## 2. Look at what a Pod actually is

```powershell
kubectl describe pod hello-pod -n taskflow
```

Read the Events at the bottom — they are the audit trail of everything that
happened, in order:

```
Events:
  Scheduled    Successfully assigned taskflow/hello-pod to taskflow
  Pulling      Pulling image "nginxdemos/hello:plain-text"
  Pulled       Successfully pulled image in 2.1s
  Created      Created container hello
  Started      Started container hello
```

That is the pipeline from [architecture/02](../architecture/02-what-happens-on-apply.md):
scheduler → kubelet → CRI → container. Nothing there says "start a container" —
you declared a Pod, and the components converged on it.

Also note in the output:

- **QoS Class** — `Burstable`, because requests and limits differ
- **Labels** — the ones your selectors will use in later steps
- **Containers → Port** and **Mounts** — the container's view of the world

### Talk to it

```powershell
# 1. Logs
kubectl logs hello-pod -n taskflow

# 2. Get a shell inside
kubectl exec -it hello-pod -n taskflow -- sh
# inside:
hostname                    # -> hello-pod
cat /etc/hostname           # same
exit

# 3. Reach it without a Service (debugging only!)
kubectl port-forward pod/hello-pod -n taskflow 8080:80
# in another terminal:
curl.exe http://localhost:8080
```

`nginxdemos/hello` prints the **pod name and IP**, which is exactly what you want to
see: the answer to "which replica handled this?" comes from the application, and you
will use the same trick in step 05 to prove load balancing works.

---

## 3. Prove it is ephemeral

### The IP is not stable

```powershell
kubectl delete pod hello-pod -n taskflow
kubectl apply -f labs/03-pod.yaml
kubectl get pod hello-pod -n taskflow -o wide
```

**A different IP.** Different node scheduling decision, different CNI allocation.
The name is the same only because you asked for that exact name.

### Nothing brings it back

```powershell
kubectl delete pod hello-pod -n taskflow
kubectl get pods -n taskflow
# No resources found in taskflow namespace.
```

Sit with that for a second. **You deleted it and nothing recreated it.** There is no
controller, no supervisor, no self-healing. This is the single most important thing
to feel in this lab, because step 04 exists entirely to fix it.

### A crashed container *is* restarted (by the kubelet)

Create it again, then kill the main process:

```powershell
kubectl apply -f labs/03-pod.yaml
kubectl exec -it hello-pod -n taskflow -- sh -c 'kill 1'
kubectl get pod hello-pod -n taskflow -w
```

Because `restartPolicy` defaults to `Always`, the **kubelet** restarts the container:

```
NAME        READY   STATUS    RESTARTS   AGE
hello-pod   1/1     Running   1          32s     <-- note the RESTARTS column
```

Two different mechanisms, and it matters which is which:

| What died | Who fixes it |
|---|---|
| A **container** inside a Pod | the **kubelet**, per `restartPolicy`, with exponential backoff |
| The **Pod** itself | a **controller** — and a bare Pod has none |

---

## 4. Add an init container and a sidecar

Real Pods often hold more than one container. Edit `labs/03-pod.yaml` and uncomment
the block marked in the file:

```yaml
spec:
  initContainers:
    - name: wait-for-something
      image: busybox:1.36
      command: ["sh","-c","sleep 3; echo pre-flight done"]
  containers:
    - name: log-sidecar
      image: busybox:1.36
      command: ["sh","-c","while true; do echo sidecar tick; sleep 30; done"]
```

```powershell
kubectl apply -f labs/03-pod.yaml
kubectl get pod hello-pod -n taskflow -w
```

Watch the sequence: `Init:0/1` → `PodInitializing` → `Running` with `2/2`.
The init container **runs to completion first**; only then does the app start.

```powershell
kubectl logs hello-pod -n taskflow -c wait-for-something     # the init container
kubectl logs hello-pod -n taskflow -c log-sidecar            # the sidecar
kubectl logs hello-pod -n taskflow -c hello                  # the app
```

Note that `kubectl logs` needs `-c` when a Pod has more than one container, and that
**each container has its own log stream** — which is exactly why log collectors on
each Node (step 08) read from `/var/log/pods` rather than from the container.

Now prove they share the network:

```powershell
kubectl exec -it hello-pod -n taskflow -c log-sidecar -- sh -c "wget -qO- localhost:80 | head -3"
```

The sidecar reached the app on `localhost` — they are in one network namespace with
one IP. That is what makes the sidecar pattern possible.

Revert the file when you are done so later steps are unaffected (`git checkout labs/03-pod.yaml`,
or re-comment the block).

---

## 5. Exercises

1. **`kubectl get pod -o yaml`** and find `status.phase`, `status.podIP`,
   `status.qosClass`, `status.containerStatuses[0].restartCount`. Then query each of
   them directly with `-o jsonpath`.
2. Set `terminationGracePeriodSeconds: 5`, then `kubectl delete pod hello-pod` and
   watch how fast it goes. Then set it to 300 and watch it hang in `Terminating`
   until you `kubectl delete pod hello-pod --grace-period=0 --force`. Why is forcing
   dangerous? (It skips the app's cleanup — and with a Deployment you would orphan
   the replacement logic.)
3. Change the image to `nginxdemos/hello:tag-that-does-not-exist` and apply. Watch
   the state machine: `ErrImagePull` → `ImagePullBackOff`. Then run
   `kubectl describe pod` and read the exact registry error.
4. Remove the `resources` block and re-apply. What is the QoS class now? (Check
   `status.qosClass` — the LimitRange from step 02 will have injected defaults, so it
   will *not* be `BestEffort`. This is the LimitRange doing its job.)
5. `kubectl explain pod.spec.restartPolicy`. Which values are legal, and which one
   would a Job require?
6. Create `sample.yaml` (the repo root file) as-is and describe what is wrong with it
   for production. There are at least four problems: no namespace, `:latest` tag, no
   resources, no probes, no owner.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pod stuck `Pending` | no node can accept it (resources, nodeSelector, taints) | `kubectl describe pod` → Events names the reason |
| `ImagePullBackOff` | wrong tag, or a private image without `imagePullSecrets` | check the exact error in `describe` |
| `CrashLoopBackOff` | the container exits; the kubelet retries with backoff | `kubectl logs <pod> --previous` |
| `Exec format error` | image built for the wrong CPU architecture (common with Apple Silicon) | build with `--platform=linux/amd64` |
| `kubectl exec` → `unable to upgrade connection` | exec goes via the API server and kubelet | `kubectl get --raw /healthz`; check the node is `Ready` |
| `port-forward` "address already in use" | 8080 taken (Docker Desktop often holds it) | use another local port, e.g. `8081:80` |

---

## 7. Clean up

```powershell
kubectl delete pod hello-pod -n taskflow --ignore-not-found
kubectl get pods -n taskflow        # should be empty
```

Delete it — and note that this time you had to *do* something, which is the whole
lesson. In the next step, deleting a Pod will be a non-event.

---

**Checkpoint:** you have seen a Pod's IP change after deletion, watched nothing
recreate it, and understand that the kubelet restarts *containers* while a
*controller* restarts *Pods*.

**Next:** [Lab 04 — Build the image, deploy it](04-deployment-and-image.md) →
