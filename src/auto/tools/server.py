"""The MCP endpoint the harness tools are served over, inside the loop process.

Each ephemeral agent is handed an inline, strict MCP config whose **URL carries
the run and the scope under judgment** — a node for an orchestrator
intervention, an effort for a takeover consultation (ADR-0009). That is the
whole scoping mechanism: an agent cannot act on a scope it was not invoked
about, because the address that would let it do so does not exist. Enforcement
by addressing beats validating an argument the agent supplies, since there is
no such argument. Each window serves its scope's own roster.

The transport is deliberately small: JSON-RPC over POST, answered with
`application/json` rather than an event stream, which the streamable-HTTP
transport allows for a request that gets a single response. There is no session
id and no server-initiated stream, because an intervention is one turn long.

Requests arrive on the HTTP server's own threads and every tool call is handed
to the loop thread before it touches anything — the same invariant the run
directory is written under, kept by construction rather than by care.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote

from auto.tools.harness import (
    SERVER_NAME,
    TAKEOVER_TOOLS,
    HarnessTools,
    Intervention,
    TakeoverTools,
    ToolResult,
)

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LOOPBACK = "127.0.0.1"
CALL_TIMEOUT_SECONDS = 60.0
MAX_BODY_BYTES = 4 * 1024 * 1024
SHUTDOWN_POLL_SECONDS = 0.05

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


def intervention_path(run_id: str, node: str) -> str:
    """Where one intervention's tools live. Nothing else answers on it."""
    return f"/runs/{quote(run_id, safe='')}/nodes/{quote(node, safe='')}/mcp"


def takeover_path(run_id: str, effort: str) -> str:
    """Where one takeover consultation's tools live: the same addressing
    scheme one level up, naming the effort under reconciliation instead of a
    node under judgment (ADR-0009)."""
    return f"/runs/{quote(run_id, safe='')}/efforts/{quote(effort, safe='')}/mcp"


class ToolServer:
    """Serves the harness tools for the lifetime of one orchestrator process."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        host: str = LOOPBACK,
        port: int = 0,
        call_timeout: float = CALL_TIMEOUT_SECONDS,
    ) -> None:
        self._loop = loop
        self._call_timeout = call_timeout
        self._open: dict[str, Intervention] = {}
        self._http = _ToolHTTPServer((host, port), _Handler)
        self._http.tools = self
        self._thread = threading.Thread(
            target=self._http.serve_forever,
            kwargs={"poll_interval": SHUTDOWN_POLL_SECONDS},
            name="auto-tools",
            daemon=True,
        )
        self._thread.start()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._http.server_address[:2]
        return str(host), int(port)

    def url_for(self, run_id: str, node: str) -> str:
        return self._url(intervention_path(run_id, node))

    def takeover_url(self, run_id: str, effort: str) -> str:
        return self._url(takeover_path(run_id, effort))

    def _url(self, path: str) -> str:
        host, port = self.address
        return f"http://{host}:{port}{path}"

    def mcp_config(self, run_id: str, node: str) -> str:
        """The inline config for one intervention, as JSON for `--mcp-config`."""
        return _inline_config(self.url_for(run_id, node))

    def takeover_mcp_config(self, run_id: str, effort: str) -> str:
        """The inline config for one takeover consultation."""
        return _inline_config(self.takeover_url(run_id, effort))

    def intervention(
        self, run_id: str, node: str, tools: HarnessTools
    ) -> contextlib.AbstractContextManager[Intervention]:
        """Open one intervention's window, and shut it when the agent is done.

        Outside this block the URL 404s, so a tool call that arrives late — from
        a process that outlived its intervention — cannot land.
        """
        return self._window(
            intervention_path(run_id, node),
            Intervention(scope=node, tools=tools),
            f"an intervention on node {node!r} is already open; "
            "interventions on one node never overlap",
        )

    def takeover(
        self, run_id: str, effort: str, tools: TakeoverTools
    ) -> contextlib.AbstractContextManager[Intervention]:
        """Open one takeover consultation's window: the takeover roster, on
        the effort's own address. The same window discipline as a node's."""
        return self._window(
            takeover_path(run_id, effort),
            Intervention(scope=effort, tools=tools, roster=TAKEOVER_TOOLS),
            f"a takeover consultation on effort {effort!r} is already open; "
            "consultations on one effort never overlap",
        )

    @contextlib.contextmanager
    def _window(
        self, path: str, scope: Intervention, refusal: str
    ) -> Iterator[Intervention]:
        if path in self._open:
            raise RuntimeError(refusal)
        self._open[path] = scope
        try:
            yield scope
        finally:
            self._open.pop(path, None)

    def close(self) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._thread.join(timeout=5.0)

    # --- called on an HTTP thread ------------------------------------------

    def open_at(self, path: str) -> Intervention | None:
        """The intervention addressed by this path, if one is open there."""
        return self._open.get(path)

    def run_tool(
        self, scope: Intervention, name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Hop to the loop thread, where run state may actually be touched."""
        future = asyncio.run_coroutine_threadsafe(
            scope.call(name, arguments), self._loop
        )
        return future.result(timeout=self._call_timeout)


class _ToolHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    tools: ToolServer


class _Handler(BaseHTTPRequestHandler):
    """JSON-RPC over POST. Everything else is not something a client needs."""

    protocol_version = "HTTP/1.1"
    server_version = "auto-harness-tools"

    server: _ToolHTTPServer

    def do_POST(self) -> None:  # noqa: N802 — the base class names it
        scope = self.server.tools.open_at(self.path)
        if scope is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "no intervention is open at this address"},
            )
            return

        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        try:
            request = json.loads(body) if body else None
        except json.JSONDecodeError:
            self._send_rpc(None, error=(_PARSE_ERROR, "invalid JSON"))
            return
        if not isinstance(request, dict):
            self._send_rpc(None, error=(_INVALID_REQUEST, "expected a JSON-RPC object"))
            return

        self._dispatch(scope, request)

    def do_GET(self) -> None:  # noqa: N802
        """No server-initiated stream: one intervention is one turn long."""
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED, {"error": "this endpoint answers POST only"}
        )

    do_DELETE = do_GET  # nothing to tear down: there is no session to end.

    def log_message(self, format: str, *args: Any) -> None:
        """Silence: the run's own output is the only thing worth reading."""

    # --- JSON-RPC ----------------------------------------------------------

    def _dispatch(self, scope: Intervention, request: dict[str, Any]) -> None:
        method = request.get("method")
        request_id = request.get("id")

        if request_id is None:
            # A notification. Nothing to answer, and nothing here acts on one.
            self._send_empty(HTTPStatus.ACCEPTED)
            return

        if method == "initialize":
            self._send_rpc(request_id, result=_initialize_result(request))
        elif method == "ping":
            self._send_rpc(request_id, result={})
        elif method == "tools/list":
            self._send_rpc(request_id, result={"tools": scope.definitions()})
        elif method == "tools/call":
            self._send_rpc(request_id, result=self._call_tool(scope, request))
        else:
            self._send_rpc(
                request_id, error=(_METHOD_NOT_FOUND, f"unsupported method: {method}")
            )

    def _call_tool(self, scope: Intervention, request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        name = params.get("name")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        if not isinstance(name, str):
            return _tool_error("tools/call needs a tool name")
        try:
            result = self.server.tools.run_tool(scope, name, arguments)
        except Exception as exc:  # noqa: BLE001 — a tool must never kill the loop
            return _tool_error(f"the harness could not run {name}: {exc}")
        return {
            "content": [{"type": "text", "text": result.text}],
            "isError": result.is_error,
        }

    # --- HTTP --------------------------------------------------------------

    def _read_body(self) -> bytes:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return b""
        try:
            length = int(raw)
        except ValueError as exc:
            raise ValueError("unreadable Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body out of range")
        return self.rfile.read(length)

    def _send_rpc(
        self,
        request_id: Any,
        *,
        result: dict[str, Any] | None = None,
        error: tuple[int, str] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = {"code": error[0], "message": error[1]}
        else:
            payload["result"] = result if result is not None else {}
        self._send_json(HTTPStatus.OK, payload)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _initialize_result(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params")
    asked = params.get("protocolVersion") if isinstance(params, dict) else None
    return {
        "protocolVersion": asked if isinstance(asked, str) and asked else (
            DEFAULT_PROTOCOL_VERSION
        ),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": "1"},
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _inline_config(url: str) -> str:
    return json.dumps(
        {"mcpServers": {SERVER_NAME: {"type": "http", "url": url}}}
    )
