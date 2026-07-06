from datetime import UTC, datetime, timedelta


def test_get_gates_404(client):
    resp = client.get("/api/gates/glue_catalog.nope.nope")
    assert resp.status_code == 404


def test_get_gates_found(client, a_table_fqn):
    resp = client.get(f"/api/gates/{a_table_fqn}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "gate1_enabled" in data
    assert "conflict_cache" in data


def test_update_gates_toggle_dry_run(client, a_table_fqn):
    resp = client.put(f"/api/gates/{a_table_fqn}", json={"gate2_enabled": False, "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_update_gates_override_requires_reason(client, a_table_fqn):
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = client.put(f"/api/gates/{a_table_fqn}", json={"gate0_override_until": until, "dry_run": True})
    assert resp.status_code == 400


def test_update_gates_override_capped(client, a_table_fqn):
    """override_until far beyond GATE0_OVERRIDE_MAX_HOURS must be clamped, not rejected."""
    from config.settings import GATE0_OVERRIDE_MAX_HOURS

    far_future = (datetime.now(UTC) + timedelta(hours=GATE0_OVERRIDE_MAX_HOURS * 5)).isoformat()
    resp = client.put(f"/api/gates/{a_table_fqn}", json={
        "gate0_override_until": far_future, "gate0_override_reason": "emergency maintenance", "dry_run": False,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

    gates = client.get(f"/api/gates/{a_table_fqn}").json()["data"]
    clamped = datetime.fromisoformat(gates["gate0_override_until"].replace("Z", "+00:00"))
    if clamped.tzinfo is None:
        clamped = clamped.replace(tzinfo=UTC)
    assert clamped <= datetime.now(UTC) + timedelta(hours=GATE0_OVERRIDE_MAX_HOURS, minutes=1)


def test_conflicts_envelope(client):
    resp = client.get("/api/conflicts?page=1&size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["page"] == 1


def test_conflicts_rescan(client, a_table_fqn):
    resp = client.post("/api/conflicts/rescan", json={"fqns": [a_table_fqn]})
    assert resp.status_code == 200
    assert resp.json()["data"]["scanned"] == 1
