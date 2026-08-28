"""The control loop.

At this ticket the loop does exactly one thing: dispatch the run's root node,
tee its stream to the captured transcript, notice it go stale at the `result`
event, record what happened, and exit. Nothing judges anything yet — no
orchestrator agent, no graph, no gates.

Everything here runs on one thread: the asyncio loop's. That is not an
implementation detail, it is the reason "one writer per file" holds without a
lock once several sessions run at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from auto.config import ConfigOverrides, resolve_config
from auto.errors import AutoError
from auto.model import (
    ROOT_NODE_ID,
    Manifest,
    NodeStatus,
    Route,
    RootNode,
    RunStatus,
    SessionRecord,
    SessionStatus,
    Telemetry,
)
from auto.preflight import preflight
from auto.run import RunDirectory, allocate_run_id
from auto.session.events import (
    StreamEvent,
    is_result,
    result_summary,
    telemetry_from_result,
)
from auto.session.protocol import LaunchedSession, Launcher, LaunchSpec

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INTERRUPTED = 130

class Outcome(StrEnum):
    """How a node's session ended.

    One outcome fixes all three statuses at once — the session's, the node's
    and the run's — so they cannot be set out of step with each other.
    """

    COMPLETE = "complete"
    FAILED = "failed"
    ABORTED = "aborted"


_STATUSES: dict[Outcome, tuple[SessionStatus, NodeStatus, RunStatus]] = {
    Outcome.COMPLETE: (SessionStatus.SUCCEEDED, NodeStatus.DONE, RunStatus.DONE),
    Outcome.FAILED: (SessionStatus.FAILED, NodeStatus.FAILED, RunStatus.FAILED),
    Outcome.ABORTED: (SessionStatus.FAILED, NodeStatus.FAILED, RunStatus.ABORTED),
}

Clock = Callable[[], datetime]
SessionIdFactory = Callable[[], str]
EventHook = Callable[["Orchestrator", StreamEvent], None]


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_session_id() -> str:
    """Pre-assigned, so the harness knows a session's id before it launches."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class RunRequest:
    """What the operator asked for."""

    route: Route
    prompt: str
    target_repo: Path
    state_dir: Path
    overrides: ConfigOverrides = field(default_factory=ConfigOverrides)


@dataclass(frozen=True)
class PreparedRun:
    """A run that has passed preflight and has a directory on disk."""

    run: RunDirectory
    manifest: Manifest
    warnings: list[str]


def prepare_run(
    request: RunRequest, *, clock: Clock = utcnow
) -> PreparedRun:
    """Preflight the target repo, resolve configuration, lay out the run.

    Preflight comes first, so a repo that cannot be driven leaves no trace.
    """
    checked = preflight(request.target_repo)
    config = resolve_config(request.state_dir, request.overrides)
    created_at = clock()
    manifest = Manifest(
        run_id=allocate_run_id(request.state_dir, created_at, request.prompt),
        route=request.route,
        prompt=request.prompt,
        target_repo=str(checked.repo),
        worktree=str(checked.repo),
        branch=checked.branch,
        head=checked.head,
        dirty=checked.dirty,
        config=config,
        created_at=created_at,
        root_node=RootNode(
            type=request.route.entry_skill,
            prompt=request.prompt,
        ),
    )
    return PreparedRun(
        run=RunDirectory.create(request.state_dir, manifest),
        manifest=manifest,
        warnings=checked.warnings,
    )


class Orchestrator:
    """Drives one run. Owns every write to its run directory."""

    def __init__(
        self,
        run: RunDirectory,
        manifest: Manifest,
        launcher: Launcher,
        *,
        clock: Clock = utcnow,
        session_id_factory: SessionIdFactory = new_session_id,
        on_event: EventHook | None = None,
    ) -> None:
        self._run = run
        self._manifest = manifest
        self._launcher = launcher
        self._clock = clock
        self._new_session_id = session_id_factory
        self._on_event = on_event
        self._session: LaunchedSession | None = None
        self._aborting = False
        self._teardown: asyncio.Task[None] | None = None

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def aborting(self) -> bool:
        """Whether the run has been asked to stop dispatching and shut down.

        A method rather than a property because it must be read *fresh* at
        every decision point: an interrupt lands between two events, from
        outside the flow the reader can see.
        """
        return self._aborting

    def request_abort(self) -> None:
        """First interrupt: stop dispatching and bring the live session down."""
        if self._aborting:
            return
        self._aborting = True
        session = self._session
        if session is not None:
            with contextlib.suppress(RuntimeError):
                # Held, not fire-and-forget: an unreferenced task can be
                # collected before it has brought the session down.
                self._teardown = asyncio.get_running_loop().create_task(
                    session.terminate()
                )

    def kill_now(self) -> None:
        """Second interrupt: no graceful path, no waiting."""
        session = self._session
        if session is not None:
            session.kill()

    async def execute(self) -> Manifest:
        """Run the root node to its first stale point, then stop."""
        node = self._manifest.root_node
        session_id = self._new_session_id()
        node.session_id = session_id
        node.status = NodeStatus.IN_PROGRESS
        self._run.write_manifest(self._manifest)

        record = SessionRecord(
            session_id=session_id,
            node=ROOT_NODE_ID,
            role=node.type,
            started_at=self._clock(),
            captured_transcript=self._run.relative_transcript_path(session_id),
        )
        self._run.write_session(record)

        try:
            stale = await self._drive(session_id, node.prompt)
        except AutoError as exc:
            self._finish(record, stale=None, note=str(exc))
            raise

        self._finish(record, stale=stale)
        return self._manifest

    async def _drive(self, session_id: str, prompt: str) -> StreamEvent | None:
        """Dispatch the root node and read its stream until it goes stale.

        The dispatch carries the entry skill's invocation and the pasted prompt
        and nothing else: no obligations, no reminders, no harness vocabulary.
        """
        node = self._manifest.root_node
        if self.aborting():
            # The interrupt landed before dispatch: stopping dispatching means
            # this session is never started at all.
            return None
        spec = LaunchSpec(
            node_id=ROOT_NODE_ID,
            session_id=session_id,
            cwd=Path(self._manifest.target_repo),
            message=f"{node.type.skill_invocation} {prompt.strip()}",
            max_budget_usd=self._manifest.config.session_budget_usd,
        )
        session = await self._launcher.launch(spec)
        self._session = session

        stale: StreamEvent | None = None
        stream = session.events()
        try:
            with self._run.open_transcript(session_id) as transcript:
                async for event in stream:
                    transcript.write(event)
                    if self._on_event is not None:
                        self._on_event(self, event)
                    if is_result(event):
                        stale = event
                        break
                    if self.aborting():
                        break
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
            await session.terminate()
        return stale

    def _finish(
        self,
        record: SessionRecord,
        *,
        stale: StreamEvent | None,
        note: str | None = None,
    ) -> None:
        """Write the run's terminal state. The only place statuses land."""
        telemetry: Telemetry | None = None
        if stale is not None:
            # A turn that ended cost what it cost, whatever the run does next.
            telemetry = telemetry_from_result(stale)
            record.telemetry = telemetry
            record.summary = result_summary(stale)
            self._manifest.driven_spend_usd += telemetry.cost_usd or 0.0

        if self.aborting():
            outcome = Outcome.ABORTED
        elif telemetry is None:
            outcome = Outcome.FAILED
            record.summary = note or "session ended without a result event"
        else:
            outcome = Outcome.FAILED if telemetry.is_error else Outcome.COMPLETE

        record.status, self._manifest.root_node.status, self._manifest.status = (
            _STATUSES[outcome]
        )
        record.ended_at = self._clock()
        self._manifest.ended_at = record.ended_at
        self._run.write_session(record)
        self._run.write_manifest(self._manifest)


def execute_run(
    prepared: PreparedRun,
    launcher: Launcher,
    *,
    clock: Clock = utcnow,
    session_id_factory: SessionIdFactory = new_session_id,
    on_event: EventHook | None = None,
    handle_interrupts: bool = False,
) -> Manifest:
    """Drive a prepared run to completion. Blocks until the run ends."""

    async def main() -> Manifest:
        orchestrator = Orchestrator(
            prepared.run,
            prepared.manifest,
            launcher,
            clock=clock,
            session_id_factory=session_id_factory,
            on_event=on_event,
        )
        if handle_interrupts:
            InterruptHandler(orchestrator).install()
        return await orchestrator.execute()

    return asyncio.run(main())


def exit_code_for(manifest: Manifest) -> int:
    """So a script can tell a finished run from a failed or interrupted one."""
    if manifest.status is RunStatus.ABORTED:
        return EXIT_INTERRUPTED
    if manifest.status is RunStatus.FAILED:
        return EXIT_FAILED
    return EXIT_OK


class InterruptHandler:
    """First interrupt stops the run cleanly; a second kills immediately.

    A graceful path that has itself hung must never be the only way out, so the
    second interrupt does not wait for anything — not the session, not the
    remaining writes.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        exit_now: Callable[[int], None] = os._exit,
        announce: Callable[[str], None] = print,
    ) -> None:
        self._orchestrator = orchestrator
        self._exit_now = exit_now
        self._announce = announce
        self.interrupts = 0

    def __call__(self) -> None:
        self.interrupts += 1
        if self.interrupts == 1:
            self._announce("\ninterrupted: stopping the run and its session")
            self._orchestrator.request_abort()
            return
        self._announce("\ninterrupted again: killing now")
        self._orchestrator.kill_now()
        self._exit_now(EXIT_INTERRUPTED)

    def install(self) -> None:
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGINT, self)
