def test_list_domains(client):
    resp = client.get("/api/domains")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert "table_count" in data[0]


def test_get_domain_404(client):
    resp = client.get("/api/domains/nope-domain")
    assert resp.status_code == 404


def test_get_domain_found(client):
    domain_name = client.get("/api/domains").json()["data"][0]["domain_name"]
    resp = client.get(f"/api/domains/{domain_name}")
    assert resp.status_code == 200
    assert resp.json()["data"]["domain_name"] == domain_name


def test_create_domain_dry_run(client):
    resp = client.post("/api/domains", json={
        "domain_name": "test-newdomain",
        "display_name": "Test New Domain",
        "owner_email": "da-test@company.com",
        "dry_run": True,
    })
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["dry_run"] is True
    assert body["audit_id"]


def test_create_domain_requires_fields(client):
    resp = client.post("/api/domains", json={
        "domain_name": "",
        "display_name": "",
        "owner_email": "",
        "dry_run": True,
    })
    assert resp.status_code == 400


def test_update_domain_dry_run(client):
    domain_name = client.get("/api/domains").json()["data"][0]["domain_name"]
    resp = client.put(f"/api/domains/{domain_name}", json={"notes": "updated via test", "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["dry_run"] is True


def test_update_domain_404(client):
    resp = client.put("/api/domains/nope-domain", json={"notes": "x", "dry_run": True})
    assert resp.status_code == 404
