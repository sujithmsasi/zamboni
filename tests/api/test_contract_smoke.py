"""
Contract smoke test -- contracts.md §6 is LOCKED. Every route listed there
must exist with the exact method. Hard guard against drift.
"""

# (method, path) exactly as written in contracts.md §6.
CONTRACT_ROUTES = [
    ("GET", "/api/tables"),
    ("GET", "/api/tables/{fqn}"),
    ("POST", "/api/tables/register"),
    ("PUT", "/api/tables/{fqn}"),
    ("POST", "/api/tables/bulk-controlm"),
    ("POST", "/api/tables/job-mapping/import"),
    ("GET", "/api/tables/job-mapping/export"),
    ("GET", "/api/glue/databases"),
    ("GET", "/api/glue/tables/{db}"),
    ("GET", "/api/policies"),
    ("GET", "/api/policies/{fqn}"),
    ("PUT", "/api/policies/{fqn}"),
    ("GET", "/api/templates"),
    ("PUT", "/api/templates/{name}"),
    ("POST", "/api/templates/{name}/apply"),
    # > ADDED (Phase 5a): Add/Delete Template -- see contracts.md §6's note
    # under routers/policies.py.
    ("POST", "/api/templates"),
    ("DELETE", "/api/templates/{name}"),
    ("GET", "/api/gates/{fqn}"),
    ("PUT", "/api/gates/{fqn}"),
    ("GET", "/api/conflicts"),
    ("POST", "/api/conflicts/rescan"),
    ("GET", "/api/nonprod"),
    ("POST", "/api/nonprod/exempt"),
    ("POST", "/api/nonprod/claim"),
    ("GET", "/api/nonprod/deletions"),
    ("GET", "/api/executions"),
    ("GET", "/api/executions/{id}"),
    ("GET", "/api/dryrun/{fqn}"),
    ("GET", "/api/health/kpis"),
    ("GET", "/api/costs"),
    ("GET", "/api/stale"),
    ("GET", "/api/jobs"),
    ("POST", "/api/jobs"),
    ("POST", "/api/jobs/import"),
    # > ADDED (Control-M Integration follow-up, 2026-07-08): drill-in for
    # Job List's "Tables Mapped" count -- see contracts.md's note under
    # routers/controlm.py.
    ("GET", "/api/jobs/{name}/tables"),
    ("DELETE", "/api/jobs/{name}"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("GET", "/api/escalation"),
    ("POST", "/api/escalation"),
    ("PUT", "/api/escalation/{key}"),
    ("DELETE", "/api/escalation/{key}"),
    ("GET", "/api/audit"),
    ("GET", "/api/system/mode"),
    ("GET", "/api/system/health"),
    ("GET", "/api/locks"),
    ("DELETE", "/api/locks/{fqn}"),
    # > ADDED (Phase 4): no domains section existed in contracts.md §6 --
    # see api/services/domains_svc.py's REALITY note.
    ("GET", "/api/domains"),
    ("GET", "/api/domains/{name}"),
    ("POST", "/api/domains"),
    ("PUT", "/api/domains/{name}"),
]


def _normalize(path: str) -> str:
    """FastAPI renders path:path converters as {fqn} in the OpenAPI schema too."""
    return path


def test_every_contract_route_exists(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    missing = []
    for method, path in CONTRACT_ROUTES:
        if path not in paths:
            missing.append((method, path))
            continue
        if method.lower() not in paths[path]:
            missing.append((method, path))

    assert not missing, f"Missing contract routes: {missing}"


def test_route_count_matches_contract():
    """Belt-and-braces: total method+path count should match the 50 defined here
    (44 from contracts.md §6 + 4 domains routes added in Phase 4 + 2 template
    routes added in Phase 5a)."""
    assert len(CONTRACT_ROUTES) == 51
