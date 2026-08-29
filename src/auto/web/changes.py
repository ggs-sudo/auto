"""Noticing that a run changed, without being told.

The server is independent of any run process, so nothing announces a write to
it — it looks. One `tick()` fingerprints every run from file stats (path,
mtime, size) over the run's state directory *and* its graph files in the
target repo, and reports which runs changed since the last tick. The watcher
calls `tick()` when the filesystem stirs; the polling fallback calls it on a
timer; the endpoints and the notification payloads are identical either way,
which is what makes polling a fallback rather than a second mode.

Versions are monotonic per run and exist so a client can cheaply tell "the
run I am showing moved" from a notification it already acted on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from auto.graph import GRAPH_FILENAME
from auto.model import Manifest
from auto.owed import EFFORT_ROOT
from auto.run import MANIFEST_FILENAME, runs_dir

Fingerprint = frozenset[tuple[str, int, int]]


@dataclass(frozen=True)
class RunChange:
    """One run that moved: the notification the SSE stream carries."""

    run_id: str
    version: int


class ChangeTracker:
    """Fingerprints per run, and the versions their changes bump."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._fingerprints: dict[str, Fingerprint] = {}
        self._versions: dict[str, int] = {}

    @property
    def versions(self) -> dict[str, int]:
        """Current version per run — the baseline a fresh client starts from."""
        return dict(self._versions)

    def tick(self) -> list[RunChange]:
        """Rescan everything and report the runs that changed.

        A run appearing or disappearing is a change like any other; a
        disappeared run's version survives, so a directory restored from
        backup still notifies.
        """
        changes: list[RunChange] = []
        current: dict[str, Fingerprint] = {}
        parent = runs_dir(self._state_dir)
        if parent.is_dir():
            for path in sorted(parent.iterdir()):
                if not (path / MANIFEST_FILENAME).is_file():
                    continue
                current[path.name] = self._fingerprint(path)

        for run_id, fingerprint in current.items():
            if self._fingerprints.get(run_id) == fingerprint:
                continue
            self._versions[run_id] = self._versions.get(run_id, 0) + 1
            changes.append(RunChange(run_id, self._versions[run_id]))
        for run_id in set(self._fingerprints) - set(current):
            self._versions[run_id] = self._versions.get(run_id, 0) + 1
            changes.append(RunChange(run_id, self._versions[run_id]))

        self._fingerprints = current
        return changes

    def watch_roots(self) -> list[Path]:
        """What the filesystem watcher should stand over, existing dirs only:
        the runs directory, plus each run's effort root in its target repo —
        where the graphs change without anything under the state dir moving."""
        roots = []
        parent = runs_dir(self._state_dir)
        if parent.is_dir():
            roots.append(parent)
        seen = set()
        for scratch in (
            repo / EFFORT_ROOT for repo in self._target_repos(parent)
        ):
            if scratch in seen or not scratch.is_dir():
                continue
            seen.add(scratch)
            roots.append(scratch)
        return roots

    def _target_repos(self, parent: Path) -> list[Path]:
        if not parent.is_dir():
            return []
        repos = []
        for path in sorted(parent.iterdir()):
            manifest = _read_manifest(path / MANIFEST_FILENAME)
            if manifest is not None:
                repos.append(Path(manifest.target_repo))
        return repos

    def _fingerprint(self, run_path: Path) -> Fingerprint:
        entries = set()
        for path in run_path.rglob("*"):
            entries.add(self._stat(path, run_path))
        manifest = _read_manifest(run_path / MANIFEST_FILENAME)
        if manifest is not None:
            scratch = Path(manifest.target_repo) / EFFORT_ROOT
            for path in scratch.glob(f"*/{GRAPH_FILENAME}"):
                entries.add(self._stat(path, scratch.parent))
        return frozenset(entry for entry in entries if entry is not None)

    @staticmethod
    def _stat(path: Path, base: Path) -> tuple[str, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (str(path.relative_to(base)), stat.st_mtime_ns, stat.st_size)


def _read_manifest(path: Path) -> Manifest | None:
    """A manifest, or None for one that is unreadable mid-write or gone."""
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
