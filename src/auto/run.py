"""Run-directory creation and I/O.

This module is the single-writer boundary: every write under
`<state-dir>/runs/<run-id>/` happens here, and every one of those calls happens
on the orchestrator's loop thread. That is what makes "one writer per file"
cheap under concurrency — the invariant is a property of where the code runs,
not of a lock.

    run.json                            # manifest
    orchestrator-prompt.md              # the agent's stable prompt, written once
    sessions/<session-id>.json          # one record per session
    transcripts/<session-id>.jsonl      # captured stream-json
    interventions/<intervention-id>.json  # one record per agent invocation

Writes are atomic (temp file plus rename) so a reader — the monitoring website,
or `auto show` — never sees half a file.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self, TypeVar

from pydantic import BaseModel, ValidationError

from auto.errors import UsageError
from auto.model import InterventionRecord, Manifest, SessionRecord

RUNS_DIR_NAME = "runs"
MANIFEST_FILENAME = "run.json"
SESSIONS_DIR_NAME = "sessions"
TRANSCRIPTS_DIR_NAME = "transcripts"
INTERVENTIONS_DIR_NAME = "interventions"
ORCHESTRATOR_PROMPT_FILENAME = "orchestrator-prompt.md"

_ModelT = TypeVar("_ModelT", bound=BaseModel)

RUN_ID_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
MAX_SLUG_LENGTH = 29


def slugify(text: str) -> str:
    """A short, readable stem for a run id. Never empty."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > MAX_SLUG_LENGTH:
            break
        slug = candidate
    if not slug:
        slug = words[0][:MAX_SLUG_LENGTH] if words else "run"
    return slug


def runs_dir(state_dir: Path) -> Path:
    return state_dir / RUNS_DIR_NAME


def allocate_run_id(state_dir: Path, created_at: datetime, prompt: str) -> str:
    """`YYYYMMDD-HHMMSS-<slug>`, suffixed until it names no existing run."""
    stem = f"{created_at.strftime(RUN_ID_TIMESTAMP_FORMAT)}-{slugify(prompt)}"
    parent = runs_dir(state_dir)
    candidate, suffix = stem, 1
    while (parent / candidate).exists():
        suffix += 1
        candidate = f"{stem}-{suffix}"
    return candidate


def _require_owner_thread(owner_thread: int, what: str) -> None:
    """Every run-directory write happens on the loop thread.

    That is what makes "one writer per file" cheap under concurrency: the
    invariant is a property of where the code runs, not of a lock. This guard
    turns a violation into a loud error rather than a corrupted run.
    """
    if threading.get_ident() != owner_thread:
        raise RuntimeError(
            f"{what} was attempted off the loop thread; all run-directory "
            "writes must happen on it"
        )


class TranscriptWriter:
    """Appends captured stream-json events, one JSON object per line.

    Flushed after every event, because the website tails this file while the
    session is still running.
    """

    def __init__(self, path: Path, owner_thread: int) -> None:
        self.path = path
        self._owner_thread = owner_thread
        self._handle: IO[str] = path.open("a", encoding="utf-8")

    def write(self, event: Any) -> None:
        _require_owner_thread(self._owner_thread, f"writing {self.path.name}")
        self._handle.write(json.dumps(event) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


@dataclass(frozen=True)
class RunDirectory:
    """One run's state on disk."""

    path: Path
    owner_thread: int = field(default_factory=threading.get_ident, compare=False)
    """The thread allowed to write here — the loop thread that created the run."""

    @classmethod
    def create(cls, state_dir: Path, manifest: Manifest) -> RunDirectory:
        """Lay out a new run directory and write its manifest."""
        run = cls(runs_dir(state_dir) / manifest.run_id)
        run.sessions_dir.mkdir(parents=True, exist_ok=True)
        run.transcripts_dir.mkdir(parents=True, exist_ok=True)
        run.interventions_dir.mkdir(parents=True, exist_ok=True)
        run.write_manifest(manifest)
        return run

    @property
    def run_id(self) -> str:
        return self.path.name

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_FILENAME

    @property
    def sessions_dir(self) -> Path:
        return self.path / SESSIONS_DIR_NAME

    @property
    def transcripts_dir(self) -> Path:
        return self.path / TRANSCRIPTS_DIR_NAME

    @property
    def interventions_dir(self) -> Path:
        return self.path / INTERVENTIONS_DIR_NAME

    @property
    def orchestrator_prompt_path(self) -> Path:
        return self.path / ORCHESTRATOR_PROMPT_FILENAME

    def write_manifest(self, manifest: Manifest) -> None:
        _require_owner_thread(self.owner_thread, "writing the manifest")
        _write_model(self.manifest_path, manifest)

    def read_manifest(self) -> Manifest:
        return _read_model(self.manifest_path, Manifest)

    def session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def write_session(self, record: SessionRecord) -> None:
        _require_owner_thread(self.owner_thread, "writing a session record")
        _write_model(self.session_path(record.session_id), record)

    def read_session(self, session_id: str) -> SessionRecord:
        return _read_model(self.session_path(session_id), SessionRecord)

    def session_records(self) -> list[SessionRecord]:
        return [
            _read_model(path, SessionRecord)
            for path in sorted(self.sessions_dir.glob("*.json"))
        ]

    def write_orchestrator_prompt(self, prompt: str) -> None:
        """The agent's stable material, written once when the run starts.

        On disk as well as on every command line because it is the run's
        standing brief: what the harness will be judging by for hours, in a
        file a reader can open rather than a flag they have to reconstruct.
        """
        _require_owner_thread(self.owner_thread, "writing the orchestrator prompt")
        _write_atomically(self.orchestrator_prompt_path, prompt)

    def intervention_path(self, intervention_id: str) -> Path:
        return self.interventions_dir / f"{intervention_id}.json"

    def write_intervention(self, record: InterventionRecord) -> None:
        _require_owner_thread(self.owner_thread, "writing an intervention record")
        _write_model(self.intervention_path(record.intervention_id), record)

    def intervention_records(self) -> list[InterventionRecord]:
        """Every intervention in the run, in the order they were made."""
        return [
            _read_model(path, InterventionRecord)
            for path in sorted(self.interventions_dir.glob("*.json"))
        ]

    def transcript_path(self, session_id: str) -> Path:
        return self.transcripts_dir / f"{session_id}.jsonl"

    def relative_transcript_path(self, session_id: str) -> str:
        return f"{TRANSCRIPTS_DIR_NAME}/{session_id}.jsonl"

    def open_transcript(self, session_id: str) -> TranscriptWriter:
        _require_owner_thread(self.owner_thread, "opening a transcript")
        return TranscriptWriter(self.transcript_path(session_id), self.owner_thread)


def load_run(state_dir: Path, run_id: str) -> RunDirectory:
    run = RunDirectory(runs_dir(state_dir) / run_id)
    if not run.manifest_path.is_file():
        raise UsageError(f"no such run: {run_id}")
    return run


def list_runs(state_dir: Path) -> list[Manifest]:
    """Every readable run's manifest, newest first.

    Run ids lead with a timestamp, so sorting by name sorts by time. A run the
    current schema cannot read is skipped rather than failing the listing —
    `auto runs` exists to find a run, and one bad directory should not hide
    every other.
    """
    parent = runs_dir(state_dir)
    if not parent.is_dir():
        return []
    manifests = []
    for path in sorted(parent.iterdir(), reverse=True):
        manifest_path = path / MANIFEST_FILENAME
        if not manifest_path.is_file():
            continue
        try:
            manifests.append(_read_model(manifest_path, Manifest))
        except UsageError:
            continue
    return manifests


def _write_model(path: Path, model: BaseModel) -> None:
    _write_atomically(path, model.model_dump_json(indent=2) + "\n")


def _write_atomically(path: Path, content: str) -> None:
    """Temp file plus rename, so a concurrent reader sees old or new, never half."""
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def _read_model(path: Path, model: type[_ModelT]) -> _ModelT:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
