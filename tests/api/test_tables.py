import io


def test_list_tables_envelope(client):
    resp = client.get("/api/tables?page=1&size=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["pagination"] == {"page": 1, "size": 5, "total": body["pagination"]["total"]}
    assert len(body["data"]) <= 5


def test_list_tables_filtered_by_domain(client):
    resp = client.get("/api/tables?page=1&size=50&domain=finance")
    assert resp.status_code == 200
    body = resp.json()
    assert all(row["domain"] == "finance" for row in body["data"])


def test_get_table_404(client):
    resp = client.get("/api/tables/glue_catalog.nope.does_not_exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["data"] is None
    assert body["error"]["code"] == "404"


def test_get_table_found(client, a_table_fqn):
    resp = client.get(f"/api/tables/{a_table_fqn}")
    assert resp.status_code == 200
    assert resp.json()["data"]["table_fqn"] == a_table_fqn


def test_register_table_dry_run(client):
    # NOTE: engine.utils.athena_client.run_query() dispatches to SQLite in
    # ZAMBONI_LOCAL_MODE before checking dry_run at all (pre-existing engine
    # behavior, not introduced by this phase -- see Migration Progress
    # "Found, not fixed"). So in local mode a dry_run write still lands;
    # what's verifiable here is the envelope shape and the DRY_RUN audit
    # trail, not data immutability.
    payload = {
        "table_fqn": "glue_catalog.finance_staging_db.zamboni_api_test_tbl",
        "domain": "finance", "layer": "staging", "tier": "standard",
        "dry_run": True,
    }
    resp = client.post("/api/tables/register", json=payload)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["success"] is True
    assert body["dry_run"] is True
    assert body["audit_id"]

    audit_rows = client.get("/api/audit?page=1&size=10&action=table_register").json()["data"]
    assert any(row["audit_id"] == body["audit_id"] and row["status"] == "DRY_RUN" for row in audit_rows)


def test_register_table_validation_error(client):
    resp = client.post("/api/tables/register", json={"table_fqn": "x"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["data"] is None
    assert body["error"]["code"] == "422"


def test_update_table_dry_run(client, a_table_fqn):
    resp = client.put(f"/api/tables/{a_table_fqn}", json={"owner_email": "new@example.com", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_bulk_controlm_dry_run(client):
    resp = client.post("/api/tables/bulk-controlm", json={
        "filters": {"domain": "finance"},
        "set_fields": {"controlm_pipeline_job": "ACE-DA-FIN-TEST-PRD"},
        "dry_run": True,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_job_mapping_export(client):
    resp = client.get("/api/tables/job-mapping/export")
    assert resp.status_code == 200
    assert "domain" in resp.json()["data"]["csv"]


def test_job_mapping_import_round_trip(client):
    csv_bytes = (
        b"domain,controlm_job_name\n"
        b"finance,ACE-DA-FIN-APS-TEST-PRD\n"
        b"\n"
    )
    files = {"file": ("mapping.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/tables/job-mapping/import?dry_run=true", files=files)
    assert resp.status_code == 200
    rows = resp.json()["data"]["rows"]
    assert len(rows) == 1  # blank line stripped
    assert rows[0]["domain"] == "finance"


def test_glue_databases(client):
    resp = client.get("/api/glue/databases")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)


def test_register_table_auto_applies_template(client):
    """Browse & Register tab parity: registering infers + applies a policy
    template in the same call (2_Table_Registration.py calls both
    registry.register_table() and apply_template() per row)."""
    payload = {
        "table_fqn": "glue_catalog.finance_staging_db.zamboni_api_test_tmpl",
        "domain": "finance", "layer": "staging", "tier": "critical",
        "dry_run": True,
    }
    resp = client.post("/api/tables/register", json=payload)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["template"] == "CRITICAL_HIGH_VOL"  # infer_template(staging, critical)


def test_list_tables_filtered_by_database_name(client, a_table_fqn):
    db_name = a_table_fqn.split(".")[1]
    resp = client.get(f"/api/tables?page=1&size=50&database_name={db_name}")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body) > 0
    assert all(f".{db_name}." in row["table_fqn"] for row in body)


def test_bulk_controlm_engine_flags(client):
    """Engine Flags 'Bulk Apply' sub-tab reuses bulk-controlm's filter+set
    mechanism for hk/archive/lifecycle_enabled rather than a second endpoint."""
    resp = client.post("/api/tables/bulk-controlm", json={
        "filters": {"domain": "finance"},
        "set_fields": {"hk_enabled": True, "archive_enabled": False},
        "dry_run": True,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True
    assert resp.json()["data"]["affected"] > 0


def test_bulk_controlm_exclude_fqns(client, a_table_fqn):
    resp_all = client.post("/api/tables/bulk-controlm", json={
        "filters": {}, "set_fields": {"ci_number": "CI-EXCL-TEST"}, "dry_run": True,
    })
    total = resp_all.json()["data"]["affected"]

    resp_excl = client.post("/api/tables/bulk-controlm", json={
        "filters": {"exclude_fqns": [a_table_fqn]},
        "set_fields": {"ci_number": "CI-EXCL-TEST"},
        "dry_run": True,
    })
    assert resp_excl.status_code == 200
    assert resp_excl.json()["data"]["affected"] == total - 1
