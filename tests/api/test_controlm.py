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


def test_delete_job(client):
    client.post("/api/jobs", json={"job_name": "ACE-DA-TEST-DELETE-PRD"})
    resp = client.delete("/api/jobs/ACE-DA-TEST-DELETE-PRD")
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True
