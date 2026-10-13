# Reference — kubectl cheatsheet

Everything here works in `kubectl` v1.30+ and uses PowerShell syntax (this machine).
Bash differences are noted where they matter.

Set this up once so you stop typing `-n taskflow`:

```powershell
kubectl config use-context taskflow
kubectl config set-context --current --namespace=taskflow
```

---

## 1. Looking at things

```powershell
kubectl get pods                            # current namespace
kubectl get pods -A                         # all namespaces
kubectl get pods -o wide                    # + IP, node, image
kubectl get all                             # the common kinds
kubectl get pods,svc,deploy,rs -o wide      # several kinds at once
kubectl get pods --show-labels
kubectl get pods -l app.kubernetes.io/name=taskflow-api
kubectl get pods --field-selector status.phase=Running
kubectl get pods -w                         # watch
kubectl get pods --sort-by=.metadata.creationTimestamp
kubectl get ns
kubectl get events --sort-by=.lastTimestamp | Select-Object -Last 20
kubectl get events --field-selector type=Warning
kubectl api-resources                       # every kind + short names + namespaced?
kubectl api-versions
kubectl cluster-info
```

### Output formats

```powershell
kubectl get pod <p> -o yaml                 # the whole object
kubectl get pod <p> -o json
kubectl get pod <p> -o name                 # pod/<name>
kubectl get pod <p> -o jsonpath='{.status.podIP}'
kubectl get pod <p> -o jsonpath='{.spec.containers[*].image}'
kubectl get pods -o custom-columns='NAME:.metadata.name,IP:.status.podIP,NODE:.spec.nodeName'
kubectl get pods -o json | ConvertFrom-Json | ConvertTo-Json -Depth 1   # PowerShell pipeline
```

---

## 2. The three debugging commands

```powershell
kubectl describe pod <p>                    # Events are the answer
kubectl logs <p>                            # stdout/stderr
kubectl exec -it <p> -- sh                  # a shell inside
```

```powershell
kubectl logs <p> -c <container>             # multi-container Pod
kubectl logs <p> --previous                 # the container that just died
kubectl logs <p> --tail=50 --timestamps
kubectl logs -f deploy/taskflow-api         # stream from a Deployment
kubectl logs -l app.kubernetes.io/name=taskflow-api --prefix --tail=20
kubectl logs job/taskflow-migrate
kubectl logs node-log-agent-xxxxx -n node-agents   # node agents live in their own namespace
kubectl logs -n kube-system -l k8s-app=kube-dns    # add-on pods live in kube-system
```

```powershell
kubectl exec -it <p> -- env
kubectl exec -it <p> -- ls -l /etc/taskflow
kubectl exec -it <p> -- printenv DB_HOST
kubectl debug -it <p> --image=busybox:1.36 --target=<container>   # distroless/crashed pods
kubectl debug node/taskflow -it --image=busybox:1.36              # node-level shell
kubectl cp <p>:/path/file ./local
```

**The single most useful debugging trick:**

```powershell
kubectl run tmp --rm -it --image=busybox:1.36 --restart=Never -- sh
kubectl run net --rm -it --image=nicolaka/netshoot --restart=Never -- bash
```

---

## 3. Creating and changing

```powershell
kubectl apply -f file.yaml                  # create or update
kubectl apply -f dir/                       # everything in a directory
kubectl apply -f https://url/manifest.yaml
kubectl create -f file.yaml                 # create only (fails if it exists)
kubectl delete -f file.yaml
kubectl delete pod <p> --grace-period=0 --force     # last resort
kubectl edit deploy <d>                     # live edit - do NOT do this in production
kubectl patch deploy <d> --type merge -p '{"spec":{"replicas":5}}'
kubectl patch deploy <d> --type json -p '[{"op":"replace","path":"/spec/replicas","value":5}]'
kubectl set image deploy/<d> <container>=<image>:<tag>
kubectl scale deploy <d> --replicas=5
kubectl autoscale deploy <d> --min=2 --max=8 --cpu-percent=70
kubectl label pod <p> env=prod
kubectl annotate pod <p> owner=team-a
kubectl rollout restart deploy <d>
kubectl replace --force -f file.yaml        # delete + recreate (drops the Pod template hash)
```

### Generate YAML instead of typing it

```powershell
kubectl create deployment web --image=nginx:1.27 --dry-run=client -o yaml > web.yaml
kubectl create configmap c --from-literal=k=v --dry-run=client -o yaml
kubectl create secret generic s --from-literal=p=CHANGE_ME --dry-run=client -o yaml
kubectl expose deploy web --port=80 --target-port=8080 --dry-run=client -o yaml
kubectl create job --from=cronjob/taskflow-cleanup manual-1 -n taskflow
```

### Preview before you commit

```powershell
kubectl apply -f labs/ --dry-run=client     # offline parse only
kubectl apply -f labs/ --dry-run=server     # real admission, no change
kubectl diff -f labs/04-deployment.yaml     # what WOULD change
kubectl rollout status deploy/taskflow-api
kubectl rollout history deploy/taskflow-api
kubectl rollout undo deploy/taskflow-api
kubectl rollout undo deploy/taskflow-api --to-revision=2
kubectl rollout pause deploy/taskflow-api
kubectl rollout resume deploy/taskflow-api
```

---

## 4. The layering tools

```powershell
# scheduling
kubectl get nodes --show-labels
kubectl describe node taskflow | Select-String "Allocated resources" -Context 0,12
kubectl taint nodes taskflow key=value:NoSchedule
kubectl cordon taskflow                     # no new Pods
kubectl drain taskflow --ignore-daemonsets --delete-emptydir-data
kubectl uncordon taskflow

# resources
kubectl top nodes
kubectl top pods --containers
kubectl describe resourcequota taskflow-quota
kubectl describe limitrange taskflow-defaults

# networking
kubectl get svc -o wide
kubectl describe svc taskflow-api
kubectl get endpointslices -A
kubectl get endpointslices -l kubernetes.io/service-name=taskflow-api -o yaml
kubectl get ingress
kubectl describe ingress taskflow
kubectl get networkpolicies
kubectl describe networkpolicy default-deny-all
kubectl port-forward svc/taskflow-api 8080:80
kubectl port-forward pod/<p> 8080:8080
kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80

# storage
kubectl get storageclass
kubectl get pv,pvc
kubectl describe pvc data-postgres-0

# config
kubectl get cm,secret
kubectl get secret taskflow-secret -o jsonpath='{.data.DB_PASSWORD}'
kubectl get secret taskflow-secret -o jsonpath='{.data.DB_PASSWORD}' | ForEach-Object { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }

# autoscaling & disruption
kubectl get hpa -w
kubectl describe hpa taskflow-api
kubectl get pdb
kubectl describe pdb taskflow-api

# batch
kubectl get jobs,cronjobs
kubectl logs job/<job>
kubectl wait --for=condition=complete job/taskflow-migrate --timeout=180s
kubectl patch cronjob taskflow-cleanup -p '{"spec":{"suspend":true}}'
```

---

## 5. Security and permissions

```powershell
kubectl auth can-i get pods
kubectl auth can-i create deployments --as=system:serviceaccount:taskflow:taskflow-api
kubectl auth can-i --list
kubectl auth whoami
kubectl get roles,rolebindings,clusterroles,clusterrolebindings -A
kubectl get sa
kubectl get --raw /healthz
kubectl get --raw /readyz
kubectl get --raw /api/v1/namespaces/taskflow/pods | ConvertFrom-Json
```

---

## 6. Contexts and config

```powershell
kubectl config get-contexts
kubectl config current-context
kubectl config use-context taskflow
kubectl config set-context --current --namespace=taskflow
kubectl config view --minify
kubectl config view --raw                 # includes credentials - do not share
```

---

## 7. Offline documentation

```powershell
kubectl explain pod
kubectl explain pod.spec
kubectl explain deployment.spec.strategy.rollingUpdate
kubectl explain networkpolicy.spec.ingress
kubectl explain statefulset.spec.volumeClaimTemplates
kubectl explain --recursive pod.spec.containers   # the whole tree
```

---

## 8. Symptom → first command

| Symptom | First command |
|---|---|
| Pod `Pending` | `kubectl describe pod` → `FailedScheduling` |
| `ImagePullBackOff` | `kubectl describe pod` → `Failed` events |
| `CreateContainerConfigError` | `kubectl describe pod` (no logs exist) |
| `CrashLoopBackOff` | `kubectl logs <p> --previous` |
| `Running` but `0/1` | `kubectl describe pod` → `Unhealthy` events |
| Service not working | `kubectl get endpointslices` |
| 404 from the Ingress | `kubectl describe ingress` |
| 503 from the Ingress | `kubectl get endpointslices` |
| 502 from the Ingress | `kubectl describe svc` → `targetPort` |
| PVC `Pending` | `kubectl describe pvc` |
| HPA `<unknown>` | `kubectl top pods`; check metrics-server; check requests |
| `kubectl top` unavailable | `kubectl logs -n kube-system deploy/metrics-server` |
| Node `NotReady` | `kubectl describe node`, then `kubectl get pods -n kube-system` |
| Pod stuck `Terminating` | `kubectl describe pod` → finalizers, grace period |
| Everything is slow | `kubectl top pods --containers`, compare with limits |
| "It worked a minute ago" | `kubectl get events --sort-by=.lastTimestamp` |

---

## 9. Handy habits

```powershell
# a short alias file for your profile
function k    { kubectl $args }
function kgp  { kubectl get pods -o wide }
function kgpa { kubectl get pods -A -o wide }
function kd   { kubectl describe $args }
function kl   { kubectl logs $args }
function ke   { kubectl exec -it $args -- sh }
function kdesc { kubectl describe $args }
```

```powershell
# namespace + context guard - run before anything destructive
"context: $(kubectl config current-context)  namespace: $(kubectl config view --minify -o jsonpath='{..namespace}')"
```

For interactive triage, install **k9s** — but only after you can do it by hand. A TUI
that hides the commands prevents you from learning them.
