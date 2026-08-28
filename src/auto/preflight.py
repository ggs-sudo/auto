"""Preflight: find out in the first second, not forty minutes and several
dollars later.

Three things fail fast, two warn, and nothing is ever created — the harness
does not set a target repo up. A target repo is assumed to already carry the
skills and the local-markdown tracker convention.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from auto.errors import PreflightError

TRACKER_DOC_PATH = "docs/agents/issue-tracker.md"
"""The target repo's tracker doc. Its presence is the contract; its flavour is
only a warning, because the harness reads tickets, not the doc."""

EFFORT_ROOT_DIR_NAME = ".scratch"
"""The directory efforts live *in*: one `.scratch/<effort>/` per effort, each
holding its tickets and the graph derived from them."""


@dataclass(frozen=True)
class PreflightResult:
    """The target repo, and the state it was in when the run was pointed at it."""

    repo: Path
    """The worktree root, which is what a run is actually pointed at."""

    branch: str | None
    head: str | None
    dirty: bool
    warnings: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def preflight(target: Path) -> PreflightResult:
    """Check the target repo and read the state it is in. Never writes."""
    target = target.expanduser()
    if not target.is_dir():
        raise PreflightError(f"target repo does not exist: {target}")
    target = target.resolve()

    toplevel = _git(target, "rev-parse", "--show-toplevel")
    if toplevel.returncode != 0:
        raise PreflightError(f"{target} is not a git repository")
    repo = Path(toplevel.stdout.strip()).resolve()

    tracker_doc = repo / TRACKER_DOC_PATH
    if not tracker_doc.is_file():
        raise PreflightError(
            f"{repo} has no tracker doc at {TRACKER_DOC_PATH}; "
            "the harness drives repos that already carry one, and creates nothing"
        )

    # Probe a path *inside* the effort directory: that matches both the
    # `.scratch/` and the bare `.scratch` forms of the ignore rule, where
    # probing the bare name only matches the latter.
    effort_probe = f"{EFFORT_ROOT_DIR_NAME}/probe"
    if _git(repo, "check-ignore", "--quiet", effort_probe).returncode != 0:
        raise PreflightError(
            f"{repo} does not gitignore {EFFORT_ROOT_DIR_NAME}/; harness bookkeeping "
            "would land in your commits"
        )

    warnings: list[str] = []
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    if dirty:
        warnings.append(
            f"{repo} has a dirty working tree; proceeding, since the harness "
            "never touches your branches"
        )
    if EFFORT_ROOT_DIR_NAME not in tracker_doc.read_text():
        warnings.append(
            f"unrecognised tracker flavour in {TRACKER_DOC_PATH}: it does not "
            f"mention {EFFORT_ROOT_DIR_NAME}/, so sessions may write tickets the "
            "harness will not find"
        )

    return PreflightResult(
        repo=repo,
        branch=_branch(repo),
        head=_head(repo),
        dirty=dirty,
        warnings=warnings,
    )


def _branch(repo: Path) -> str | None:
    result = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _head(repo: Path) -> str | None:
    result = _git(repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
