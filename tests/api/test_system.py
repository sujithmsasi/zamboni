def test_system_mode(client):
    resp = client.get("/api/system/mode")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["mode"] == "local"
    assert "app_env" in body
    assert "dry_run_default" in body


def test_system_health(client):
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["db"] == "ok"
    assert body["engine"] == "ok"


def test_list_locks_empty(client):
    resp = client.get("/api/locks")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_lock_force_release(client, a_table_fqn):
    from engine.core.lock_service import LockService

    svc = LockService(mode="local")
    lock = svc.acquire(a_table_fqn, "test_operation")
    assert lock is not None

    listed = client.get("/api/locks").json()["data"]
    assert any(row["table_fqn"] == a_table_fqn for row in listed)

    resp = client.delete(f"/api/locks/{a_table_fqn}")
    assert resp.status_code == 200
    assert resp.json()["data"]["released"] is True

    listed_after = client.get("/api/locks").json()["data"]
    assert not any(row["table_fqn"] == a_table_fqn for row in listed_after)
