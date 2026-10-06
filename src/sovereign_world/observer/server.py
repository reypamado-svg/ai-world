"""The observer's server (O3): a run served live over HTTP, read-only, behind a token.

    sovereign-world observe RUN_DIR [--host 127.0.0.1] [--port 8766]

Every `/api` route sits behind one router-level check of `Authorization: Bearer <token>`,
so no route can be added without it. The token comes from the observer's own environment
(`SOVEREIGN_WORLD_OBSERVER_TOKEN`) or is made fresh for each process; it is never written to
a file, and it travels only in that header (the browser reads it once from the page
address's fragment, which is never sent, and drops it from the address). The runner and
the sovereigns never see it.

The observer's own page and code are served without the token, but not the static run
exports under `data/runs` or the browser tests.

Answers carry `X-History-Epoch`, the run's history epoch they were made under. The day and
people files are byte-for-byte the static run export's (`run_export`), and the terrain files
the terrain export's.
"""

from __future__ import annotations

import mimetypes
import os
import secrets
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.observer import TOKEN_ENV
from sovereign_world.observer.runner_link import RunnerLink
from sovereign_world.observer.service import MAX_LOOKAHEAD, NoRunner, NotReady, RunService, Served

DEFAULT_PORT = 8766
REFUSED_PREFIXES = ("data/runs", "tests")
"""Static paths never served: run exports (the API serves runs) and the browser tests."""
UI_ROOT = Path(__file__).resolve().parents[3] / "observer"
"""The observer's page and code in a source checkout."""
TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def make_token() -> str:
    """The observer's token: from its environment, else fresh for this process."""
    return os.environ.get(TOKEN_ENV) or secrets.token_urlsafe(32)


def _answer(served: Served, media_type: str) -> Response:
    return Response(
        content=served.body,
        media_type=media_type,
        headers={"X-History-Epoch": str(served.epoch), "Cache-Control": "no-store"},
    )


def _json(served: Served) -> Response:
    return _answer(served, "application/json")


class ControlRequest(BaseModel):
    """What the page may ask of a runner the observer started: play or pause, how many days
    ahead it may run, and the day the page shows."""

    model_config = ConfigDict(extra="forbid")

    paused: bool | None = None
    lookahead: int | None = Field(default=None, ge=1, le=MAX_LOOKAHEAD)
    shown: int | None = Field(default=None, ge=0)


def build_app(service: RunService, token: str, *, ui_root: Path | None = UI_ROOT) -> FastAPI:
    if not token:
        raise ValueError("the observer needs a token")
    expected = f"Bearer {token}".encode()

    def authorised(request: Request) -> None:
        given = request.headers.get("authorization", "").encode()
        if not secrets.compare_digest(given, expected):
            raise HTTPException(
                status_code=401,
                detail="this observer needs its token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    api = APIRouter(prefix="/api", dependencies=[Depends(authorised)])

    def day_or_error(read: Callable[[], Served]) -> Served:
        try:
            return read()
        except NoRunner as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except NotReady as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error.args[0])) from error
        except RuntimeError as error:
            # The run cannot be shown this way (a map edited away from its seed's).
            raise HTTPException(status_code=409, detail=str(error)) from error

    @api.get("/control")
    def control() -> Response:
        return _json(day_or_error(service.control))

    @api.post("/control")
    def set_control(request: ControlRequest) -> Response:
        return _json(
            day_or_error(
                lambda: service.set_control(
                    paused=request.paused, lookahead=request.lookahead, shown=request.shown
                )
            )
        )

    @api.get("/status")
    def status() -> Response:
        return _json(service.status())

    @api.get("/run/manifest")
    def manifest() -> Response:
        return _json(service.manifest())

    @api.get("/run/ids")
    def ids(start: int = Query(0, alias="from", ge=0)) -> Response:
        return _json(service.ids(start))

    @api.get("/run/days")
    def days() -> Response:
        return _json(service.days())

    @api.get("/run/days/{day}")
    def day(day: int) -> Response:
        return _json(day_or_error(lambda: service.day(day)))

    @api.get("/run/days/{day}/people")
    def people(day: int) -> Response:
        return _answer(day_or_error(lambda: service.people(day)), "application/gzip")

    @api.get("/run/days/{day}/changes")
    def changes(day: int, start: int = Query(..., alias="from", ge=0)) -> Response:
        return _json(day_or_error(lambda: service.changes(day, start)))

    @api.get("/run/days/{day}/routes")
    def routes(day: int) -> Response:
        return _json(day_or_error(lambda: service.routes(day)))

    @api.get("/run/days/{day}/perspective/{civ}")
    def perspective(day: int, civ: int) -> Response:
        return _json(day_or_error(lambda: service.perspective(day, civ)))

    @api.get("/run/chronicle")
    def chronicle(day: int = Query(..., ge=0)) -> Response:
        return _json(day_or_error(lambda: service.chronicle(day)))

    @api.get("/run/terrain/{path:path}")
    def terrain(path: str) -> Response:
        return _json(day_or_error(lambda: service.terrain(path)))

    app.include_router(api)

    @app.get("/api/{rest:path}", include_in_schema=False)
    def unknown_api(rest: str, _: None = Depends(authorised)) -> Response:
        raise HTTPException(status_code=404, detail="no such route")

    if ui_root is not None:
        root = ui_root.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def page(path: str) -> Response:
            relative = path.strip("/") or "index.html"
            parts = Path(relative).parts
            if (
                any(part.startswith(".") for part in parts)
                or any(
                    relative == prefix or relative.startswith(prefix + "/")
                    for prefix in REFUSED_PREFIXES
                )
                or "\\" in relative
            ):
                raise HTTPException(status_code=404, detail="not found")
            file = (root / relative).resolve()
            if root not in file.parents or not file.is_file():
                raise HTTPException(status_code=404, detail="not found")
            kind = TYPES.get(file.suffix) or mimetypes.guess_type(file.name)[0]
            return FileResponse(
                file,
                media_type=kind or "application/octet-stream",
                headers={"Cache-Control": "no-store"},
            )

    return app


def serve(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    run_days: int | None = None,
) -> None:
    """Serve a run until interrupted (the `observe` command); with `run_days`, also run it
    that many days further, as the page plays."""
    import uvicorn

    from_env = bool(os.environ.get(TOKEN_ENV))
    token = make_token()
    service = RunService(root)
    if run_days is not None:
        service.attach(RunnerLink.spawn(root, run_days, on_change=service.runner_changed))
    service.start()
    about = service.describe()
    shown = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    address = f"http://{shown}:{port}/?run=live"
    print(
        f"Observing run {about['run_id']} (seed {about['seed']}, {about['size']},"
        f" days {about['days']})"
    )
    if run_days is not None:
        print(f"The run goes on for up to {run_days} days, but only while the page plays.")
    if from_env:
        print(f"Open {address} and give the token from {TOKEN_ENV}.")
    else:
        print(f"Open {address}#token={token}")
    try:
        uvicorn.run(build_app(service, token), host=host, port=port, log_level="warning")
    finally:
        service.stop()
