"""The control loop.

At this ticket the loop drives one node — the run's root — and the shape it
drives it in is the shape every node will be driven in: dispatch, read the
stream to the `result` event that *is* stale, and hand the moment to a fresh
ephemeral orchestrator agent. The agent either messages the session onward, in
which case the loop keeps reading, or marks the node complete, in which case
the loop stops. Nothing else advances a node.

The loop never reads the agent's prose. Every decision it acts on arrives as a
tool call that already landed as validated state.

Everything here runs on one thread: the asyncio loop's. That is not an
implementation detail, it is the reason "one writer per file" holds without a
lock once several sessions run at once — and it is why the tool server hands
every call it receives over to this thread before anything is touched.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from auto.agent.invoke import OrchestratorAgent
from auto.agent.prompt import intervention_message, stable_system_prompt
from auto.agent.trace import render as render_trace
from auto.config import ConfigOverrides, resolve_config
from auto.errors import AutoError
from auto.model import (
    ROOT_NODE_ID,
    InterventionRecord,
    InterventionTrigger,
    Manifest,
    NodeStatus,
    Route,
    RootNode,
    RunStatus,
    SessionRecord,
    SessionStatus,
)
from auto.preflight import preflight
from auto.run import RunDirectory, TranscriptWriter, allocate_run_id
from auto.session.events import (
    StreamEvent,
    is_result,
    result_summary,
    telemetry_from_result,
)
from auto.session.protocol import LaunchedSession, Launcher, LaunchSpec
from auto.tools.harness import COMPLETE_NODE, SEND_TO_SESSION, ToolResult
from auto.tools.server import ToolServer

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INTERRUPTED = 130

NO_RESULT_NOTE = "session ended without a result event"
NO_ACTION_NOTE = (
    "the orchestrator agent neither messaged the session nor completed the "
    "node, and a session that has gone stale does nothing further on its own"
)


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


def prepare_run(request: RunRequest, *, clock: Clock = utcnow) -> PreparedRun:
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


class NodeTools:
    """The loop side of the harness tools, bound to one node.

    Every method here runs on the orchestrator's loop thread: the tool server
    takes a call off an HTTP thread and hands it over before anything is
    touched, so these are ordinary writes under the same single-writer
    invariant as the rest of the run directory.

    Note what is *not* here: nothing writes a tracker file, and nothing reaches
    into the target repo. The orchestrator lands judgments as harness state and
    asks the session to persist everything else itself.
    """

    def __init__(
        self,
        *,
        run: RunDirectory,
        manifest: Manifest,
        node: RootNode,
        record: SessionRecord,
        session: LaunchedSession,
    ) -> None:
        self._run = run
        self._manifest = manifest
        self._node = node
        self._record = record
        self._session = session

    async def send_to_session(
        self, message: str, highlights: Sequence[str]
    ) -> ToolResult:
        try:
            await self._session.send(message)
        except AutoError as exc:
            return ToolResult(str(exc), is_error=True)
        self._note(highlights)
        return ToolResult(f"delivered to the session for node {self._node.node_id}")

    async def complete_node(
        self, summary: str, highlights: Sequence[str]
    ) -> ToolResult:
        self._record.summary = summary
        self._node.status = NodeStatus.DONE
        self._note(highlights)
        self._run.write_manifest(self._manifest)
        return ToolResult(f"node {self._node.node_id} marked complete")

    def _note(self, highlights: Sequence[str]) -> None:
        self._record.highlights.extend(highlights)
        self._run.write_session(self._record)


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
        self._agent: OrchestratorAgent | None = None
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
        """Drive the root node until an intervention says it is finished."""
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

        tools = self._start_judging()
        try:
            outcome, note = await self._drive(record)
        except AutoError as exc:
            self._finish(record, Outcome.FAILED, note=str(exc))
            raise
        finally:
            tools.close()

        self._finish(record, outcome, note=note)
        return self._manifest

    def _start_judging(self) -> ToolServer:
        """Stand the tools up and write the agent's prompt, once for the run.

        The stable half of the prompt is assembled here and nowhere else: every
        invocation appends this same string to its system prompt, so hundreds
        of them share one cached prefix. Anything per-node that leaked in here
        would quietly cost the run that prefix.
        """
        system_prompt = stable_system_prompt(self._manifest)
        self._run.write_orchestrator_prompt(system_prompt)
        tools = ToolServer(asyncio.get_running_loop())
        self._agent = OrchestratorAgent(
            run=self._run,
            launcher=self._launcher,
            tools=tools,
            model=self._manifest.config.orchestrator_model,
            system_prompt=system_prompt,
            cwd=Path(self._manifest.target_repo),
            clock=self._clock,
            session_id_factory=self._new_session_id,
        )
        return tools

    async def _drive(self, record: SessionRecord) -> tuple[Outcome, str | None]:
        """Dispatch the root node, then alternate stale points and judgments.

        The dispatch carries the entry skill's invocation and the pasted prompt
        and nothing else: no obligations, no reminders, no harness vocabulary.
        """
        node = self._manifest.root_node
        if self.aborting():
            # The interrupt landed before dispatch: stopping dispatching means
            # this session is never started at all.
            return Outcome.ABORTED, None

        session = await self._launcher.launch(
            LaunchSpec(
                node_id=ROOT_NODE_ID,
                session_id=record.session_id,
                cwd=Path(self._manifest.target_repo),
                message=f"{node.type.skill_invocation} {node.prompt.strip()}",
                max_budget_usd=self._manifest.config.session_budget_usd,
            )
        )
        self._session = session

        seen: list[StreamEvent] = []
        stream = session.events()
        try:
            with self._run.open_transcript(record.session_id) as transcript:
                while True:
                    stale = await self._read_to_stale(stream, transcript, seen)
                    if stale is None:
                        if self.aborting():
                            return Outcome.ABORTED, None
                        return Outcome.FAILED, NO_RESULT_NOTE
                    self._absorb(record, stale)
                    if self.aborting():
                        return Outcome.ABORTED, None
                    if record.telemetry.is_error:
                        return Outcome.FAILED, None

                    intervention = await self._intervene(record, session, seen)
                    if _acted(intervention, COMPLETE_NODE):
                        return Outcome.COMPLETE, None
                    if not _acted(intervention, SEND_TO_SESSION):
                        # A no-op intervention is legitimate — it means the node
                        # is still working — but nothing will wake an idle
                        # headless session, so there is no next stale point to
                        # wait for. #11 replaces this with the nudge budget.
                        return Outcome.FAILED, NO_ACTION_NOTE
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
            await session.terminate()

    async def _read_to_stale(
        self,
        stream: AsyncGenerator[StreamEvent, None],
        transcript: TranscriptWriter,
        seen: list[StreamEvent],
    ) -> StreamEvent | None:
        """Read one turn, capturing it, and stop at the moment it goes stale."""
        async for event in stream:
            transcript.write(event)
            seen.append(event)
            if self._on_event is not None:
                self._on_event(self, event)
            if is_result(event):
                return event
            if self.aborting():
                return None
        return None

    def _absorb(self, record: SessionRecord, stale: StreamEvent) -> None:
        """Record what the turn cost and said, whatever the run does next."""
        record.telemetry = telemetry_from_result(stale)
        summary = result_summary(stale)
        if summary is not None:
            record.summary = summary
        self._manifest.driven_spend_usd += record.telemetry.cost_usd or 0.0
        self._run.write_session(record)
        self._run.write_manifest(self._manifest)

    async def _intervene(
        self,
        record: SessionRecord,
        session: LaunchedSession,
        seen: Sequence[StreamEvent],
    ) -> InterventionRecord:
        """Invoke a fresh agent on this node's stale point."""
        assert self._agent is not None
        node = self._manifest.root_node
        intervention = await self._agent.intervene(
            node=node.node_id,
            trigger=InterventionTrigger.STALE,
            message=intervention_message(
                self._manifest,
                node_id=node.node_id,
                node_type=node.type,
                node_status=node.status,
                trigger=InterventionTrigger.STALE,
                trace=render_trace(node.type, seen),
            ),
            tools=NodeTools(
                run=self._run,
                manifest=self._manifest,
                node=node,
                record=record,
                session=session,
            ),
        )
        # Counted apart from the session's own spend, so what the harness costs
        # to run is measurable rather than folded into what it drove.
        self._manifest.orchestrator_spend_usd += intervention.telemetry.cost_usd or 0.0
        self._run.write_manifest(self._manifest)
        return intervention

    def _finish(
        self,
        record: SessionRecord,
        outcome: Outcome,
        *,
        note: str | None = None,
    ) -> None:
        """Write the run's terminal state. The only place statuses land."""
        if self.aborting():
            outcome = Outcome.ABORTED
        if note is not None:
            record.summary = note

        record.status, self._manifest.root_node.status, self._manifest.status = (
            _STATUSES[outcome]
        )
        record.ended_at = self._clock()
        self._manifest.ended_at = record.ended_at
        self._run.write_session(record)
        self._run.write_manifest(self._manifest)


def _acted(intervention: InterventionRecord, tool: str) -> bool:
    """Whether a tool call of this name actually took effect."""
    return any(call.tool == tool and call.accepted for call in intervention.tool_calls)


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
