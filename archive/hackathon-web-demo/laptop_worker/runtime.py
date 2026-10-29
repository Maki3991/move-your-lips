from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from .logs import event

ROOT = Path(__file__).resolve().parents[3]


def model_process(pipe, vsr, checkpoint, threads):
    try:
        from .model import Model

        model = Model(vsr, checkpoint, threads)
        pipe.send({"ready": True})
        while True:
            path = pipe.recv()
            try:
                text = model.recognize(path)
                pipe.send({"text": text, "code": "" if text else "empty_output"})
            except ValueError:
                pipe.send({"text": "", "code": "no_mouth"})
            except Exception as exc:
                event("Model task failed: %s", type(exc).__name__)
                pipe.send({"text": "", "code": "model_error"})
    except (EOFError, KeyboardInterrupt):
        pass
    except Exception as exc:
        event("Model initialization failed: %s %s", type(exc).__name__, str(exc)[:240])
        try:
            pipe.send({"ready": False})
        except (BrokenPipeError, EOFError):
            pass


class Client:
    def __init__(self, url, secret):
        if not url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
            raise ValueError("Use HTTPS for a remote API.")
        self.url, self.secret = url.rstrip("/"), secret

    def request(self, path, body=None, lease=None, timeout=10):
        headers = {"Authorization": f"Bearer {self.secret}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if lease:
            headers["X-Task-Lease"] = lease
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        return urllib.request.urlopen(request, timeout=timeout)

    def json(self, path, body=None, lease=None):
        with self.request(path, body, lease) as response:
            return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--vsr-root")
    parser.add_argument("--checkpoint")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    settings_file = ROOT / "local.settings.json"
    settings = (
        json.loads(settings_file.read_text(encoding="utf-8-sig"))
        if settings_file.exists()
        else {}
    )
    vsr = Path(
        args.vsr_root
        or settings.get("vsrRoot")
        or ROOT / "third_party" / "CNVSRC2025" / "VSR"
    )
    active_file = ROOT / "outputs" / "mvp" / "active-model.json"
    active = (
        json.loads(active_file.read_text(encoding="utf-8-sig"))
        if active_file.exists()
        else {}
    )
    checkpoint = Path(
        args.checkpoint
        or active.get("checkpoint")
        or vsr / "pretrained_models" / "model_avg_cncvs_2_3_cnvsrc.pth"
    )
    if not vsr.is_dir() or not checkpoint.is_file():
        raise SystemExit(
            "Model repository/checkpoint missing. Check local.settings.json or CLI arguments."
        )
    secret = os.environ.get("MYL_WORKER_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("MYL_WORKER_SECRET is missing.")
    client = Client(args.url, secret)
    worker_id = secrets.token_urlsafe(24)
    work_root = ROOT / ".web-demo" / "worker"
    work_root.mkdir(parents=True, exist_ok=True)
    # Prevent two laptop Workers deleting one another's active video.
    lock_path = work_root / "worker.lock"
    lock_file = lock_path.open("a+b")
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        lock_file.write(b"0")
        lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("A laptop Worker is already running.")
    else:
        import fcntl

        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for path in work_root.glob("task-*"):
        if path.is_dir():
            shutil.rmtree(path)
    stop, ready = threading.Event(), threading.Event()

    def heartbeats():
        while not stop.is_set():
            try:
                client.json(
                    "/api/worker/heartbeat",
                    {"worker": worker_id, "ready": ready.is_set()},
                )
            except Exception:
                pass  # The service will report Offline if these stop arriving.
            stop.wait(4)

    heartbeat = threading.Thread(target=heartbeats, daemon=True)
    heartbeat.start()
    process, pipe = None, None

    def start_model():
        nonlocal process, pipe
        ready.clear()
        if process is not None:
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
            pipe.close()
        pipe, child = mp.Pipe()
        process = mp.Process(
            target=model_process,
            args=(child, str(vsr), str(checkpoint), max(1, args.threads)),
            daemon=True,
        )
        process.start()
        child.close()
        print("Loading model...", flush=True)
        if not pipe.poll(300) or not pipe.recv().get("ready"):
            raise RuntimeError("Model did not become ready; see the Worker log.")
        ready.set()
        # Publish readiness immediately, before attempting the first claim.
        try:
            client.json("/api/worker/heartbeat", {"worker": worker_id, "ready": True})
        except Exception:
            pass  # The heartbeat thread will reconnect; keep the loaded model.
        print("Model ready. Waiting for videos.", flush=True)

    try:
        start_model()
        while not stop.is_set():
            try:
                if not process.is_alive():
                    start_model()
                task = client.json(
                    "/api/worker/claim", {"worker": worker_id, "ready": True}
                )["task"]
                if not task:
                    stop.wait(1)
                    continue
            except Exception as exc:
                event("Relay unavailable: %s", type(exc).__name__)
                stop.wait(4)
                continue
            task_root = work_root / f"task-{task['id']}"
            task_root.mkdir()
            path = task_root / "input.video"
            deadline = time.monotonic() + task["timeout"] - 3
            outcome = {"text": "", "code": "download_failed"}
            reset = False
            try:
                size = 0
                with (
                    client.request(
                        f"/api/worker/tasks/{task['id']}/video",
                        lease=task["lease"],
                        timeout=30,
                    ) as source,
                    path.open("wb") as target,
                ):
                    while chunk := source.read(65536):
                        size += len(chunk)
                        if (
                            size > min(task["size"], 10_000_000)
                            or time.monotonic() > deadline
                        ):
                            raise ValueError("download_limit")
                        target.write(chunk)
                if size != task["size"]:
                    raise ValueError("download_incomplete")
                pipe.send(str(path))
                if pipe.poll(max(0.1, deadline - time.monotonic())):
                    outcome = pipe.recv()
                else:
                    outcome = {"text": "", "code": "processing_timeout"}
                    reset = True
            except (EOFError, BrokenPipeError):
                outcome = {"text": "", "code": "model_error"}
                reset = True
            except Exception as exc:
                event("Task failed: %s", type(exc).__name__)
            # Retry only result delivery, never recognition. Retain the text in RAM.
            if reset:
                ready.clear()
                process.terminate()
                process.join(5)
            delivered = False
            while time.monotonic() < deadline + 20:
                try:
                    client.json(
                        f"/api/worker/tasks/{task['id']}/result", outcome, task["lease"]
                    )
                    delivered = True
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in (404, 409):
                        break  # Expired lease / browser already acknowledged: discard.
                except Exception:
                    pass
                stop.wait(2)
            # Successful relay persistence ACK, or terminal timeout: remove all local video.
            shutil.rmtree(task_root, ignore_errors=True)
            event(
                "Task %s %s %s",
                task["id"][:8],
                "delivered" if delivered else "ended",
                outcome["code"] or "success",
            )
            outcome.clear()
            if reset:
                start_model()
    except KeyboardInterrupt:
        print("Stopping Worker.", flush=True)
    finally:
        ready.clear()
        stop.set()
        try:
            client.json("/api/worker/heartbeat", {"worker": worker_id, "ready": False})
        except Exception:
            pass
        if process is not None:
            process.terminate()
            process.join(5)
        for path in work_root.glob("task-*"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        lock_file.close()


if __name__ == "__main__":
    mp.freeze_support()
    main()
