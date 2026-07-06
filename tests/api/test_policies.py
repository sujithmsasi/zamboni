def test_list_policies_envelope(client):
    resp = client.get("/api/policies?page=1&size=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["pagination"]["page"] == 1


def test_list_policies_filtered(client):
    resp = client.get("/api/policies?page=1&size=50&domain=finance")
    assert resp.status_code == 200
    assert all(row["domain"] == "finance" for row in resp.json()["data"])


def test_get_policy_404(client):
    resp = client.get("/api/policies/glue_catalog.nope.nope")
    assert resp.status_code == 404


def test_get_policy_found(client, a_table_fqn):
    resp = client.get(f"/api/policies/{a_table_fqn}")
    assert resp.status_code == 200


def test_update_policy_dry_run(client, a_table_fqn):
    resp = client.put(f"/api/policies/{a_table_fqn}", json={
        "snapshot_retention_days": 10, "override_notes": "test override", "dry_run": True,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_list_templates(client):
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], dict)
    assert len(resp.json()["data"]) > 0


def test_update_template_dry_run(client):
    before = client.get("/api/templates").json()["data"]["STAGING_DEFAULT"]["description"]
    resp = client.put("/api/templates/STAGING_DEFAULT", json={"description": "updated", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True

    # unlike run_query()'s local-mode gap, template writes go straight to
    # disk in policies_svc.update_template() -- dry_run here is genuinely
    # honored and verifiable.
    after = client.get("/api/templates").json()["data"]["STAGING_DEFAULT"]["description"]
    assert after == before
    assert resp.json()["data"]["success"] is True


def test_update_template_not_found(client):
    resp = client.put("/api/templates/DOES_NOT_EXIST", json={"description": "x", "dry_run": True})
    assert resp.status_code == 404


def test_apply_template_dry_run(client):
    resp = client.post("/api/templates/STAGING_DEFAULT/apply", json={"domain": "finance", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True
