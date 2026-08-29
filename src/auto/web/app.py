"""The HTTP surface of `auto serve`.

Four endpoints and the site itself:

    GET /api/runs                                       the runs rail
    GET /api/runs/{run_id}                              one run, whole
    GET /api/runs/{run_id}/transcripts/{session_id}     incremental tail
    GET /api/events                                     SSE change stream

The change stream carries `{"run_id", "version"}` notifications and opens
with a `versions` baseline; a client refetches what it is showing when a
notification names it. Change detection is the watcher's (or, with
`poll=True`, a timer's) — both just call the tracker's tick, so the
endpoints cannot tell the two apart.

The site is served from `static/` inside the package — prebuilt, so the CLI
needs no node toolchain — or, in dev mode, proxied to a Vite dev server so UI
work keeps its fast loop while the API stays real on one origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from watchfiles import awatch

from auto.errors import UsageError
from auto.web.changes import ChangeTracker, RunChange
from auto.web.reads import run_detail, run_summaries, transcript_tail

STATIC_DIR = Path(__file__).parent / "static"
KEEPALIVE_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 1.0


class Broadcast:
    """Fan one change notification out to every open SSE stream."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[RunChange]] = set()

    def publish(self, change: RunChange) -> None:
        for queue in self._subscribers:
            queue.put_nowait(change)

    @contextlib.contextmanager
    def subscribe(self) -> Any:
        queue: asyncio.Queue[RunChange] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


async def _poll_loop(tracker: ChangeTracker, broadcast: Broadcast, interval: float) -> None:
    """The fallback: same tick, on a timer instead of a filesystem signal."""
    while True:
        for change in tracker.tick():
            broadcast.publish(change)
        await asyncio.sleep(interval)


async def _watch_loop(tracker: ChangeTracker, broadcast: Broadcast) -> None:
    """Tick when the filesystem stirs under the runs dir or an effort root.

    The watched set can grow mid-run — a new run names a new target repo —
    so the watcher is restarted whenever a tick changes what should be
    watched. An empty set (no runs yet) falls back to a slow poll until
    there is something to stand over.
    """
    while True:
        roots = tracker.watch_roots()
        if not roots:
            for change in tracker.tick():
                broadcast.publish(change)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        async for _ in awatch(*roots):
            for change in tracker.tick():
                broadcast.publish(change)
            if tracker.watch_roots() != roots:
                break


def create_app(
    state_dir: Path,
    *,
    static_dir: Path | None = None,
    dev_server: str | None = None,
    poll: bool = False,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> Starlette:
    """The server over one state directory.

    `dev_server` (a Vite dev server's URL) replaces the packaged static
    assets with a proxy to it; `poll=True` swaps the filesystem watcher for
    a timer at `poll_interval` seconds, behind identical endpoints.
    """
    tracker = ChangeTracker(state_dir)
    broadcast = Broadcast()
    tracker.tick()  # baseline, so startup does not notify about old news

    async def runs(request: Request) -> Response:
        return JSONResponse(run_summaries(state_dir))

    async def run(request: Request) -> Response:
        try:
            detail = run_detail(state_dir, request.path_params["run_id"])
        except UsageError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        detail["version"] = tracker.versions.get(request.path_params["run_id"], 0)
        return JSONResponse(detail)

    async def transcript(request: Request) -> Response:
        try:
            after = int(request.query_params.get("after", "0"))
        except ValueError:
            return JSONResponse({"error": "after must be an integer"}, status_code=400)
        try:
            tail = transcript_tail(
                state_dir,
                request.path_params["run_id"],
                request.path_params["session_id"],
                after=after,
            )
        except UsageError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(tail)

    async def events(request: Request) -> Response:
        async def stream() -> AsyncIterator[str]:
            with broadcast.subscribe() as queue:
                yield _sse("versions", tracker.versions)
                while True:
                    try:
                        change = await asyncio.wait_for(
                            queue.get(), timeout=KEEPALIVE_SECONDS
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield _sse(
                        "change",
                        {"run_id": change.run_id, "version": change.version},
                    )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    routes: list[Route | Mount] = [
        Route("/api/runs", runs),
        Route("/api/runs/{run_id}", run),
        Route("/api/runs/{run_id}/transcripts/{session_id}", transcript),
        Route("/api/events", events),
    ]
    if dev_server is not None:
        routes.append(Route("/{path:path}", _dev_proxy(dev_server)))
    else:
        routes.append(
            Mount(
                "/",
                app=StaticFiles(
                    directory=static_dir if static_dir is not None else STATIC_DIR,
                    html=True,
                ),
            )
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        task = asyncio.create_task(
            _poll_loop(tracker, broadcast, poll_interval)
            if poll
            else _watch_loop(tracker, broadcast)
        )
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return Starlette(routes=routes, lifespan=lifespan)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _dev_proxy(dev_server: str) -> Any:
    """Forward everything that is not the API to a Vite dev server.

    Import inside, not at module top: httpx2 is a dev dependency, and the
    packaged server must not require it.
    """
    import httpx2

    client = httpx2.AsyncClient(base_url=dev_server)

    async def proxy(request: Request) -> Response:
        upstream = await client.request(
            request.method,
            "/" + request.path_params["path"],
            params=request.query_params,
            headers={
                name: value
                for name, value in request.headers.items()
                if name.lower() not in ("host", "connection")
            },
            content=await request.body(),
        )
        return Response(
            upstream.content,
            status_code=upstream.status_code,
            headers={
                name: value
                for name, value in upstream.headers.items()
                if name.lower()
                not in ("content-encoding", "transfer-encoding", "connection")
            },
        )

    return proxy
