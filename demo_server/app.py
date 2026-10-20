from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .admission import BrowserAdmission
from .store import Rejected, Store
from .video import validate_video

ROOT = Path(__file__).resolve().parents[1]
ERROR_CODES = {
    "no_mouth",
    "empty_output",
    "model_error",
    "processing_timeout",
    "download_failed",
    "worker_stopped",
}


class Reservation(BaseModel):
    attempt: str = Field(pattern=r"^[a-zA-Z0-9_-]{32,128}$")
    token: str = Field(pattern=r"^[a-zA-Z0-9_-]{32,128}$")
    size: int = Field(gt=0, le=10_000_000)


class Heartbeat(BaseModel):
    worker: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,128}$")
    ready: bool


class Completion(BaseModel):
    text: str = Field(default="", max_length=4096)
    code: str = Field(default="", max_length=64)


def create_app(data_dir=None, secret=None, limits=None, validator=validate_video):
    worker_secret = secret or os.environ.get("MYL_WORKER_SECRET", "")
    if len(worker_secret) < 32:
        raise RuntimeError(
            "Set MYL_WORKER_SECRET to a random value of at least 32 characters."
        )
    store = Store(
        Path(data_dir or os.environ.get("MYL_DATA_DIR", ROOT / ".web-demo" / "server")),
        limits,
    )
    admission = BrowserAdmission(worker_secret)
    validation_slots = asyncio.Semaphore(store.limits.uploads)

    @asynccontextmanager
    async def lifespan(app):
        async def sweep():
            while True:
                await asyncio.sleep(10)
                await run_in_threadpool(store.cleanup)

        task = asyncio.create_task(sweep())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.admission = admission

    @app.exception_handler(Rejected)
    async def rejected(request, exc):
        return JSONResponse(
            {"code": exc.code},
            status_code=exc.status,
            headers={"Retry-After": "60"}
            if exc.code in {"rate_limited", "ip_rate_limited"}
            else None,
        )

    @app.middleware("http")
    async def headers(request, call_next):
        browser, fresh = "", False
        if request.url.path in {"/api/status", "/api/tasks"}:
            browser, fresh = admission.identify(
                request.cookies.get(admission.cookie, "")
            )
            request.state.browser = browser
        # API JSON bodies are tiny. Videos have their own streamed bound.
        if request.method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 8192:
                    return JSONResponse({"code": "request_too_large"}, status_code=413)
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        if fresh:
            response.set_cookie(
                admission.cookie,
                browser,
                max_age=admission.lifetime,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                path="/api",
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(self), microphone=()",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            }
        )
        return response

    def bearer(value):
        if not value or not value.startswith("Bearer "):
            raise Rejected("not_found", 404)
        return value[7:]

    def worker_auth(value):
        if not hmac.compare_digest(bearer(value), worker_secret):
            raise Rejected("not_found", 404)

    @app.get("/api/status")
    def status():
        return store.status()

    @app.post("/api/tasks")
    def reserve(body: Reservation, request: Request):
        if body.size > store.limits.max_bytes:
            raise Rejected("file_too_large", 413)
        return store.reserve(
            body.attempt,
            body.token,
            body.size,
            admission=lambda: admission.check(
                request.state.browser,
                request.client.host if request.client else "unknown",
            ),
        )

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, authorization: str | None = Header(default=None)):
        return store.get(task_id, bearer(authorization))

    @app.put("/api/tasks/{task_id}/video")
    async def upload(
        task_id: str, request: Request, authorization: str | None = Header(default=None)
    ):
        token = bearer(authorization)
        expected = store.begin_upload(task_id, token)
        if expected is None:
            return store.get(task_id, token)
        part = store.path(task_id).with_suffix(".part")
        try:
            async with asyncio.timeout(store.limits.upload_seconds):
                size = 0
                with part.open("wb") as target:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > expected:
                            raise Rejected("file_too_large", 413)
                        target.write(chunk)
                if size != expected:
                    raise Rejected("upload_incomplete", 400)
                async with validation_slots:
                    await run_in_threadpool(validator, part)
                store.finish_upload(task_id)
        except ValueError as exc:
            code = (
                str(exc)
                if str(exc)
                in {
                    "video_only",
                    "resolution_invalid",
                    "format_invalid",
                    "duration_invalid",
                    "video_too_short",
                }
                else "format_invalid"
            )
            store.fail_upload(task_id, code)
            raise Rejected(code, 422)
        except BaseException as exc:
            store.fail_upload(task_id, "upload_failed")
            part.unlink(missing_ok=True)
            if isinstance(exc, Rejected):
                raise
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise Rejected("upload_failed", 400) from exc
        return store.get(task_id, token)

    @app.post("/api/tasks/{task_id}/ack", status_code=204)
    def ack(task_id: str, authorization: str | None = Header(default=None)):
        store.ack(task_id, bearer(authorization))
        return Response(status_code=204)

    @app.post("/api/worker/heartbeat")
    def heartbeat(body: Heartbeat, authorization: str | None = Header(default=None)):
        worker_auth(authorization)
        store.heartbeat(body.worker, body.ready)
        return {"ok": True}

    @app.post("/api/worker/claim")
    def claim(body: Heartbeat, authorization: str | None = Header(default=None)):
        worker_auth(authorization)
        return {"task": store.claim(body.worker)}

    @app.get("/api/worker/tasks/{task_id}/video")
    def download(
        task_id: str,
        authorization: str | None = Header(default=None),
        x_task_lease: str = Header(default=""),
    ):
        worker_auth(authorization)
        return FileResponse(
            store.download(task_id, x_task_lease), media_type="application/octet-stream"
        )

    @app.post("/api/worker/tasks/{task_id}/result")
    def result(
        task_id: str,
        body: Completion,
        authorization: str | None = Header(default=None),
        x_task_lease: str = Header(default=""),
    ):
        worker_auth(authorization)
        if body.code and body.code not in ERROR_CODES:
            raise Rejected("invalid_error_code", 400)
        store.complete(task_id, x_task_lease, body.text, body.code)
        return {"ok": True}

    build = ROOT / "web" / "dist"
    if build.exists():
        app.mount("/", StaticFiles(directory=build, html=True), name="web")
    return app
