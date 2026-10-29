from concurrent.futures import ThreadPoolExecutor
import secrets

import pytest
from fastapi.testclient import TestClient

from demo_server.app import create_app
from demo_server.store import Store, Limits, Rejected

SECRET = "server-secret-for-automated-tests-only-12345"
WORKER = "test-worker-1234567890"


def reserve(store, size=5):
    token = secrets.token_urlsafe(32)
    task = store.reserve(secrets.token_urlsafe(32), token, size)
    return task, token


def enqueue(store):
    task, token = reserve(store)
    store.begin_upload(task["id"], token)
    store.path(task["id"]).with_suffix(".part").write_bytes(b"video")
    store.finish_upload(task["id"])
    return task, token


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path, Limits(disk_floor_bytes=0))
    store.heartbeat(WORKER, True)
    return store


def test_offline_preparing_and_idempotent_reservation(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(Rejected) as error:
        reserve(store)
    assert error.value.code == "offline"
    store.heartbeat(WORKER, False)
    with pytest.raises(Rejected) as error:
        reserve(store)
    assert error.value.code == "preparing"
    store.heartbeat(WORKER, True)
    attempt, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    first = store.reserve(attempt, token, 5)
    assert store.reserve(attempt, token, 5)["id"] == first["id"]
    with pytest.raises(Rejected) as error:
        store.reserve(attempt, "another-token", 5)
    assert error.value.status == 404


def test_concurrent_upload_reservations_never_exceed_two(store):
    def submit(_):
        try:
            return reserve(store)
        except Rejected:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = [r for r in pool.map(submit, range(24)) if r]
    assert len(accepted) == 2
    assert store.status()["uploading"] == 2


def test_queue_counts_uploads_and_processing_slot_is_atomic(store):
    store.limits.max_estimated_wait_seconds = 3600  # Exercise the independent hard cap.
    queued = [enqueue(store) for _ in range(9)]
    reserve(store)
    with pytest.raises(Rejected) as error:
        reserve(store)
    assert error.value.code == "queue_full"
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim(WORKER), range(8)))
    assert len([c for c in claims if c]) == 1
    assert store.status()["processing"] == 1
    # Processing no longer occupies waiting capacity.
    reserve(store)
    assert store.status()["queued"] == len(queued) - 1


def test_quota_and_failed_upload_release(store):
    store.limits.quota_bytes = 7
    task, _ = reserve(store, 5)
    with pytest.raises(Rejected) as error:
        reserve(store, 5)
    assert error.value.code == "storage_full"
    store.fail_upload(task["id"], "upload_failed")
    reserve(store, 5)


def test_isolation_persistence_video_removal_and_ack(store):
    task, token = enqueue(store)
    task_id = task["id"]
    with pytest.raises(Rejected) as error:
        store.get(task_id, "wrong-token")
    assert error.value.status == 404
    claim = store.claim(WORKER)
    store.complete(task_id, claim["lease"], "测试文字", "")
    assert not store.path(task_id).exists()
    assert store.get(task_id, token)["text"] == "测试文字"
    store.complete(task_id, claim["lease"], "测试文字", "")
    with pytest.raises(Rejected):
        store.complete(task_id, claim["lease"], "覆盖原文", "")
    # Confirm real disk persistence by opening another store; terminal result survives.
    reloaded = Store(store.root)
    assert reloaded.get(task_id, token)["text"] == "测试文字"
    reloaded.ack(task_id, token)
    with pytest.raises(Rejected):
        reloaded.get(task_id, token)
    with reloaded.db() as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row["text"] == row["token"] == row["lease"] == ""


def test_timeout_stale_result_and_result_expiry(store):
    now = [store.clock()]
    store.clock = lambda: now[0]
    task, token = enqueue(store)
    claim = store.claim(WORKER)
    now[0] += store.limits.process_seconds + 1
    store.cleanup()
    assert store.get(task["id"], token)["code"] == "processing_timeout"
    assert not store.path(task["id"]).exists()
    with pytest.raises(Rejected):
        store.complete(task["id"], claim["lease"], "迟到结果", "")
    now[0] += store.limits.result_seconds + 1
    with pytest.raises(Rejected):
        store.get(task["id"], token)


def test_restart_partial_upload_processing_and_orphan_cleanup(store):
    processing, token = enqueue(store)
    claim = store.claim(WORKER)
    partial, other_token = reserve(store)
    store.path(partial["id"]).with_suffix(".part").write_bytes(b"incomplete")
    queued, queued_token = enqueue(store)
    (store.video_root / "orphan.video").write_bytes(b"orphan")
    restarted = Store(store.root)
    assert restarted.get(processing["id"], token)["code"] == "service_restarted"
    assert restarted.get(partial["id"], other_token)["code"] == "service_restarted"
    assert restarted.get(queued["id"], queued_token)["state"] == "queued"
    assert len(list(store.video_root.iterdir())) == 1
    with pytest.raises(Rejected):
        restarted.complete(processing["id"], claim["lease"], "stale", "")


def test_http_auth_real_video_validation_and_delivery(tmp_path):
    from .video_fixture import video_bytes

    app = create_app(tmp_path, SECRET)
    worker_headers = {"Authorization": f"Bearer {SECRET}"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/worker/heartbeat", json={"worker": WORKER, "ready": True}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/worker/heartbeat",
                headers=worker_headers,
                json={"worker": WORKER, "ready": True},
            ).status_code
            == 200
        )
        token, attempt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        video = video_bytes(tmp_path / "valid.webm")
        task = client.post(
            "/api/tasks", json={"attempt": attempt, "token": token, "size": len(video)}
        ).json()
        browser_headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/tasks/" + task["id"]).status_code == 404
        result = client.put(
            f"/api/tasks/{task['id']}/video", headers=browser_headers, content=video
        )
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "queued"
        claim = client.post(
            "/api/worker/claim",
            headers=worker_headers,
            json={"worker": WORKER, "ready": True},
        ).json()["task"]
        headers = dict(worker_headers, **{"X-Task-Lease": claim["lease"]})
        assert (
            client.get(f"/api/worker/tasks/{task['id']}/video", headers=headers).content
            == video
        )
        assert (
            client.post(
                f"/api/worker/tasks/{task['id']}/result",
                headers=headers,
                json={"text": "HTTP 流程测试", "code": ""},
            ).status_code
            == 200
        )
        assert (
            client.get("/api/tasks/" + task["id"], headers=browser_headers).json()[
                "text"
            ]
            == "HTTP 流程测试"
        )
        assert (
            client.post(
                f"/api/tasks/{task['id']}/ack", headers=browser_headers, json={}
            ).status_code
            == 204
        )
        assert (
            client.get("/api/tasks/" + task["id"], headers=browser_headers).status_code
            == 404
        )
        # Malformed media never reaches the queue or remains on disk.
        bad = client.post(
            "/api/tasks",
            json={"attempt": secrets.token_urlsafe(32), "token": token, "size": 5},
        ).json()
        assert (
            client.put(
                f"/api/tasks/{bad['id']}/video",
                headers=browser_headers,
                content=b"wrong",
            ).status_code
            == 422
        )
        assert (
            client.get("/api/tasks/" + bad["id"], headers=browser_headers).json()[
                "state"
            ]
            == "failed"
        )
        assert not list(app.state.store.video_root.iterdir())
        assert client.post("/api/tasks", content=b"x" * 9000).status_code == 413


def test_queue_and_upload_expire_and_offline_heartbeat(store):
    now = [store.clock()]
    store.clock = lambda: now[0]
    queued, token = enqueue(store)
    partial, partial_token = reserve(store)
    now[0] += 181
    assert store.status()["state"] == "offline"
    assert store.get(partial["id"], partial_token)["code"] == "upload_expired"
    now[0] += 1800
    assert store.get(queued["id"], token)["code"] == "queue_expired"
