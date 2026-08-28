#!/usr/bin/env python3
"""A stand-in for the `claude` binary, for testing the real launcher's plumbing.

Two shapes, the same two the harness launches. A **driven session** reads
stream-json turns on stdin, answers each with a `result` event and stays alive
between them. A **one-shot** invocation takes its message on the command line,
optionally calls the harness tools its inline MCP config points at — over real
HTTP, exactly as the orchestrator agent would — and exits.

Everything it says echoes what it was given, so a test can assert on what
actually reached the process.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    argv = sys.argv[1:]
    session_id = value_of(argv, "--session-id") or "?"
    if os.environ.get("FAKE_CLAUDE_FAIL_TO_START"):
        print("fake claude: refusing to start", file=sys.stderr)
        return 2

    emit({"type": "system", "subtype": "init", "session_id": session_id, "argv": argv})

    one_shot = positional_after(argv, "-p")
    if one_shot is not None:
        call_tools(argv)
        turn(session_id, one_shot, 1)
        return 0

    write_tracker_files()

    count = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        count += 1
        message = json.loads(line)
        turn(session_id, message["message"]["content"][0]["text"], count)
    return 0


def turn(session_id: str, text: str, count: int) -> None:
    emit(
        {
            "type": "assistant",
            "session_id": session_id,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": f"received: {text}",
            "session_id": session_id,
            "num_turns": count,
            "total_cost_usd": 0.01 * count,
            "stop_reason": "end_turn",
            "terminal_reason": "completed",
        }
    )


def write_tracker_files() -> None:
    """Lay down what a driven session leaves behind, if the test says it does.

    `$FAKE_CLAUDE_WRITES` names a JSON file of path → content, written into the
    working directory the session was launched in. The harness checks the
    target repo for a node's owed artifacts, so a stand-in that never wrote one
    could only ever stand in for a session that forgot.
    """
    script = os.environ.get("FAKE_CLAUDE_WRITES")
    if not script:
        return
    for path, content in json.loads(open(script, encoding="utf-8").read()).items():
        file = os.path.join(os.getcwd(), path)
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file, "w", encoding="utf-8") as handle:
            handle.write(content)


def call_tools(argv: list[str]) -> None:
    """Make this invocation's scripted calls, for real, over MCP.

    `$FAKE_CLAUDE_SCRIPT` names a JSON file holding one list of calls per
    invocation; a cursor file beside it says which invocation this is, since
    each one is a fresh process with no memory of the last.
    """
    script = os.environ.get("FAKE_CLAUDE_SCRIPT")
    config = value_of(argv, "--mcp-config")
    if not script or config is None:
        return
    calls = next_invocation(script)
    if not calls:
        return
    servers = json.loads(config)["mcpServers"]
    url = retarget(next(iter(servers.values()))["url"])
    rpc(url, 1, "initialize", {"protocolVersion": "2025-06-18"})
    for index, (name, arguments) in enumerate(calls, start=2):
        rpc(url, index, "tools/call", {"name": name, "arguments": arguments})


def next_invocation(script: str) -> list[list[object]]:
    """This invocation's calls, and move the cursor on for the next one."""
    invocations = json.loads(open(script, encoding="utf-8").read())
    cursor_path = script + ".cursor"
    try:
        cursor = int(open(cursor_path, encoding="utf-8").read())
    except OSError:
        cursor = 0
    with open(cursor_path, "w", encoding="utf-8") as handle:
        handle.write(str(cursor + 1))
    if cursor >= len(invocations):
        return []
    return list(invocations[cursor])


def retarget(url: str) -> str:
    """Aim at a node this invocation was not invoked about, when asked to."""
    node = os.environ.get("FAKE_CLAUDE_TOOL_NODE")
    if not node:
        return url
    head, _, tail = url.partition("/nodes/")
    return f"{head}/nodes/{node}/" + tail.split("/", 1)[1]


def rpc(url: str, request_id: int, method: str, params: dict[str, object]) -> None:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        # A refused call is something a real agent reads and lives with.
        with exc:
            exc.read()


def value_of(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def positional_after(argv: list[str], flag: str) -> str | None:
    """The message, when it is on the command line rather than on stdin."""
    if flag not in argv:
        return None
    index = argv.index(flag) + 1
    if index >= len(argv) or argv[index].startswith("-"):
        return None
    return argv[index]


def emit(event: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
