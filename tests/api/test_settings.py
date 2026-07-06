def test_get_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert "execution_log_retention_days" in resp.json()["data"]


def test_update_settings_dry_run(client):
    settings = client.get("/api/settings").json()["data"]
    resp = client.put("/api/settings", json={
        "settings": {**settings, "budget_alert_threshold_usd_monthly": 5000},
        "dry_run": True,
    })
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["dry_run"] is True
    assert body["audit_id"]


def test_update_settings_validation_error(client):
    resp = client.put("/api/settings", json={})
    assert resp.status_code == 422


def test_escalation_crud_dry_run(client):
    resp = client.post("/api/escalation", json={
        "key": "domain:finance", "entry": {"primary_owner_email": "x@example.com"}, "dry_run": True,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["audit_id"]

    resp = client.put("/api/escalation/domain:finance", json={
        "key": "domain:finance", "entry": {"primary_owner_email": "y@example.com"}, "dry_run": True,
    })
    assert resp.status_code == 200

    resp = client.delete("/api/escalation/domain:finance?dry_run=true")
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_list_escalation(client):
    resp = client.get("/api/escalation")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)


def test_list_audit(client):
    resp = client.get("/api/audit?page=1&size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["page"] == 1
    assert len(body["data"]) <= 10
