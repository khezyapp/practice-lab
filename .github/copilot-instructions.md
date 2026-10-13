# Copilot instructions — practice-lab

This repository is a **self-paced Kubernetes course plus a runnable lab**, not an
application. There is no test suite, no linter and no CI. Almost every file is either
Markdown prose or a Kubernetes manifest, so "validation" means the manifests still
apply cleanly, the docs still match them, and the documentation site still builds —
not running a test command.

The root `package.json` exists only for the VitePress documentation site (see below).

## Repository shape

There are two halves that must stay in sync:

- **`docs/`** — the teaching material. `docs/concepts/` is one lesson per Kubernetes
  object; `docs/lab/` is the step-by-step guided lab; `docs/getting-started/`,
  `docs/architecture/`, `docs/reference/` are supporting tracks.
- **`labs/`** — the real YAML applied during the lab, one file per lab step, plus
  `labs/app/` (the sample app).

Concept lesson `NN` pairs with lab step `NN` and with `labs/NN-*.yaml`. The numbering
is the navigation mechanism: a lesson links forward to the step, and the step links
back to the lesson. Keep that three-way correspondence intact when editing.

`docs/index.md` is the site landing page and `docs/overview.md` is the course overview
(the learning path, repository map, schedule, prerequisites and progress checklist).
`docs/lab/index.md` and `labs/README.md` are the step/manifest indices. The root
`README.md` is the GitHub landing page; it duplicates the course's pitch deliberately, so
keep it in step when the structure changes.

> The "Repository map" tree inside `docs/overview.md` is **stale** — it lists filenames that
> no longer exist (e.g. `07-postgres-statefulset.yaml`, `08-redis.yaml`,
> `13-cronjob-migrate.yaml`). `labs/README.md` and the actual `labs/*.yaml` files are
> authoritative. Do not copy filenames out of that tree; check the directory.

## The sample application (`labs/app/`)

`app.py` is a single Flask service exposing **four modes from one image**, selected by
the first CLI argument:

| Mode | Invocation | Kubernetes workload that runs it |
|---|---|---|
| `serve` (default) | `gunicorn app:app` | `taskflow-api` Deployment |
| `worker` | `python app.py worker` | `taskflow-worker` Deployment |
| `migrate` | `python app.py migrate` | `taskflow-migrate` Job |
| `cleanup` | `python app.py cleanup` | `taskflow-cleanup` CronJob |

Design rules that the manifests depend on, so preserve them in any edit:

- **All configuration comes from the environment** (ConfigMap/Secret). Nothing is
  baked into the image.
- **The app degrades gracefully** and stays Ready when Postgres/Redis are absent — this
  is deliberate, so the lab can teach Pods/Deployments/Services before the data tier
  exists. `/api/tasks` returns a `503` explaining the database is unreachable while
  `/healthz`, `/readyz`, `/api/whoami`, `/api/config` still respond.
- The container runs as **non-root UID 1000**; `runAsNonRoot: true` depends on this.
- It handles **SIGTERM** so rolling updates do not drop requests.

## Commands

Build and load the image (the lab builds `1.0.0`, then rebuilds as `1.1.0` to
demonstrate a rollout; the minikube profile used by the lab is named `taskflow`):

```powershell
docker build -t taskflow-api:1.0.0 labs/app
minikube image load taskflow-api:1.0.0 --profile taskflow   # minikube only; Docker Desktop shares the daemon
```

Apply the lab in dependency order (`labs/` files are numbered to make a single pass
work). Note `03-pod.yaml` and `11-broken-workloads.yaml` are **not** part of a normal
install:

```powershell
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
kubectl wait --for=condition=complete job/taskflow-migrate -n taskflow --timeout=180s
kubectl rollout restart deployment/taskflow-api deployment/taskflow-worker -n taskflow
```

Teardown, and the PVC/PV caveat that always applies here:

```powershell
kubectl delete namespace taskflow
kubectl delete namespace node-agents
kubectl get pv          # StatefulSet PVCs are NOT garbage-collected with the StatefulSet
```

There is no "run a single test" command — the repo has no tests. Verify a manifest
change by applying it to a local cluster and inspecting the result. Verify a
documentation change by building the site (see below).

## Documentation site (VitePress)

`docs/` is a VitePress site. `docs/.vitepress/config.mts` holds the top nav, the
per-section sidebars, and the Mermaid wrapper. The site is the primary way the course is
read, so a new page is not finished until it is reachable from the sidebar.

- `npm run docs:dev` — dev server with hot reload; the loop to edit docs in.
- `npm run docs:build` — production build into `docs/.vitepress/dist/`. This is the
  validation step for documentation changes, and it **fails the build on a dead link**.
- `npm run docs:preview` — serve the built site, to check what actually ships.
- `docker compose up -d --build` — build and serve the site on <http://localhost:3100>
  with nginx. `Dockerfile` is a two-stage build (Node builds, nginx serves); the final
  image contains no Node or Markdown. `.dockerignore` keeps the context small.

Two things about the image build are load-bearing:

- The build stage installs `git` because VitePress's `lastUpdated` feature spawns it. A
  **missing git binary fails the build outright**; git present but with no `.git`
  directory (the case in Docker, since `.git` is not in the build context) simply omits
  the dates. Do not remove that `apk add`.
- The `HEALTHCHECK` uses `127.0.0.1`, not `localhost`. Inside the image `localhost`
  resolves to `::1` first and nginx listens on IPv4 only, so the container would report
  itself unhealthy while serving perfectly.

Because `lastUpdated` needs git history, page dates appear in local dev but not in the
container. That is expected.

Non-obvious behaviour when editing content:

- **Only `index.md` is treated as a directory index.** `README.md` renders at `/README`,
  which would leave `/` empty. The landing page is `docs/index.md` and section landings
  are `index.md`; renaming one means updating every link that points at it.
- **Vue interpolates `{{ ... }}` in Markdown — including inside code spans and fenced
  code blocks.** A template expression such as `{{.MemTotal}}` fails the build. Wrap it
  in `<code v-pre>...</code>` instead of backticks.
- **Fenced code blocks tagged `mermaid`** are rendered to inline SVG by
  `vitepress-plugin-mermaid`. Mermaid is pinned to `^11` because the plugin's peer range
  is `10 || 11`, and it is bundled eagerly (~169 KB gzipped) on every page.
- **The `vite.optimizeDeps.include` list is load-bearing.** `fastdom` and its two deep
  paths must be pre-bundled or `docs:dev` fails to boot entirely (the production build is
  unaffected, so this only shows up in dev). The `debug` entry is filtered out after
  `withMermaid` because mermaid 11 no longer depends on it and it is not installed.
- **Page links are relative `.md` paths**; VitePress rewrites them to the built URL. A
  link to a file outside `docs/` cannot be resolved by VitePress — point at the GitHub
  URL instead (that is why the two `labs/app/` links are absolute).
- `layout: home` (used only by `docs/index.md`) disables both the sidebar and the
  "On this page" outline. Keep long-form content on a normal page like `overview.md`.

## Conventions

- **Shell syntax is Windows PowerShell 5.1** everywhere in the docs, matching the
  machine the lab was built on. Bash differences are noted only where the syntax
  genuinely differs (e.g. `minikube docker-env`). In PowerShell 5.1 `curl` is an alias
  for `Invoke-WebRequest` and its flags differ, so the lab writes **`curl.exe`**; inside
  a container plain `curl` is correct.
- **Two namespaces, on purpose.** `taskflow` runs the application under Pod Security
  `baseline`; `node-agents` runs the privileged `hostPath` DaemonSet under
  `privileged`, because `baseline` forbids `hostPath` and Pod Security Admission is
  enforced per namespace with no per-Pod exemption. Step 08 has the reader prove this
  by trying to move the DaemonSet back.
- **Labels are load-bearing.** Every object carries `app.kubernetes.io/name`,
  `app.kubernetes.io/component` and `app.kubernetes.io/part-of: taskflow`; NetworkPolicy
  and Service selectors match on them. Do not rename these labels in isolation —
  cross-check `09-networkpolicy.yaml`, which selects on
  `app.kubernetes.io/component`.
- **Never `:latest`.** Image tags are explicit (`1.0.0`, then `1.1.0`) so rollouts and
  `kubectl rollout undo` have something to act on.
- **`StorageClass` is intentionally unspecified** so the cluster default is used.
- **YAML comments are teaching material.** The manifests are commented like lesson
  notes, including the header block that names the lab step and the `kubectl apply`
  line. Match that voice when adding or editing manifests; do not strip comments as
  "noise".
- **Lab steps follow a fixed section order**: Goal → Before you start → Steps → Verify →
  Break it → Exercises → Troubleshooting → Clean up. New steps should follow it, and the
  "Break it" section is considered the point of the step, not optional filler.

## Kubernetes MCP server

`.vscode/mcp.json` configures `containers/kubernetes-mcp-server` over stdio, pinned to
`--cluster-provider kubeconfig` and `--toolsets core,config`. It talks to the cluster
through client-go, so it does **not** require a `kubectl` binary on PATH.

It only works once a kubeconfig with a current context exists — the server exits with
`no current-context is set` until then. `minikube start` creates that context.

Two flags worth knowing when the cluster matters and mistakes are costly:
`--read-only` exposes only read tools, and `--disable-destructive` keeps writes but drops
`destructiveHint` tools. Neither is enabled by default, because the lab deliberately
applies, breaks and deletes workloads.

## Adding or renumbering a lab step

A new step touches several places at once; a change that updates only one of them
leaves the repo inconsistent:

1. `docs/concepts/NN-*.md` — the concept lesson (if the step introduces a new object).
2. `docs/lab/NN-*.md` — the step itself, following the section order above.
3. `labs/NN-*.yaml` — the manifest, with a header comment naming the step.
4. The index tables: `docs/overview.md` (learning path), `docs/lab/index.md` (step
   index), `labs/README.md` (apply order table), and the sidebar/nav entries in
   `docs/.vitepress/config.mts`.
5. Cross-links in the neighbouring step/lesson, and the progress checklist in
   `docs/overview.md`.
