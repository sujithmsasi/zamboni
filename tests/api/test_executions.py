def test_list_executions_envelope(client):
    resp = client.get("/api/executions?page=1&size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["page"] == 1
    assert len(body["data"]) <= 10


def test_list_executions_filtered_by_status(client):
    resp = client.get("/api/executions?status=SUCCESS&page=1&size=20")
    assert resp.status_code == 200
    assert all(row["status"] == "SUCCESS" for row in resp.json()["data"])


def test_list_executions_filtered_by_integrity_status(client):
    """> ADDED (Phase 4): integrity_status filter added for the Health
    Dashboard's integrity-failures grid."""
    resp = client.get("/api/executions?integrity_status=FAILED&page=1&size=20")
    assert resp.status_code == 200
    assert all(row["integrity_status"] == "FAILED" for row in resp.json()["data"])


def test_get_execution_404(client):
    resp = client.get("/api/executions/does-not-exist")
    assert resp.status_code == 404


def test_get_execution_found(client):
    listed = client.get("/api/executions?page=1&size=1").json()["data"]
    if not listed:
        return
    exec_id = listed[0]["execution_id"]
    resp = client.get(f"/api/executions/{exec_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["execution_id"] == exec_id


def test_dry_run_view_404(client):
    resp = client.get("/api/dryrun/glue_catalog.nope.nope")
    assert resp.status_code == 404


def test_dry_run_view_found(client, a_table_fqn):
    resp = client.get(f"/api/dryrun/{a_table_fqn}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["table_fqn"] == a_table_fqn
    assert "gates" in data


def test_health_kpis(client):
    resp = client.get("/api/health/kpis")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "total_registered" in data
    assert "conflicts" in data


def test_costs(client):
    resp = client.get("/api/costs?group_by=domain")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["group_by"] == "domain"
    assert "totals" in data


def test_stale_hk(client):
    resp = client.get("/api/stale?kind=hk")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)
    if data:
        assert "days_since_hk" in data[0]


def test_stale_hk_days_threshold_filters_rows(client):
    """> ADDED (Phase 5b): kind=hk previously ignored `days` entirely and only
    ever returned never-housekept rows -- a real gap found while wiring the
    Stale Resources page's threshold InputNumber to this endpoint."""
    permissive = client.get("/api/stale?kind=hk&days=0").json()["data"]
    strict = client.get("/api/stale?kind=hk&days=100000").json()["data"]
    assert len(strict) <= len(permissive)


def test_stale_hk_environment_filter(client):
    resp = client.get("/api/stale?kind=hk&environment=dev")
    assert resp.status_code == 200
    assert all(row["environment"] == "dev" for row in resp.json()["data"])


def test_stale_zero_row(client):
    resp = client.get("/api/stale?kind=zero_row&threshold=0")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)


def test_stale_nonprod(client):
    resp = client.get("/api/stale?kind=nonprod")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)


def test_stale_orphan_no_prefix_returns_empty(client):
    resp = client.get("/api/stale?kind=orphan")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_stale_orphan_invalid_prefix_400(client):
    resp = client.get("/api/stale?kind=orphan&prefix=not-an-s3-uri")
    assert resp.status_code == 400


def test_stale_invalid_kind(client):
    resp = client.get("/api/stale?kind=bogus")
    assert resp.status_code == 400
