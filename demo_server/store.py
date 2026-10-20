from __future__ import annotations

import hashlib
import hmac
import secrets
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class Rejected(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status


@dataclass
class Limits:
    uploads: int = 2
    waiting: int = 10  # Includes upload reservations, excludes processing.
    processing: int = 1
    max_bytes: int = 10_000_000
    quota_bytes: int = 200_000_000
    upload_seconds: int = 180
    queue_seconds: int = 1800
    process_seconds: int = 300
    result_seconds: int = 3600
    heartbeat_seconds: int = 20
    disk_floor_bytes: int = 100_000_000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, root: Path, limits: Limits | None = None, clock=time.time):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.video_root = self.root / "videos"
        self.video_root.mkdir(exist_ok=True)
        self.limits, self.clock = limits or Limits(), clock
        self.lock = threading.RLock()
        self.worker_seen = 0.0
        self.worker_ready = False
        self.worker_id = ""
        # journal_mode cannot switch an existing database inside a transaction.
        with sqlite3.connect(self.root / "tasks.sqlite3") as initial:
            initial.execute("PRAGMA journal_mode=WAL")
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, token TEXT NOT NULL, attempt TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                expires REAL NOT NULL, size INTEGER NOT NULL, receiving INTEGER DEFAULT 0,
                lease TEXT DEFAULT '', text TEXT DEFAULT '', code TEXT DEFAULT '')""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state, created)"
            )
            # A restarted service cannot trust partial bodies or previous model leases.
            for row in db.execute(
                "SELECT id FROM tasks WHERE state IN ('uploading','processing')"
            ).fetchall():
                self._fail(db, row["id"], "service_restarted")
        self.cleanup()

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.root / "tasks.sqlite3", timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def path(self, task_id: str) -> Path:
        # IDs come only from authenticated rows / cryptographic generation.
        return self.video_root / f"{task_id}.video"

    def _delete(self, task_id):
        for path in (self.path(task_id), self.path(task_id).with_suffix(".part")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # Windows may still hold an upload/download handle; next sweep retries.

    def _fail(self, db, task_id, code):
        now = self.clock()
        db.execute(
            "UPDATE tasks SET state='failed',code=?,text='',receiving=0,expires=?,updated=? WHERE id=?",
            (code, now + self.limits.result_seconds, now, task_id),
        )
        self._delete(task_id)

    def _auth(self, db, task_id, token):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if (
            not row
            or not row["token"]
            or not hmac.compare_digest(row["token"], digest(token))
        ):
            raise Rejected("not_found", 404)
        return row

    def heartbeat(self, worker_id, ready):
        with self.lock:
            if (
                self.worker_id != worker_id
                and self.clock() - self.worker_seen < self.limits.heartbeat_seconds
            ):
                raise Rejected("worker_already_connected")
            self.worker_id, self.worker_seen, self.worker_ready = (
                worker_id,
                self.clock(),
                ready,
            )

    def _online(self):
        return (
            bool(self.worker_id)
            and self.clock() - self.worker_seen < self.limits.heartbeat_seconds
        )

    def status(self):
        self.cleanup()
        with self.db() as db:
            counts = {
                r["state"]: r["n"]
                for r in db.execute("SELECT state,count(*) n FROM tasks GROUP BY state")
            }
            pending = counts.get("queued", 0) + counts.get("uploading", 0)
            state = (
                "offline"
                if not self._online()
                else "preparing"
                if not self.worker_ready
                else "online"
            )
            if state == "online" and (
                pending >= self.limits.waiting
                or counts.get("uploading", 0) >= self.limits.uploads
            ):
                state = "full"
            return dict(
                state=state,
                queued=counts.get("queued", 0),
                uploading=counts.get("uploading", 0),
                processing=counts.get("processing", 0),
                limits=dict(
                    uploads=self.limits.uploads,
                    waiting=self.limits.waiting,
                    processing=self.limits.processing,
                    maxBytes=self.limits.max_bytes,
                    maxSeconds=10,
                ),
            )

    def reserve(self, attempt, token, size):
        self.cleanup()
        with self.db() as db:
            previous = db.execute(
                "SELECT * FROM tasks WHERE attempt=?", (attempt,)
            ).fetchone()
            if previous:
                self._auth(db, previous["id"], token)
                return self._view(db, previous)
            if not self._online():
                raise Rejected("offline", 503)
            if not self.worker_ready:
                raise Rejected("preparing", 503)
            rows = db.execute(
                "SELECT id,state,size FROM tasks WHERE state IN ('uploading','queued','processing')"
            ).fetchall()
            if sum(r["state"] == "uploading" for r in rows) >= self.limits.uploads:
                raise Rejected("uploads_full", 429)
            if sum(r["state"] != "processing" for r in rows) >= self.limits.waiting:
                raise Rejected("queue_full", 429)
            active_ids = {r["id"] for r in rows}
            orphan_bytes = sum(
                p.stat().st_size
                for p in self.video_root.iterdir()
                if p.is_file() and p.stem not in active_ids
            )
            if (
                sum(r["size"] for r in rows) + orphan_bytes + size
                > self.limits.quota_bytes
            ):
                raise Rejected("storage_full", 507)
            if shutil.disk_usage(self.root).free < self.limits.disk_floor_bytes + size:
                raise Rejected("storage_full", 507)
            if db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 10000:
                raise Rejected("queue_full", 429)
            task_id, now = secrets.token_hex(16), self.clock()
            db.execute(
                "INSERT INTO tasks(id,token,attempt,state,created,updated,expires,size) VALUES (?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    digest(token),
                    attempt,
                    "uploading",
                    now,
                    now,
                    now + self.limits.upload_seconds,
                    size,
                ),
            )
            return self._view(
                db, db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            )

    def _view(self, db, row):
        position = 0
        if row["state"] == "queued":
            position = db.execute(
                "SELECT count(*) FROM tasks WHERE state='queued' AND (created < ? OR (created=? AND id<=?))",
                (row["created"], row["created"], row["id"]),
            ).fetchone()[0]
        return dict(
            id=row["id"],
            state=row["state"],
            created=row["created"],
            text=row["text"],
            code=row["code"],
            position=position,
            expires=row["expires"],
        )

    def get(self, task_id, token):
        self.cleanup()
        with self.db() as db:
            return self._view(db, self._auth(db, task_id, token))

    def begin_upload(self, task_id, token):
        with self.db() as db:
            row = self._auth(db, task_id, token)
            if row["state"] != "uploading":
                return None  # Response-loss retry: don't enqueue twice.
            if row["receiving"]:
                raise Rejected("upload_in_progress")
            if row["expires"] <= self.clock():
                raise Rejected("upload_expired", 410)
            db.execute("UPDATE tasks SET receiving=1 WHERE id=?", (task_id,))
            return row["size"]

    def finish_upload(self, task_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["state"] != "uploading" or row["expires"] <= self.clock():
                self._delete(task_id)
                raise Rejected("upload_expired", 410)
            self.path(task_id).with_suffix(".part").replace(self.path(task_id))
            now = self.clock()
            db.execute(
                "UPDATE tasks SET state='queued',receiving=0,updated=?,expires=? WHERE id=?",
                (now, now + self.limits.queue_seconds, task_id),
            )

    def fail_upload(self, task_id, code):
        with self.db() as db:
            row = db.execute(
                "SELECT state FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row and row["state"] == "uploading":
                self._fail(db, task_id, code)

    def claim(self, worker_id):
        self.cleanup()
        with self.db() as db:
            if (
                worker_id != self.worker_id
                or not self._online()
                or not self.worker_ready
            ):
                raise Rejected("worker_not_ready")
            if (
                db.execute(
                    "SELECT count(*) FROM tasks WHERE state='processing'"
                ).fetchone()[0]
                >= self.limits.processing
            ):
                return None
            row = db.execute(
                "SELECT * FROM tasks WHERE state='queued' ORDER BY created,id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            lease, now = secrets.token_urlsafe(32), self.clock()
            db.execute(
                "UPDATE tasks SET state='processing',lease=?,updated=?,expires=? WHERE id=?",
                (digest(lease), now, now + self.limits.process_seconds, row["id"]),
            )
            return dict(
                id=row["id"],
                lease=lease,
                size=row["size"],
                timeout=self.limits.process_seconds,
            )

    def _lease(self, db, task_id, lease):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if (
            not row
            or not row["lease"]
            or not hmac.compare_digest(row["lease"], digest(lease))
        ):
            raise Rejected("stale_lease", 409)
        return row

    def download(self, task_id, lease):
        self.cleanup()
        with self.db() as db:
            row = self._lease(db, task_id, lease)
            if row["state"] != "processing":
                raise Rejected("stale_lease")
            return self.path(task_id)

    def complete(self, task_id, lease, text, code):
        self.cleanup()
        with self.db() as db:
            row = self._lease(db, task_id, lease)
            if (
                row["state"] in ("succeeded", "failed")
                and row["text"] == text
                and row["code"] == code
            ):
                # Idempotent result delivery; never overwrite the first terminal outcome.
                return
            if row["state"] != "processing":
                raise Rejected("stale_lease")
            now = self.clock()
            state = "failed" if code or not text.strip() else "succeeded"
            db.execute(
                "UPDATE tasks SET state=?,text=?,code=?,updated=?,expires=? WHERE id=?",
                (
                    state,
                    text if state == "succeeded" else "",
                    code or ("empty_output" if state == "failed" else ""),
                    now,
                    now + self.limits.result_seconds,
                    task_id,
                ),
            )
        # Persist before removing video; cleanup also removes any orphan after a crash.
        self._delete(task_id)

    def ack(self, task_id, token):
        with self.db() as db:
            row = self._auth(db, task_id, token)
            if row["state"] not in ("succeeded", "failed"):
                raise Rejected("task_not_finished")
            db.execute(
                "UPDATE tasks SET state='acked',token='',text='',lease='',code='' WHERE id=?",
                (task_id,),
            )
            self._delete(task_id)

    def cleanup(self):
        with self.db() as db:
            now = self.clock()
            for row in db.execute(
                "SELECT id,state FROM tasks WHERE expires<=?", (now,)
            ).fetchall():
                if row["state"] in ("uploading", "queued", "processing"):
                    self._fail(
                        db,
                        row["id"],
                        {
                            "uploading": "upload_expired",
                            "queued": "queue_expired",
                            "processing": "processing_timeout",
                        }[row["state"]],
                    )
                else:
                    self._delete(row["id"])
                    db.execute("DELETE FROM tasks WHERE id=?", (row["id"],))
            active = {
                r["id"]
                for r in db.execute(
                    "SELECT id FROM tasks WHERE state IN ('uploading','queued','processing')"
                )
            }
            for path in self.video_root.iterdir():
                if path.is_file() and path.stem not in active:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
