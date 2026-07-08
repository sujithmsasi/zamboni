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


def test_job_mapping_import_blank_table_pattern_column_still_matches(client):
    """
    Regression test: a table_pattern column that's PRESENT but blank on every
    row reads back from pandas as all-NaN float64 rather than empty strings
    -- unlike an entirely absent column, which goes through the "fill missing
    column with a default" path instead. This is exactly what a real
    Export Mapping Template download looks like (table_pattern is always
    present, always blank, for domain teams to optionally fill in), so a
    naive re-upload of the template with no edits must still match real
    tables, not silently produce tables_matched=0 for every row.
    """
    csv_bytes = (
        b"domain,layer,database_name,table_pattern,controlm_job_name\n"
        b"finance,staging,finance_staging_db,,ACE-DA-FIN-APS-TEST-PRD\n"
    )
    files = {"file": ("mapping.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/tables/job-mapping/import?dry_run=true", files=files)
    assert resp.status_code == 200
    rows = resp.json()["data"]["rows"]
    assert len(rows) == 1
    assert rows[0]["table_pattern"] == ""
    assert rows[0]["tables_matched"] > 0


def test_job_mapping_import_real_apply_registers_job_in_registry(client):
    """Same regression as test_bulk_controlm_registers_job_in_registry, for
    the Import Job Mapping path -- see that test's docstring."""
    job_name = "ACE-TEST-IMPORT-REGISTRY-PRD"
    csv_bytes = (
        f"domain,layer,database_name,table_pattern,controlm_job_name\n"
        f"finance,staging,finance_staging_db,,{job_name}\n"
    ).encode()
    files = {"file": ("mapping.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/tables/job-mapping/import?dry_run=false", files=files)
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["tables_matched"] > 0

    jobs_resp = client.get(f"/api/jobs?search={job_name}")
    assert jobs_resp.status_code == 200
    names = [j["job_name"] for j in jobs_resp.json()["data"]]
    assert job_name in names


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


def test_bulk_controlm_registers_job_in_registry(client, a_table_fqn):
    """
    Regression test: Manual Bulk Apply and Import Job Mapping only ever
    wrote controlm_pipeline_job/controlm_hk_job onto matched stream_registry
    rows -- they never touched controlm_jobs, so a job applied through
    either flow never showed up in the Control-M Job Registry grid. A real
    (non-dry-run) apply should now auto-register the job name.
    """
    job_name = "ACE-TEST-BULK-REGISTRY-PRD"
    table_pattern = a_table_fqn.split(".")[-1]
    resp = client.post("/api/tables/bulk-controlm", json={
        "filters": {"pattern": table_pattern},
        "set_fields": {"controlm_pipeline_job": job_name, "dependent_job_type": "glue"},
        "dry_run": False,
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["affected"] >= 1

    jobs_resp = client.get(f"/api/jobs?search={job_name}")
    assert jobs_resp.status_code == 200
    names = [j["job_name"] for j in jobs_resp.json()["data"]]
    assert job_name in names
