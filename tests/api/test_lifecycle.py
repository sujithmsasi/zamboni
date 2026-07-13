def test_list_nonprod_envelope(client):
    resp = client.get("/api/nonprod?env=preprod&page=1&size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["page"] == 1


def test_list_nonprod_filtered_by_state(client):
    resp = client.get("/api/nonprod?env=preprod&state=ACTIVE&page=1&size=50")
    assert resp.status_code == 200
    assert all(row["lifecycle_state"] == "ACTIVE" for row in resp.json()["data"])


def test_list_nonprod_no_env_returns_all_environments(client):
    """The environment dropdown was removed from the Non-Prod Lifecycle
    page (single-AWS-account-per-environment deployments never had more
    than one real value to switch between) -- env is now optional, and
    omitting it must return every environment's rows, not silently fall
    back to one. The seeded demo data spans dev/preprod/test."""
    unfiltered = client.get("/api/nonprod?page=1&size=250").json()
    preprod_only = client.get("/api/nonprod?env=preprod&page=1&size=250").json()
    assert unfiltered["pagination"]["total"] >= preprod_only["pagination"]["total"]
    environments = {row["environment"] for row in unfiltered["data"]}
    assert len(environments) > 1


def _first_nonprod_fqn(client, env="preprod") -> str | None:
    resp = client.get(f"/api/nonprod?env={env}&page=1&size=1")
    data = resp.json()["data"]
    return data[0]["table_fqn"] if data else None


def test_exempt_dry_run(client):
    fqn = _first_nonprod_fqn(client)
    if not fqn:
        return
    resp = client.post("/api/nonprod/exempt", json={"fqns": [fqn], "reason": "quarterly review", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_exempt_validation_error(client):
    resp = client.post("/api/nonprod/exempt", json={"fqns": ["x"]})
    assert resp.status_code == 422


def test_claim_dry_run(client):
    fqn = _first_nonprod_fqn(client)
    if not fqn:
        return
    resp = client.post("/api/nonprod/claim", json={"fqns": [fqn], "reason": "taking ownership", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_list_deletions(client):
    resp = client.get("/api/nonprod/deletions?env=preprod&page=1&size=10")
    assert resp.status_code == 200
    assert resp.json()["pagination"]["page"] == 1


def test_list_deletions_no_env_returns_all_environments(client):
    """Same env-optional change as test_list_nonprod_no_env_returns_all_environments,
    for the Deletion History tab."""
    unfiltered = client.get("/api/nonprod/deletions?page=1&size=250").json()
    preprod_only = client.get("/api/nonprod/deletions?env=preprod&page=1&size=250").json()
    assert unfiltered["pagination"]["total"] >= preprod_only["pagination"]["total"]
    for row in unfiltered["data"]:
        assert "environment" in row


def test_lifecycle_config(client):
    """> ADDED (Phase 5b): thresholds sourced from lifecycle_engine constants,
    not hardcoded client-side."""
    resp = client.get("/api/lifecycle/config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data == {"stale_days": 60, "greenzone_days": 14, "pending_drop_days": 2}
