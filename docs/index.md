---
layout: home
title: Kubernetes Practice Lab
titleTemplate: false

hero:
  name: Kubernetes Practice Lab
  text: From zero to a production-like app
  tagline: Read a short concept lesson, then run the matching lab step on a real cluster. No cloud account, no Helm — plain YAML that you apply, break, and diagnose.
  actions:
    - theme: brand
      text: Start the lab
      link: /lab/00-scenario-and-requirements
    - theme: alt
      text: The mental model
      link: /getting-started/03-mental-model
    - theme: alt
      text: Browse the concepts
      link: /concepts/01-what-is-kubernetes

features:
  - title: Concept, then hands-on
    details: Every concept lesson links to the lab step that exercises it, and every lab step links back to the concept it uses.
  - title: One realistic application
    details: TaskFlow is built up piece by piece — Deployment, Service, Ingress, StatefulSet, DaemonSet, NetworkPolicy, HPA, Job and CronJob.
  - title: Learn by breaking things
    details: Each step ends with a "Break it" experiment, and one manifest file is deliberately broken so you can practise diagnosing it.
  - title: Runs on your laptop
    details: minikube or Docker Desktop Kubernetes. 13 concept lessons, 14 lab steps, and 19 diagrams.
---

## How the course fits together

Two halves that point at each other. Read a concept lesson, then do the matching lab
step — muscle memory beats reading.

| Track | What it is | Where to start |
| --- | --- | --- |
| **A — Concepts** | 13 short lessons, one per Kubernetes object, in dependency order | [Concept 01 — What is Kubernetes?](/concepts/01-what-is-kubernetes) |
| **B — Lab** | One realistic app (TaskFlow) built up step by step on a real cluster | [Lab 00 — Scenario & requirements](/lab/00-scenario-and-requirements) |
| **C — Reference** | kubectl cheatsheet, glossary, and interview questions with answers | [kubectl cheatsheet](/reference/kubectl-cheatsheet) |

Not sure where to begin? Read [the mental model](/getting-started/03-mental-model) first —
it ties image → Pod → Deployment → Service → Ingress together in a single page.

The repository map, the suggested four-week schedule, prerequisites, and the progress
checklist live on the [course overview](/overview) page.

## How each lab step is structured

```
Goal → Before you start → Steps → Verify → Break it → Exercises → Troubleshooting → Clean up
```

**Do the "Break it" sections.** Applying YAML teaches you syntax; breaking something and
diagnosing it teaches you Kubernetes.
