import io


def test_list_jobs(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    names = [j["job_name"] for j in resp.json()["data"]]
    assert "ACE-DA-FIN-APS-INGEST-PRD" in names


def test_list_jobs_search(client):
    resp = client.get("/api/jobs?search=INGEST")
    assert resp.status_code == 200
    assert all("INGEST" in j["job_name"] for j in resp.json()["data"])


def test_list_jobs_includes_tables_mapped_count(client):
    """ACE-DA-FIN-APS-INGEST-PRD is seeded as controlm_pipeline_job on
    fin_aps_payment_stg -- tables_mapped should reflect real references,
    not just exist as a zeroed placeholder column."""
    resp = client.get("/api/jobs?search=ACE-DA-FIN-APS-INGEST-PRD")
    assert resp.status_code == 200
    jobs = resp.json()["data"]
    assert len(jobs) == 1
    assert jobs[0]["tables_mapped"] >= 1


def test_get_job_mapped_tables(client):
    """Backs the Job List 'Tables Mapped' drill-in popup -- ACE-DA-FIN-APS-
    INGEST-PRD is seeded as controlm_pipeline_job on fin_aps_payment_stg."""
    resp = client.get("/api/jobs/ACE-DA-FIN-APS-INGEST-PRD/tables")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) >= 1
    assert any(r["table_fqn"].endswith("fin_aps_payment_stg") for r in rows)


def test_get_job_mapped_tables_empty_for_unknown_job(client):
    resp = client.get("/api/jobs/NOT-A-REAL-JOB/tables")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_upsert_job(client):
    resp = client.post("/api/jobs", json={"job_name": "ACE-DA-TEST-NEW-PRD", "job_type": "controlm"})
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

    listed = client.get("/api/jobs?search=TEST-NEW").json()["data"]
    assert any(j["job_name"] == "ACE-DA-TEST-NEW-PRD" for j in listed)


def test_upsert_job_validation_error(client):
    resp = client.post("/api/jobs", json={})
    assert resp.status_code == 422


def test_import_jobs_round_trip(client):
    csv_bytes = b"job_name\nACE-DA-TEST-BULK-PRD\n\n"
    files = {"file": ("jobs.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/jobs/import", files=files)
    assert resp.status_code == 200
    assert resp.json()["data"]["imported"] == 1


def test_import_jobs_dry_run_previews_without_saving(client):
    csv_bytes = b"job_name,domain\nACE-DA-TEST-PREVIEW-PRD,finance\n"
    files = {"file": ("jobs.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/jobs/import?dry_run=true", files=files)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["imported"] == 0
    assert body["rows"][0]["job_name"] == "ACE-DA-TEST-PREVIEW-PRD"

    listed = client.get("/api/jobs?search=TEST-PREVIEW").json()["data"]
    assert listed == []


def test_import_jobs_real_apply_excludes_deselected_rows(client):
    csv_bytes = b"job_name\nACE-DA-TEST-KEEP-PRD\nACE-DA-TEST-DROP-PRD\n"
    files = {"file": ("jobs.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/jobs/import?dry_run=false&exclude=ACE-DA-TEST-DROP-PRD", files=files)
    assert resp.status_code == 200
    assert resp.json()["data"]["imported"] == 1

    names = [j["job_name"] for j in client.get("/api/jobs?search=ACE-DA-TEST-").json()["data"]]
    assert "ACE-DA-TEST-KEEP-PRD" in names
    assert "ACE-DA-TEST-DROP-PRD" not in names


def test_delete_job(client):
    client.post("/api/jobs", json={"job_name": "ACE-DA-TEST-DELETE-PRD"})
    resp = client.delete("/api/jobs/ACE-DA-TEST-DELETE-PRD")
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True
