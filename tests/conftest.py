from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """A well-formed target repo: git, tracker doc, gitignored effort dirs."""
    return make_target_repo(tmp_path / "target")


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """A temporary stand-in for `~/.auto`."""
    return tmp_path / "state"


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


def one_turn(text: str = "Done.", **kwargs: object) -> list[dict[str, object]]:
    """The events of a session that runs one turn and goes stale."""
    return [
        init_event(),
        assistant_event("Working on it."),
        result_event(text, **kwargs),  # type: ignore[arg-type]
    ]


def write_fixture(path: Path, events: list[dict[str, object]]) -> Path:
    """Write recorded events in the transcript capture format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path
