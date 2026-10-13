# Kubernetes Practice Lab

A self-paced Kubernetes course you run on your own laptop. Read a short concept lesson,
then do the matching lab step against a real cluster — no cloud account, no Helm, just
plain YAML that you apply, break, and diagnose.

Everything is built around one small but realistic application, **TaskFlow**, which grows
from a single Pod into a production-shaped deployment: Postgres behind a StatefulSet, an
Ingress in front, NetworkPolicies around it, and an HPA scaling it.

---

## What's inside

| | |
|---|---|
| **13 concept lessons** | One per Kubernetes object, in dependency order — Pod, Deployment, Service, Ingress, Egress, StatefulSet, DaemonSet, ConfigMaps & Secrets, Storage, Probes & Scaling, Namespaces/RBAC/Quotas, Jobs & CronJobs |
| **14 lab steps** | Build TaskFlow piece by piece on a real cluster, each step ending in a "Break it" experiment |
| **2 architecture deep-dives** | The request lifecycle, and what actually happens when you run `kubectl apply` |
| **3 reference pages** | kubectl cheatsheet, glossary, and interview questions with answers |

Concept lesson `NN` pairs with lab step `NN` and with `labs/NN-*.yaml`, so you can read a
lesson and immediately apply it.

## Read the course as a website

The docs are a [VitePress](https://vitepress.dev) site with full-text search and a
sidebar that follows the course structure.

### Locally, with hot reload

```bash
npm install
npm run docs:dev          # http://localhost:5173
```

### With Docker

```bash
docker compose up -d --build    # http://localhost:3100
docker compose logs -f docs     # follow the server
docker compose down             # stop
```

The image builds the site with Node and serves the static output with nginx, so what you
get in the container is exactly what would be deployed. The service uses
`restart: unless-stopped`, so it comes back after a reboot.

| Script | What it does |
|---|---|
| `npm run docs:dev` | Dev server with hot reload — the loop to edit docs in |
| `npm run docs:build` | Production build into `docs/.vitepress/dist/`; also the validation step (it **fails on a dead link**) |
| `npm run docs:preview` | Serve the built site locally, to check what actually ships |

## Do the lab

### Prerequisites

| Tool | Why |
|---|---|
| `docker` | Build the TaskFlow image, and host a cluster |
| `kubectl` | Talk to the cluster |
| `minikube` | The recommended local cluster — its ingress and metrics-server add-ons are one command each |

Docker Desktop's built-in Kubernetes also works. You do **not** need a cloud account.

The lab steps in [`docs/lab/`](docs/lab/) are written for **Windows PowerShell 5.1**
(matching the machine the lab was built and verified on); bash differences are called out
where the syntax genuinely differs.

### Run the whole lab once

Once you have a cluster and have done the lab at least once, `labs/` is numbered in
dependency order, so a single pass works. Start by building the app image:

```bash
docker build -t taskflow-api:1.0.0 labs/app
minikube image load taskflow-api:1.0.0 --profile taskflow    # Docker Desktop: skip
```

Then apply the manifests:

```bash
kubectl apply -f labs/01-namespace.yaml
kubectl apply -f labs/02-config-secret.yaml
kubectl apply -f labs/04-deployment.yaml
kubectl apply -f labs/05-service.yaml
kubectl apply -f labs/06-ingress.yaml
kubectl apply -f labs/07-postgres.yaml
kubectl rollout status statefulset/postgres -n taskflow --timeout=180s
kubectl apply -f labs/08-daemonset.yaml
kubectl apply -f labs/09-networkpolicy.yaml
kubectl apply -f labs/10-hpa-pdb.yaml
```

Wait for the migration Job, then restart the API so it picks up the database:

```bash
kubectl wait --for=condition=complete job/taskflow-migrate -n taskflow --timeout=180s
kubectl rollout restart deployment/taskflow-api deployment/taskflow-worker -n taskflow
```

`labs/03-pod.yaml` and `labs/11-broken-workloads.yaml` are deliberately **not** in that
list — one is a teaching example you delete immediately, the other is intentionally
broken for debugging practice. Apply them only when the matching lab step asks.

### Tear down

```bash
kubectl delete namespace taskflow
kubectl delete namespace node-agents
```

StatefulSet PVCs are deliberately **not** garbage-collected with the StatefulSet, so
check for leftovers before assuming the data is gone:

```bash
kubectl get pv
```

## Repository layout

```
docs/                 the course (a VitePress site)
  index.md            landing page
  overview.md         learning path, schedule, prerequisites, progress checklist
  getting-started/    local cluster, kubectl basics, the mental model
  concepts/           13 lessons, one per Kubernetes object
  architecture/       request lifecycle, what happens on apply
  lab/                the 14 guided steps
  reference/          cheatsheet, glossary, interview questions
labs/                 the YAML you actually apply
  app/                TaskFlow source + Dockerfile
  NN-*.yaml           one manifest per lab step
docker/nginx.conf     nginx config for the container image
sample.yaml           your first Pod manifest (lab step 03)
```

The lab manifests are commented like teaching notes — read them as you go, rather than
`kubectl apply`-ing on autopilot.

## The practice app

TaskFlow is deliberately small but forces every real concern to appear. One image serves
**four Kubernetes workloads**, selected by the first CLI argument:

| Mode | Invocation | Runs as |
|---|---|---|
| `serve` (default) | `gunicorn app:app` | the `taskflow-api` Deployment |
| `worker` | `python app.py worker` | the `taskflow-worker` Deployment |
| `migrate` | `python app.py migrate` | the `taskflow-migrate` Job |
| `cleanup` | `python app.py cleanup` | the `taskflow-cleanup` CronJob |

All configuration arrives from ConfigMaps and Secrets — nothing is baked into the image —
and the API starts and becomes Ready even when Postgres and Redis are unreachable, so you
can learn Pods, Deployments, Services and Ingress before any stateful dependency exists.

## Where to start

New to Kubernetes? Read
[the mental model](docs/getting-started/03-mental-model.md) first — it connects image →
Pod → Deployment → Service → Ingress in a single page. Then work through
[the course overview](docs/overview.md) or jump straight into
[lab step 00](docs/lab/00-scenario-and-requirements.md).

Want to know how the site itself is wired up? See
[.github/copilot-instructions.md](.github/copilot-instructions.md).
