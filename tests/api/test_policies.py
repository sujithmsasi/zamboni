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


def test_list_policies_search_by_table_name(client, a_table_fqn):
    fragment = a_table_fqn.split(".")[-1][:6]
    resp = client.get(f"/api/policies?page=1&size=50&search={fragment}")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) >= 1
    assert all(fragment in row["table_fqn"] for row in rows)


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


def test_apply_template_skips_manually_overridden_tables_by_default(client, a_table_fqn):
    """Bulk Apply's 'Override existing manual overrides' checkbox, unchecked
    by default -- a manually-overridden table should not be silently
    reset by a bulk template apply unless skip_overridden=False."""
    from config.settings import HK_CONFIG_TABLE, STREAM_REGISTRY_TABLE
    from engine.utils.athena_client import read_sql, run_query

    domain = read_sql(
        f"SELECT domain, layer FROM {STREAM_REGISTRY_TABLE} WHERE table_fqn = '{a_table_fqn}'", workgroup="app",
    ).iloc[0]
    run_query(
        f"UPDATE {HK_CONFIG_TABLE} SET manually_overridden = 1 WHERE table_fqn = '{a_table_fqn}'",
        workgroup="app", dry_run=False,
    )
    try:
        resp = client.post("/api/templates/STAGING_DEFAULT/apply", json={
            "domain": domain["domain"], "layer": domain["layer"], "skip_overridden": True, "dry_run": True,
        })
        assert resp.status_code == 200
        skip_count = resp.json()["data"]["affected"]

        resp2 = client.post("/api/templates/STAGING_DEFAULT/apply", json={
            "domain": domain["domain"], "layer": domain["layer"], "skip_overridden": False, "dry_run": True,
        })
        no_skip_count = resp2.json()["data"]["affected"]
        assert no_skip_count > skip_count
    finally:
        run_query(
            f"UPDATE {HK_CONFIG_TABLE} SET manually_overridden = 0 WHERE table_fqn = '{a_table_fqn}'",
            workgroup="app", dry_run=False,
        )


def test_create_template_dry_run(client):
    resp = client.post("/api/templates", json={"name": "ZAMBONI_TEST_TMPL", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True
    assert "ZAMBONI_TEST_TMPL" not in client.get("/api/templates").json()["data"]


def test_create_template_duplicate_rejected(client):
    resp = client.post("/api/templates", json={"name": "STAGING_DEFAULT", "dry_run": True})
    assert resp.status_code == 400


def test_create_template_then_delete_round_trip(client):
    create = client.post("/api/templates", json={
        "name": "ZAMBONI_TEST_ROUNDTRIP", "compaction_strategy": "sort",
        "compaction_engine": "glue", "dry_run": False,
    })
    assert create.status_code == 200
    templates = client.get("/api/templates").json()["data"]
    assert templates["ZAMBONI_TEST_ROUNDTRIP"]["compaction_strategy"] == "sort"

    delete = client.delete("/api/templates/ZAMBONI_TEST_ROUNDTRIP?dry_run=false")
    assert delete.status_code == 200
    assert "ZAMBONI_TEST_ROUNDTRIP" not in client.get("/api/templates").json()["data"]


def test_delete_builtin_template_blocked(client):
    resp = client.delete("/api/templates/STAGING_DEFAULT?dry_run=true")
    assert resp.status_code == 400


def test_delete_template_in_use_blocked(client, a_table_fqn):
    resp = client.post("/api/templates", json={"name": "ZAMBONI_TEST_INUSE", "dry_run": False})
    assert resp.status_code == 200
    try:
        from config.settings import HK_CONFIG_TABLE
        from engine.utils.athena_client import run_query
        run_query(
            f"UPDATE {HK_CONFIG_TABLE} SET policy_template = 'ZAMBONI_TEST_INUSE' "
            f"WHERE table_fqn = '{a_table_fqn}'",
            workgroup="app", dry_run=False,
        )
        resp = client.delete("/api/templates/ZAMBONI_TEST_INUSE?dry_run=true")
        assert resp.status_code == 400
    finally:
        run_query(
            f"UPDATE {HK_CONFIG_TABLE} SET policy_template = 'STAGING_DEFAULT' "
            f"WHERE table_fqn = '{a_table_fqn}'",
            workgroup="app", dry_run=False,
        )
        client.delete("/api/templates/ZAMBONI_TEST_INUSE?dry_run=false")
