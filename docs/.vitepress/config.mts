import { withMermaid } from 'vitepress-plugin-mermaid'

const repo = 'https://github.com/khezyapp/practice-lab'

const gettingStarted = [
  { text: 'Run a cluster on your laptop', link: '/getting-started/01-local-cluster-setup' },
  { text: 'kubectl essentials', link: '/getting-started/02-kubectl-essentials' },
  { text: 'The mental model (read this first)', link: '/getting-started/03-mental-model' }
]

const concepts = [
  { text: '01 — What is Kubernetes?', link: '/concepts/01-what-is-kubernetes' },
  { text: '02 — Pod', link: '/concepts/02-pod' },
  { text: '03 — Deployment', link: '/concepts/03-deployment' },
  { text: '04 — Service', link: '/concepts/04-service' },
  { text: '05 — Ingress', link: '/concepts/05-ingress' },
  { text: '06 — Egress and NetworkPolicy', link: '/concepts/06-egress' },
  { text: '07 — StatefulSet', link: '/concepts/07-statefulset' },
  { text: '08 — DaemonSet', link: '/concepts/08-daemonset' },
  { text: '09 — ConfigMaps and Secrets', link: '/concepts/09-configmaps-and-secrets' },
  { text: '10 — Storage and Volumes', link: '/concepts/10-storage-and-volumes' },
  { text: '11 — Probes, Resources and Scaling', link: '/concepts/11-probes-resources-and-scaling' },
  { text: '12 — Namespaces, RBAC and Quotas', link: '/concepts/12-namespaces-rbac-and-quotas' },
  { text: '13 — Jobs and CronJobs', link: '/concepts/13-jobs-and-cronjobs' }
]

const lab = [
  { text: 'Overview', link: '/lab/' },
  { text: '00 — Scenario and requirements', link: '/lab/00-scenario-and-requirements' },
  { text: '01 — Bootstrap the cluster', link: '/lab/01-bootstrap-cluster-and-tools' },
  { text: '02 — Namespace, ConfigMap, Secret', link: '/lab/02-namespace-config-secrets' },
  { text: '03 — Your first Pod', link: '/lab/03-pods-first-contact' },
  { text: '04 — Build the image, deploy it', link: '/lab/04-deployment-and-image' },
  { text: '05 — Service', link: '/lab/05-service' },
  { text: '06 — Ingress', link: '/lab/06-ingress' },
  { text: '07 — StatefulSet and Postgres', link: '/lab/07-statefulset-postgres' },
  { text: '08 — DaemonSet', link: '/lab/08-daemonset' },
  { text: '09 — Egress and NetworkPolicy', link: '/lab/09-egress-and-network-policies' },
  { text: '10 — Probes, HPA, PDB, rollouts', link: '/lab/10-probes-scaling-and-rollouts' },
  { text: '11 — Debugging like an operator', link: '/lab/11-operations-troubleshooting' },
  { text: '12 — Job and CronJob', link: '/lab/12-job-and-cronjob' },
  { text: '13 — Cleanup, review, and what next', link: '/lab/13-cleanup-and-review' }
]

const architecture = [
  { text: '01 — The request lifecycle', link: '/architecture/01-request-lifecycle' },
  { text: '02 — What happens on kubectl apply', link: '/architecture/02-what-happens-on-apply' }
]

const reference = [
  { text: 'kubectl cheatsheet', link: '/reference/kubectl-cheatsheet' },
  { text: 'Glossary', link: '/reference/glossary' },
  { text: 'Interview questions', link: '/reference/interview-questions' }
]

const config = withMermaid({
  title: 'Kubernetes Practice Lab',
  description:
    'A self-paced Kubernetes course: read a concept lesson, then run the matching lab step on a real cluster.',
  lang: 'en-US',
  lastUpdated: true,

  vite: {
    optimizeDeps: {
      // mermaid 11 depends on fastdom, which is CommonJS. If Vite does not pre-bundle
      // these, the dev server serves the raw CJS files and the whole client bundle dies
      // with "does not provide an export named 'default'". Dev-only — the production
      // build is unaffected.
      include: ['fastdom', 'fastdom/fastdom.js', 'fastdom/extensions/fastdom-promised.js']
    }
  },

  themeConfig: {
    nav: [
      { text: 'Overview', link: '/overview' },
      { text: 'Getting started', link: '/getting-started/03-mental-model' },
      { text: 'Concepts', link: '/concepts/01-what-is-kubernetes' },
      { text: 'Lab', link: '/lab/' },
      { text: 'Architecture', link: '/architecture/01-request-lifecycle' },
      { text: 'Reference', link: '/reference/kubectl-cheatsheet' }
    ],

    sidebar: {
      '/getting-started/': [{ text: 'Getting started', items: gettingStarted }],
      '/concepts/': [{ text: 'Concepts', items: concepts }],
      '/lab/': [{ text: 'Lab — build TaskFlow', items: lab }],
      '/architecture/': [{ text: 'Architecture', items: architecture }],
      '/reference/': [{ text: 'Reference', items: reference }],
      // Fallback for pages outside the sections above (the course overview).
      '/': [
        { text: 'Course overview', items: [{ text: 'Course overview', link: '/overview' }] },
        { text: 'Getting started', items: gettingStarted },
        { text: 'Concepts', items: concepts },
        { text: 'Lab — build TaskFlow', items: lab },
        { text: 'Architecture', items: architecture },
        { text: 'Reference', items: reference }
      ]
    },

    search: { provider: 'local' },
    outline: { level: [2, 3], label: 'On this page' },

    editLink: {
      pattern: `${repo}/edit/main/docs/:path`,
      text: 'Edit this page on GitHub'
    },

    socialLinks: [{ icon: 'github', link: repo }],

    footer: {
      message: 'Built for learning Kubernetes by doing it — on a cluster you can throw away.',
      copyright: 'Practice Lab'
    },

    docFooter: { prev: 'Previous', next: 'Next' },
    returnToTopLabel: 'Back to top',
    sidebarMenuLabel: 'Menu',
    darkModeSwitchLabel: 'Appearance',
    lightModeSwitchTitle: 'Switch to light theme',
    darkModeSwitchTitle: 'Switch to dark theme'
  }
})

// The plugin's optimizeDeps list still carries `debug`, which mermaid 11 no longer
// depends on and which is therefore not installed; Vite warns about it on every dev start.
config.vite.optimizeDeps.include = config.vite.optimizeDeps.include.filter(
  (dep) => dep !== 'debug'
)

export default config
