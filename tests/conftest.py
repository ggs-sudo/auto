from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TRACKER_DOC_BODY = """# Issue tracker: local markdown

Tickets live under `.scratch/<effort>/issues/`, one Markdown file each.
"""


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` with an identity, so tests can commit anywhere."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.email=tests@example.invalid",
            "-c",
            "user.name=auto tests",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def make_target_repo(
    path: Path,
    *,
    git_init: bool = True,
    tracker_doc: str | None = TRACKER_DOC_BODY,
    gitignore: str | None = ".scratch/\n",
    commit: bool = True,
) -> Path:
    """A target repo in whatever state a preflight test needs it."""
    path.mkdir(parents=True, exist_ok=True)
    if tracker_doc is not None:
        doc = path / "docs" / "agents" / "issue-tracker.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(tracker_doc)
    if gitignore is not None:
        (path / ".gitignore").write_text(gitignore)
    if git_init:
        git(path, "init", "--initial-branch=main", "--quiet")
        if commit:
            git(path, "add", "-A")
            git(path, "commit", "--quiet", "-m", "Initial commit")
    return path


MAP_BODY = """## Destination

Search on the settings page, good enough to ship.

## Decisions so far
"""

TICKET_BODY = """# 01: Index the settings content

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent
"""

SPEC_BODY = """## Problem Statement

Settings are long and nobody can find anything.
"""

CHARTED = {
    ".scratch/add-search/map.md": MAP_BODY,
    ".scratch/add-search/issues/01-index.md": TICKET_BODY,
}
"""What a wayfinder session leaves behind: a map, and a ticket beside it.

The owed-artifact table is the harness's knowledge of this, so a fixture that
skipped it would be testing a session that forgot — which is its own case.
"""

SPECCED = {
    ".scratch/add-search/spec.md": SPEC_BODY,
    ".scratch/add-search/issues/01-index.md": TICKET_BODY,
}
"""What a grilling session leaves behind once its chain has run to tickets."""


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """A well-formed target repo: git, tracker doc, gitignored effort dirs."""
    return make_target_repo(tmp_path / "target")


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """A temporary stand-in for `~/.auto`."""
    return tmp_path / "state"


@pytest.fixture
def brief_graces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the loop's two waiting-it-out periods to nothing.

    Both are wall-clock in production, because what they are waiting on is:
    a background task reporting, or a session proving the loop wrong about
    nothing being able to wake it. Nothing under test depends on their size,
    only on their being spent before the loop gives up — so a test that reaches
    either one asks for this rather than sitting through a minute of real time.
    """
    from auto import orchestrate

    monkeypatch.setattr(orchestrate, "STREAM_POLL_SECONDS", 0.005)
    monkeypatch.setattr(orchestrate, "IDLE_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(orchestrate, "BACKGROUND_GRACE_SECONDS", 0.02)


def dead_pid() -> int:
    """A pid no process holds: a child that has already exited and been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def init_event(session_id: str = "fixture-session") -> dict[str, object]:
    return {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
        "model": "claude-opus-5",
        "tools": ["Read", "Edit"],
        "permissionMode": "bypassPermissions",
    }


def assistant_event(text: str, session_id: str = "fixture-session") -> dict[str, object]:
    return {
        "type": "assistant",
        "session_id": session_id,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def result_event(
    text: str = "Done.",
    *,
    session_id: str = "fixture-session",
    is_error: bool = False,
    cost_usd: float = 0.42,
    num_turns: int = 3,
) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "error_during_execution" if is_error else "success",
        "is_error": is_error,
        "result": text,
        "session_id": session_id,
        "num_turns": num_turns,
        "duration_ms": 2283,
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "total_cost_usd": cost_usd,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "modelUsage": {"claude-opus-5": {"costUSD": cost_usd}},
        "permission_denials": [],
    }


def background_tasks_event(
    *task_ids: str, session_id: str = "fixture-session"
) -> dict[str, object]:
    """The snapshot the CLI sends whenever its set of background tasks changes.

    The whole live set, every time — so no arguments is how a set that has
    drained arrives.
    """
    return {
        "type": "system",
        "subtype": "background_tasks_changed",
        "session_id": session_id,
        "tasks": [
            {
                "task_id": task_id,
                "task_type": "local_agent",
                "description": f"review {task_id}",
            }
            for task_id in task_ids
        ],
    }


def waiting_turn(
    text: str, *task_ids: str, **kwargs: object
) -> list[dict[str, object]]:
    """A turn that ends with background work still running.

    A turn boundary, not a stale point: the CLI wakes this session itself when
    the tasks report, so the harness has nothing to do but read on.
    """
    return [
        init_event(),
        background_tasks_event(*task_ids),
        assistant_event(text),
        result_event(text, **kwargs),  # type: ignore[arg-type]
    ]


def drained_turn(text: str, **kwargs: object) -> list[dict[str, object]]:
    """The turn a session wakes itself into, its background work reported."""
    return [
        background_tasks_event(),
        assistant_event(text),
        result_event(text, **kwargs),  # type: ignore[arg-type]
    ]


def one_turn(text: str = "Done.", **kwargs: object) -> list[dict[str, object]]:
    """The events of a session that runs one turn and goes stale.

    The result event reports the turn's last assistant message, as a real
    stream does, so a turn is recognisable by what the session said in it.
    """
    return [
        init_event(),
        assistant_event(text),
        result_event(text, **kwargs),  # type: ignore[arg-type]
    ]


def write_fixture(path: Path, events: list[dict[str, object]]) -> Path:
    """Write recorded events in the transcript capture format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


async def mcp_request(
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: int | None = 1,
) -> tuple[int, dict[str, Any] | None]:
    """One JSON-RPC request against a live tool server, over real HTTP.

    On a thread, because the server hands every tool call back to the loop: a
    request made from the loop itself would be waiting on the thread that has
    to answer it.
    """
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    return await asyncio.to_thread(_post, url, json.dumps(payload).encode("utf-8"))


def _post(url: str, body: bytes) -> tuple[int, dict[str, Any] | None]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


async def call_tool(
    url: str, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """`tools/call`, unwrapped to the tool result the agent would see."""
    _, body = await mcp_request(
        url, "tools/call", {"name": name, "arguments": arguments or {}}
    )
    assert body is not None
    result: dict[str, Any] = body["result"]
    return result


def tool_text(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])
