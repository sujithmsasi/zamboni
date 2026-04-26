# Zamboni

**Iceberg Table Governance Framework** — automated housekeeping, archival,
and lifecycle management for Apache Iceberg tables at enterprise scale.

---

## Running the App

```bash
# From the project root
streamlit run app/Home.py --server.port 8501
```

> The entrypoint is `app/_main.py`. The `_` prefix hides it from Streamlit's
> page navigation while still allowing it to be used as the entrypoint.

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
│   ├── Home.py      ← Entry point (run: streamlit run app/Home.py)
│   ├── pages/       ← One file per module
│   ├── components/  ← Reusable UI components
│   └── assets/      ← Logo files (da_logo.png, da_logo_small.png)
├── glue_jobs/       ← PySpark Glue job for sort/zorder compaction
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

---

## Quick Start

```bash
git clone https://github.com/sujithmsasi/zamboni.git
cd zamboni && git checkout dev
pip install -r requirements-dev.txt
cp .env.example .env          # fill in your values

# Run unit tests
python -m pytest tests/unit/ -v --override-ini="addopts="

# Run Streamlit app
streamlit run app/Home.py --server.port 8501
```

---

## D&A Logo

Place your logo files at:
- `app/assets/da_logo.png`       — 400×80px, used in page header
- `app/assets/da_logo_small.png` — 120×40px, used in sidebar

---

## Build Status

| Phase | Description | Status |
|---|---|---|
| 1 | Foundation | ✅ |
| 2 | CI/CD | ✅ |
| 3 | Core Layer | ✅ |
| 4 | HK Engine | ✅ |
| 5 | Archival Engine | ✅ |
| 6 | Lifecycle Engine | ✅ |
| 7 | Streamlit App | ✅ |
| 8 | CLI Tools | ⏳ |
| 9 | Hardening | ⏳ |
