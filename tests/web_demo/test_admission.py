import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from demo_server.admission import BrowserAdmission
from demo_server.app import create_app
from demo_server.store import Limits, Rejected, Store
from .test_lifecycle import SECRET, WORKER, enqueue, reserve


def test_shared_ip_separate_browser_limits_and_expiry():
    now = [1000.0]
    admission = BrowserAdmission(
        SECRET, clock=lambda: now[0], wall_clock=lambda: now[0]
    )
    a, _ = admission.identify("")
    b, _ = admission.identify("")
    assert admission.identify(a) == (a, False)
    for _ in range(6):
        admission.check(a, "shared-wifi")
    with pytest.raises(Rejected) as error:
        admission.check(a, "another-network")
    assert error.value.code == "rate_limited"
    admission.check(b, "shared-wifi")
    assert admission.identify(a + "tampered")[1]
    assert admission.identify("invalid.1000.\u00e9")[1]
    now[0] += 60
    admission.check(a, "shared-wifi")
    now[0] += admission.lifetime
    assert admission.identify(a)[1]


def test_ip_limit_and_thread_safety():
    admission = BrowserAdmission(SECRET)

    def submit(i):
        try:
            admission.check(str(i), "shared")
            return True
        except Rejected as exc:
            assert exc.code == "ip_rate_limited"
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(submit, range(100))) == 60
    other = BrowserAdmission(SECRET)

    def same_browser(_):
        try:
            other.check("one-browser", "ip")
            return True
        except Rejected:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(same_browser, range(30))) == 6


def test_rate_state_is_bounded():
    admission = BrowserAdmission(SECRET)
    for i in range(2048):
        admission.check(str(i), str(i))
    with pytest.raises(Rejected):
        admission.check("extra", "extra")
    assert len(admission.rates) == 4096


def test_http_cookie_shared_wifi_and_idempotent_recovery(tmp_path):
    app = create_app(tmp_path, SECRET)
    store = app.state.store
    store.heartbeat(WORKER, True)
    with (
        TestClient(app, base_url="https://testserver") as a,
        TestClient(app, base_url="https://testserver") as b,
    ):
        response = a.get("/api/status")
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        assert "set-cookie" not in a.get("/api/status").headers
        token, attempt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        body = dict(attempt=attempt, token=token, size=5)
        first = a.post("/api/tasks", json=body).json()
        for _ in range(8):  # Recovery is not a new application.
            assert a.post("/api/tasks", json=body).json()["id"] == first["id"]
        store.fail_upload(first["id"], "upload_failed")
        for _ in range(5):
            r = a.post("/api/tasks", json=dict(body, attempt=secrets.token_urlsafe(32)))
            assert r.status_code == 200
            store.fail_upload(r.json()["id"], "upload_failed")
        denied = a.post(
            "/api/tasks", json=dict(body, attempt=secrets.token_urlsafe(32))
        )
        assert denied.json()["code"] == "rate_limited"
        assert denied.headers["retry-after"] == "60"
        # Same TestClient IP, new browser, direct POST without warming status.
        assert (
            b.post(
                "/api/tasks", json=dict(body, attempt=secrets.token_urlsafe(32))
            ).status_code
            == 200
        )
        assert b.cookies.get("myl_browser") != a.cookies.get("myl_browser")
        assert a.post("/api/tasks", json=dict(body, token="x" * 40)).status_code == 404


@pytest.fixture
def timed_store(tmp_path):
    now = [1000.0]
    store = Store(tmp_path, Limits(disk_floor_bytes=0), clock=lambda: now[0])
    store.heartbeat(WORKER, True)
    return store, now


def test_three_minute_admission_counts_uploads_and_keeps_accepted_work(timed_store):
    store, now = timed_store
    first, _ = enqueue(store)
    claim = store.claim(WORKER)
    now[0] += 0.001  # Give reservations an unambiguous chronological order.
    reserve(store)  # processing 90 + uploading 90 = 180 seconds; still accepts.
    now[0] += 0.001
    accepted, token = reserve(store)
    assert accepted["estimatedWaitSeconds"] == 180
    # Upload cap is independently enforced; after one validates, time cap wins.
    store.path(accepted["id"]).with_suffix(".part").write_bytes(b"video")
    store.finish_upload(accepted["id"])
    assert store.status()["code"] == "wait_too_long"
    with pytest.raises(Rejected) as error:
        reserve(store)
    assert error.value.code == "wait_too_long"
    assert store.status()["estimatedWaitSeconds"] == 270
    now[0] += 10
    store.heartbeat(WORKER, True)
    assert store.status()["estimatedWaitSeconds"] == 260
    assert store.get(accepted["id"], token)["state"] == "queued"
    store.complete(first["id"], claim["lease"], "result", "")


def test_admitted_queue_survives_a_slower_estimate(timed_store):
    store, now = timed_store
    first, _ = enqueue(store)
    claim = store.claim(WORKER)
    now[0] += 0.001
    queued, token = enqueue(store)
    now[0] += 0.001
    last, last_token = enqueue(store)
    now[0] += 149.998
    store.heartbeat(WORKER, True)
    store.complete(first["id"], claim["lease"], "result", "")
    assert store.status()["estimatedProcessSeconds"] == 180
    assert store.status()["code"] == "wait_too_long"
    assert store.get(queued["id"], token)["state"] == "queued"
    assert store.get(last["id"], last_token)["state"] == "queued"


def test_processing_overrun_and_offline_estimate(timed_store):
    store, now = timed_store
    enqueue(store)
    store.claim(WORKER)
    now[0] += 120
    assert store.status()["estimatedWaitSeconds"] is None
    store.heartbeat(WORKER, True)
    assert store.status()["estimatedWaitSeconds"] == 15
    store.heartbeat(WORKER, False)
    assert store.status()["estimatedWaitSeconds"] is None


def test_recent_samples_persist_and_idempotent_delivery_is_not_counted_twice(
    timed_store,
):
    store, now = timed_store
    for duration in (30, 40, 50):
        task, token = enqueue(store)
        claim = store.claim(WORKER)
        now[0] += duration
        store.heartbeat(WORKER, True)
        store.complete(task["id"], claim["lease"], "result", "")
        store.complete(task["id"], claim["lease"], "result", "")
        store.ack(task["id"], token)
    status = store.status()
    assert status["waitEstimateSource"] == "recent"
    assert status["estimatedProcessSeconds"] == 60  # P80 50 + 20% margin.
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM processing_samples").fetchone()[0] == 3
    reloaded = Store(store.root, clock=lambda: now[0])
    reloaded.heartbeat(WORKER, True)
    assert reloaded.status()["estimatedProcessSeconds"] == 60
    now[0] += 3601
    reloaded.cleanup()
    assert reloaded.status()["estimatedProcessSeconds"] == 60


def test_slow_samples_timeout_and_sample_bound(timed_store):
    store, now = timed_store
    task, _ = enqueue(store)
    claim = store.claim(WORKER)
    now[0] += 150
    store.heartbeat(WORKER, True)
    store.complete(task["id"], claim["lease"], "result", "")
    assert (
        store.status()["estimatedProcessSeconds"] == 180
    )  # One slow result raises bootstrap.
    enqueue(store)
    store.claim(WORKER)
    now[0] += 301
    store.heartbeat(WORKER, True)
    assert store.status()["estimatedProcessSeconds"] == 300
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM processing_samples").fetchone()[0] == 2
        for _ in range(25):
            store._sample(db, 20)
        assert db.execute("SELECT count(*) FROM processing_samples").fetchone()[0] == 20
    assert store.status()["estimatedProcessSeconds"] == 24


def test_old_database_without_samples_migrates(tmp_path):
    Store(tmp_path)
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as db:
        db.execute("DROP TABLE processing_samples")
    reloaded = Store(tmp_path)
    reloaded.heartbeat(WORKER, True)
    assert reloaded.status()["estimatedProcessSeconds"] == 90
    assert reloaded.status()["waitEstimateSource"] == "initial"


def test_concurrent_reservations_cannot_bypass_time_budget(timed_store):
    store, _ = timed_store
    store.limits.uploads = 10  # Isolate admission from the independent upload limit.

    def submit(_):
        try:
            return reserve(store)
        except Rejected as exc:
            assert exc.code == "wait_too_long"
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(r is not None for r in pool.map(submit, range(20))) == 3
    assert store.status()["uploading"] == 3
