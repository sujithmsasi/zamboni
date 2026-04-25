# Zamboni

**Iceberg Table Governance Framework** — automated housekeeping, archival,
and lifecycle management for Apache Iceberg tables at enterprise scale.

---

## Three Engines

| Engine | Purpose | Trigger |
|---|---|---|
| **HK Engine** | Compaction, snapshot expiry, orphan file cleanup | Post-batch via Control-M |
| **Archival Engine** | Export-then-delete cold staging partitions to S3 Intelligent-Tiering | Weekly |
| **Lifecycle Engine** | Auto-discover and clean up stale non-prod tables | Weekly |

---

## Repo Structure

```
zamboni/
├── engine/          ← Framework — runs on EC2 via Control-M
│   ├── core/        ← Registry, config, health, window evaluator, circuit breaker
│   ├── engines/     ← HK, Archival, Lifecycle engine classes
│   ├── operations/  ← Compaction, vacuum, archival, catalog cleanup
│   ├── strategies/  ← Binpack, sort, zorder
│   ├── utils/       ← Athena, S3, Glue clients + logger
│   ├── scripts/     ← Control-M entry points
│   └── cli/         ← Helper tools for engineers
├── app/             ← Streamlit UI — systemd service on same EC2
│   ├── pages/       ← One file per module
│   └── components/  ← Reusable UI components
├── sql/             ← Athena DDL
├── config/          ← Settings, policy templates, domain retention
├── tests/           ← Unit + integration tests
├── deploy/          ← CI/CD, CodeDeploy hooks, IAM policy
└── docs/            ← Design documents
```

---

## Environment

| Property | Value |
|---|---|
| AWS Region | us-west-2 |
| Athena Catalog | glue_catalog |
| Metadata Database | zamboni_catalog |
| Layers | staging · datalake · base · master |

## Athena Workgroups

| Workgroup | Purpose |
|---|---|
| `zamboni-critical` | Critical tier HK operations |
| `zamboni-standard` | Standard tier HK operations |
| `zamboni-low` | Low priority HK operations |
| `zamboni-archival` | Archival Engine |
| `zamboni-app` | Streamlit app queries |

---

## Quick Start

```bash
git clone https://github.com/sujithmsasi/zamboni.git
cd zamboni && git checkout dev
pip install -r requirements.txt
cp .env.example .env          # fill in your values
pytest tests/unit/ -v         # all should pass, no AWS needed
```

---

## Build Status

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation — structure, SQL, config, utils | 🔄 |
| 2 | CI/CD — GitHub Actions + CodePipeline + CodeDeploy | ⏳ |
| 3 | Core Layer | ⏳ |
| 4 | HK Engine | ⏳ |
| 5 | Archival Engine | ⏳ |
| 6 | Lifecycle Engine | ⏳ |
| 7 | Streamlit App | ⏳ |
| 8 | CLI Tools | ⏳ |
| 9 | Hardening | ⏳ |
