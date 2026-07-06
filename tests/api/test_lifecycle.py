def test_list_nonprod_envelope(client):
    resp = client.get("/api/nonprod?env=preprod&page=1&size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["page"] == 1


def test_list_nonprod_filtered_by_state(client):
    resp = client.get("/api/nonprod?env=preprod&state=ACTIVE&page=1&size=50")
    assert resp.status_code == 200
    assert all(row["lifecycle_state"] == "ACTIVE" for row in resp.json()["data"])


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
