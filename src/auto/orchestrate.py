"""The control loop.

Every node is driven in the same shape: dispatch, read the stream to the point
where the node may be judged, and hand that moment to a fresh ephemeral
orchestrator agent. The agent either messages the session onward, in which
case the loop keeps reading, or marks the node terminal, in which case the
loop moves on. Nothing else advances a node.

That point is a `result` event — a turn boundary — **with nothing left running
in the session**. A turn that ends with a subagent or a backgrounded command
outstanding is not a session at rest: the CLI wakes it when the task reports,
unprompted and on the same stream, so the boundary is held and the loop keeps
reading rather than judging a session that is about to speak again (ADR-0013).

The run's first node is the root, which carries the pasted prompt. Its agent
emits the run's first graph from the tickets its session wrote, and from then
on the loop alternates two moves until nothing is left: re-derive the graphs
from the tracker files — which is how tickets written mid-run join — and keep
every ready node in flight, up to the run's concurrency cap. The cap bounds
driven sessions only; orchestrator interventions run inside each node's own
task, uncounted. The run spend ceiling gates the same point: once driven and
orchestrator spend together reach it, nothing further dispatches, though the
sessions already running are left to finish.

The loop never reads the agent's prose. Every decision it acts on arrives as a
tool call that already landed as validated state.

What the loop *does* decide by itself is arithmetic, not judgment: whether the
tracker files a node's type owes are on disk, whether a session that keeps
leaving them missing has spent its nudge budget, and which node the graph says
is ready next.

Everything here runs on one thread: the asyncio loop's. That is not an
implementation detail, it is the reason "one writer per file" holds without a
lock once several sessions run at once — and it is why the tool server hands
every call it receives over to this thread before anything is touched.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from auto import owed
from auto.agent.invoke import OrchestratorAgent
from auto.agent.prompt import intervention_message, stable_system_prompt
from auto.agent.trace import render as render_trace
from auto.config import ConfigOverrides, resolve_config
from auto.errors import AutoError, UsageError
from auto.gates import Announce, GateLedger
from auto.graph import GraphError, GraphStore
from auto.liveness import HEARTBEAT_SECONDS
from auto.model import (
    Gate,
    GateDecision,
    GateKind,
    GateResponse,
    Graph,
    GraphNode,
    InterventionRecord,
    InterventionTrigger,
    Liveness,
    Manifest,
    NodeStatus,
    NodeType,
    Route,
    RootNode,
    RunStatus,
    SessionRecord,
    SessionStatus,
    TaskResolutionMode,
    TicketType,
)
from auto.owed import Baseline, OwedArtifact, read_tracker_doc
from auto.preflight import preflight
from auto.run import RunDirectory, TranscriptWriter, allocate_run_id
from auto.session.events import (
    BackgroundWork,
    StreamEvent,
    is_result,
    result_summary,
    telemetry_from_result,
)
from auto.session.protocol import LaunchedSession, Launcher, LaunchSpec
from auto.tools.harness import (
    COMPLETE_NODE,
    ESCALATE_QUESTION,
    FAIL_NODE,
    HAND_TO_USER,
    PROTOTYPE_READY,
    SEND_TO_SESSION,
    ToolResult,
)
from auto.tools.server import ToolServer

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INTERRUPTED = 130

NO_RESULT_NOTE = "session ended without a result event"
NO_ACTION_NOTE = (
    "the orchestrator agent neither messaged the session nor completed the "
    "node, and the session then sat out its whole grace period idle, with "
    "nothing running that could have moved it"
)

STREAM_POLL_SECONDS = 1.0
"""How often reading a session's stream comes up for air.

Not a quiet-period heuristic: every decision below is still made on events the
CLI sent. This is only how often the read may be interrupted to notice an abort
or to count silence, and it is done without cancelling the read itself."""

BACKGROUND_GRACE_SECONDS = 600.0
"""How long a session that ended its turn with background work outstanding may
say *nothing at all* before the harness stops believing the task will report.

Generous on purpose. A running subagent keeps the stream warm with
`task_progress` every few seconds, so this bounds the other case: a backgrounded
command that prints nothing until it exits, and whose wall time is the test
suite's, not the harness's. It is a backstop against a task that never reports
at all — not a schedule, and never reached by a session doing ordinary work."""

IDLE_GRACE_SECONDS = 60.0
"""How long a session gets to move on its own after an intervention that asked
nothing of it.

The loop's own model says nothing will wake it. This is the insurance on that
model being incomplete: a wake-up the harness does not know how to see costs a
minute of waiting, where being wrong costs the node (ADR-0013)."""

NUDGE_BUDGET = 3
"""Consecutive nudge points a node survives before it fails.

A **nudge point** is a stale point at which the harness refused to complete the
node — the only moment it and the session are known to disagree about whether
the work is done. Only those count: a session whose agent has not yet judged it
finished is working, not stalling, and a planning conversation that runs for
twenty turns before it writes a ticket is the ordinary case rather than the
pathological one. See ADR-0004."""

CANNOT_NOTE = "the user reported that this human-only task cannot be done"

NUDGE_EXHAUSTED_NOTE = (
    "the node still owed the same tracker files at {budget} consecutive stale "
    "points, having been told each time what was absent: {missing}"
)

GATE_POLL_SECONDS = 1.0
"""How often a gated node looks for the response file beside its gate.

Polling rather than watching, because the response may be written by anything
from the website to a human hand in an editor, and a file's appearance is the
whole contract (ADR-0007)."""


class Outcome(StrEnum):
    """How a node's session ended.

    One outcome fixes the session's and the node's statuses at once, so they
    cannot be set out of step with each other. The run's own status is not
    here: it is computed once, at the end, from every node the run drove.
    """

    COMPLETE = "complete"
    FAILED = "failed"
    ABORTED = "aborted"


_STATUSES: dict[Outcome, tuple[SessionStatus, NodeStatus]] = {
    Outcome.COMPLETE: (SessionStatus.SUCCEEDED, NodeStatus.DONE),
    Outcome.FAILED: (SessionStatus.FAILED, NodeStatus.FAILED),
    Outcome.ABORTED: (SessionStatus.FAILED, NodeStatus.FAILED),
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
    entry = request.route.entry_skill
    if entry is None:
        raise UsageError(
            f"the `{request.route.value}` route has no entry skill: it is "
            "entered through `auto takeover`, never `auto run`"
        )
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
            type=entry,
            prompt=request.prompt,
        ),
    )
    logger.debug(
        "prepared run %s: route %s (entry skill %s), repo %s",
        manifest.run_id,
        manifest.route.value,
        entry.skill_invocation,
        manifest.target_repo,
    )
    return PreparedRun(
        run=RunDirectory.create(request.state_dir, manifest),
        manifest=manifest,
        warnings=checked.warnings,
    )


class _StreamReader:
    """A session's event stream, readable with a deadline and without loss.

    The loop must be able to stop waiting on a stream — to notice an abort, or
    to count how long a session has said nothing — while still meaning to read
    the same event afterwards. Cancelling the read itself would do that
    wrongly: the codec accumulates a long line across several awaits, so a
    cancelled read can drop what it had already taken off the pipe. So the read
    is started once, shielded, and kept; a wait that times out abandons the
    wait, never the read.
    """

    def __init__(self, stream: AsyncGenerator[StreamEvent, None]) -> None:
        self._stream = stream
        self._pending: asyncio.Task[StreamEvent | None] | None = None
        self._ended = False

    @property
    def ended(self) -> bool:
        """Whether the stream is finished, as opposed to merely quiet."""
        return self._ended

    async def next(self, timeout: float) -> StreamEvent | None:
        """The next event, or `None` — the stream ended, or the wait expired.

        `ended` tells those two apart.
        """
        if self._ended:
            return None
        if self._pending is None:
            self._pending = asyncio.create_task(_next_event(self._stream))
        try:
            event = await asyncio.wait_for(asyncio.shield(self._pending), timeout)
        except TimeoutError:
            return None
        self._pending = None
        if event is None:
            self._ended = True
        return event

    async def aclose(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            with contextlib.suppress(BaseException):
                await self._pending
            self._pending = None
        with contextlib.suppress(Exception):
            await self._stream.aclose()


async def _next_event(stream: AsyncGenerator[StreamEvent, None]) -> StreamEvent | None:
    """One event, or `None` at the end of the stream.

    Exhaustion as a value rather than `StopAsyncIteration`, because this is
    awaited as a task and a task is no place for that exception.
    """
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return None


@dataclass(frozen=True)
class Dispatch:
    """One node as the loop drives it: what to launch, where its state lives.

    The root node and a graph node are driven identically; the only things
    that differ — the opening message, where harness state persists, whether
    there is a ticket — are exactly the fields here.
    """

    node: RootNode | GraphNode
    node_id: str
    """Run-wide: `root`, or `<graph>/<stem>` for a node that lives in one."""

    type: NodeType
    message: str
    """The entry skill's invocation and its ticket or prompt, and nothing else."""

    ticket: str | None
    persist: Callable[[], None]
    """Write the node's harness state where it lives — the manifest for the
    root, the graph file beside the tickets for everything else."""


class NodeTools:
    """The loop side of the harness tools, bound to one node.

    Every method here runs on the orchestrator's loop thread: the tool server
    takes a call off an HTTP thread and hands it over before anything is
    touched, so these are ordinary writes under the same single-writer
    invariant as the rest of the run directory.

    Note what is *not* here: nothing writes a tracker file, and nothing reaches
    into the target repo except to read it — the graph file, harness state by
    definition, is the one exception. The orchestrator lands judgments as
    harness state and asks the session to persist everything else itself.
    """

    def __init__(
        self,
        *,
        run: RunDirectory,
        dispatch: Dispatch,
        record: SessionRecord,
        session: LaunchedSession,
        baseline: Baseline,
        graphs: GraphStore,
        gates: GateLedger,
        repo: Path,
    ) -> None:
        self._run = run
        self._dispatch = dispatch
        self._record = record
        self._session = session
        self._baseline = baseline
        self._graphs = graphs
        self._gates = gates
        self._repo = repo
        self.owed_refusals = 0
        """Completions refused for want of a tracker file, over the node's life.

        The loop reads this rather than the agent's prose: each refusal marks a
        nudge point, which is what the nudge budget counts."""

        self.open_gate: Gate | None = None
        """The gate this node is waiting at, from the moment a gate-raising
        tool lands until the loop takes its response up. What the loop polls
        on."""

        self.response_undelivered = False
        """Whether a gate response has been taken up but not yet passed to the
        session. While it is set, delivery is a precondition, not advice: the
        node cannot be completed or parked at a fresh gate, because either
        would quietly drop the user's words."""

    def still_owed(self) -> tuple[OwedArtifact, ...]:
        """What this node's type owes that the target repo cannot back up."""
        return owed.missing(
            self._dispatch.type,
            self._repo,
            ticket=self._dispatch.ticket,
            since=self._baseline,
        )

    async def send_to_session(
        self, message: str, highlights: Sequence[str]
    ) -> ToolResult:
        try:
            await self._session.send(message)
        except AutoError as exc:
            return ToolResult(str(exc), is_error=True)
        self.response_undelivered = False
        self._note(highlights)
        return ToolResult(f"delivered to the session for node {self._dispatch.node_id}")

    async def complete_node(
        self, summary: str, highlights: Sequence[str]
    ) -> ToolResult:
        undelivered = self._must_deliver_first()
        if undelivered is not None:
            return undelivered
        outstanding = self.still_owed()
        if outstanding:
            # A precondition, not advice: an agent cannot talk its way past a
            # tracker file that is not there, and the harness will not write it.
            self.owed_refusals += 1
            return ToolResult(
                f"node {self._dispatch.node_id} is not complete: it still owes "
                f"{owed.spelt_out(outstanding)}. Only the session can write "
                "those, and until they are on disk this node cannot be finished.",
                is_error=True,
            )
        self._record.summary = summary
        self._dispatch.node.status = NodeStatus.DONE
        self._note(highlights)
        self._dispatch.persist()
        return ToolResult(f"node {self._dispatch.node_id} marked complete")

    async def fail_node(self, reason: str, highlights: Sequence[str]) -> ToolResult:
        """Terminal failure. Nothing here is owed: giving up needs no artifact."""
        self._record.summary = reason
        self._dispatch.node.status = NodeStatus.FAILED
        self._note(highlights)
        self._dispatch.persist()
        return ToolResult(f"node {self._dispatch.node_id} marked failed")

    async def emit_graph(
        self,
        effort: str,
        task_modes: Mapping[str, TaskResolutionMode],
        highlights: Sequence[str],
    ) -> ToolResult:
        """Take hold of the graph this node's session charted.

        The judgment — that the tickets are genuinely finished, and how each
        `task` resolves — is the agent's; everything checkable is checked here.
        """
        if not self._dispatch.type.monitored:
            return ToolResult(
                f"node {self._dispatch.node_id} runs "
                f"`{self._dispatch.type.skill_invocation}`, which spawns no "
                "graph: only grilling and wayfinder sessions write the tickets "
                "one is derived from",
                is_error=True,
            )
        if self._graphs.holds(effort):
            return self._classify_late(effort, task_modes, highlights)
        if self._dispatch.node.graph is not None:
            return ToolResult(
                f"node {self._dispatch.node_id} already emitted the graph "
                f"`{self._dispatch.node.graph}`, and one node spawns at most "
                "one graph",
                is_error=True,
            )
        try:
            graph = self._graphs.emit(
                effort, spawned_by=self._dispatch.node_id, task_modes=task_modes
            )
        except GraphError as exc:
            return ToolResult(str(exc), is_error=True)
        self._dispatch.node.graph = effort
        self._note(highlights)
        self._dispatch.persist()
        return ToolResult(
            f"graph `{effort}` emitted for node {self._dispatch.node_id}, "
            f"with {len(graph.nodes)} node(s)"
        )

    def _classify_late(
        self,
        effort: str,
        task_modes: Mapping[str, TaskResolutionMode],
        highlights: Sequence[str],
    ) -> ToolResult:
        """The emit that arrives after the graph is held: land the modes.

        Membership joins on its own each tick, but a `task` ticket written
        after the emit still needs the agent's resolution mode — and this
        tool is the only place one can land. Refusing it outright, as the
        harness once did, stranded the task unclassified and undispatchable
        for the rest of the run.
        """
        if not task_modes:
            return ToolResult(
                f"a graph for `{effort}` was already emitted, and membership "
                "is re-derived from the tickets on every tick, so new "
                "tickets join it on their own. Only a new `task` ticket "
                "needs this tool again: call it with the task's resolution "
                "mode to classify it",
                is_error=True,
            )
        try:
            graph = self._graphs.classify(effort, task_modes)
        except GraphError as exc:
            return ToolResult(str(exc), is_error=True)
        self._note(highlights)
        classified = ", ".join(
            f"`{ref}` as {mode.value}" for ref, mode in task_modes.items()
        )
        unclassified = [
            node.node_id
            for node in graph.nodes
            if node.ticket_type is TicketType.TASK and node.task_mode is None
        ]
        text = (
            f"the graph `{effort}` was already held, so its membership needed "
            f"no emit; classified {classified}"
        )
        if unclassified:
            text += ". Still unclassified: " + ", ".join(unclassified)
        return ToolResult(text)

    async def prototype_ready(
        self, question: str, artifact: str, highlights: Sequence[str]
    ) -> ToolResult:
        """Raise the review gate this prototype's build has earned."""
        if self._dispatch.type is not NodeType.PROTOTYPE:
            return ToolResult(
                f"node {self._dispatch.node_id} runs "
                f"`{self._dispatch.type.skill_invocation}`: only a prototype "
                "session's build goes to the user for review",
                is_error=True,
            )
        return self._park_at_gate(
            GateKind.PROTOTYPE_REVIEW, question, artifact, highlights
        )

    async def hand_to_user(
        self, question: str, highlights: Sequence[str]
    ) -> ToolResult:
        """Raise the task-completion gate a human-only ticket ends at.

        Only for a `task` ticket the run classified `user` when its graph was
        emitted: that classification is the judgment that the work is
        human-only, and it was already made. The ticket itself is the gate's
        artifact — it is what the user should read before doing the work.
        """
        node = self._dispatch.node
        if not (isinstance(node, GraphNode) and node.is_user_task):
            return ToolResult(
                f"node {self._dispatch.node_id} is not a `task` ticket "
                "classified `user`: only work the run already judged "
                "human-only is handed to the user. If this session is merely "
                "stuck, message it onward instead",
                is_error=True,
            )
        return self._park_at_gate(
            GateKind.TASK_COMPLETION, question, self._dispatch.ticket, highlights
        )

    async def escalate_question(
        self, question: str, highlights: Sequence[str]
    ) -> ToolResult:
        """Raise the gate for a question only the user can answer.

        No node-type precondition: the answer policy's escape hatch is about
        the question, not the kind of session that asked it, and judging the
        question policy-critical is exactly the agent's call to make.
        """
        return self._park_at_gate(
            GateKind.ESCALATED_QUESTION, question, None, highlights
        )

    async def ping_user(self, message: str, highlights: Sequence[str]) -> ToolResult:
        """Tell the user something. Run-level: no node parks, nothing polls.

        The gate file exists so the website can surface and dismiss it; the
        run never reads a response to it, so an undismissed ping outlives the
        run harmlessly.
        """
        self._gates.raise_gate(
            kind=GateKind.USER_PING, node=None, question=message, artifact=None
        )
        self._note(highlights)
        return ToolResult("the user has been pinged; nothing waits on it")

    def _park_at_gate(
        self,
        kind: GateKind,
        question: str,
        artifact: str | None,
        highlights: Sequence[str],
    ) -> ToolResult:
        """Park this node at a fresh gate, whatever the kind.

        The node parks at `review-pending` with the gate id persisted beside
        its other harness state, so the website can follow the pointer; the
        user is pinged from the ledger. The session is not touched — it stays
        alive and idle, which is the whole point of a gate.
        """
        undelivered = self._must_deliver_first()
        if undelivered is not None:
            return undelivered
        gate = self._gates.raise_gate(
            kind=kind,
            node=self._dispatch.node_id,
            question=question,
            artifact=artifact,
        )
        self.open_gate = gate
        self._dispatch.node.gate = gate.gate_id
        self._dispatch.node.status = NodeStatus.REVIEW_PENDING
        self._note(highlights)
        self._dispatch.persist()
        return ToolResult(
            f"gate {gate.gate_id} raised: the user has been pinged, and node "
            f"{self._dispatch.node_id} now waits for their answer"
        )

    def _must_deliver_first(self) -> ToolResult | None:
        """The refusal owed while a taken-up gate response sits undelivered."""
        if not self.response_undelivered:
            return None
        return ToolResult(
            f"the user's answer has not reached node {self._dispatch.node_id}'s "
            "session yet: deliver it with send_to_session before anything else "
            "is decided about this node",
            is_error=True,
        )

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
        announce: Announce = print,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self._run = run
        self._manifest = manifest
        self._launcher = launcher
        self._clock = clock
        self._new_session_id = session_id_factory
        self._on_event = on_event
        self._heartbeat_seconds = heartbeat_seconds
        self._liveness: Liveness | None = None
        self._graphs = GraphStore(Path(manifest.target_repo))
        self._gates = GateLedger(run, clock=clock, announce=announce)
        self._live: set[LaunchedSession] = set()
        self._agent: OrchestratorAgent | None = None
        self._tool_server: ToolServer | None = None
        self._aborting = False
        self._teardowns: list[asyncio.Task[None]] = []
        self._driving: set[str] = set()
        """Node ids currently between dispatch and a terminal status."""
        self._waiting: set[str] = set()
        """The subset of those parked at a gate. When the two sets are equal
        and non-empty, nothing can proceed until a human acts: that — and
        only that — is what `RunStatus.GATED` means."""

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
        """First interrupt: stop dispatching and bring the live sessions down."""
        if self._aborting:
            return
        self._aborting = True
        logger.debug(
            "abort requested for run %s: terminating %d live session(s)",
            self._manifest.run_id,
            len(self._live),
        )
        for session in list(self._live):
            with contextlib.suppress(RuntimeError):
                # Held, not fire-and-forget: an unreferenced task can be
                # collected before it has brought the session down.
                self._teardowns.append(
                    asyncio.get_running_loop().create_task(session.terminate())
                )

    def kill_now(self) -> None:
        """Second interrupt: no graceful path, no waiting."""
        for session in list(self._live):
            session.kill()

    async def execute(self) -> Manifest:
        """Drive the run to exhaustion, inside the scaffolding every run gets:
        liveness first, the tool server and agent for its lifetime, and the
        terminal status written whatever happens."""
        logger.debug(
            "orchestrator (pid %s) executing run %s: route %s, "
            "concurrency %s, orchestrator model %s",
            os.getpid(),
            self._manifest.run_id,
            self._manifest.route.value,
            self._manifest.config.concurrency,
            self._manifest.config.orchestrator_model,
        )
        self._record_liveness()
        heartbeat = asyncio.create_task(self._keep_recording_liveness())
        tools = self._start_judging()
        try:
            try:
                await self._drive_run()
            except AutoError:
                self._finish_run(errored=True)
                raise
            self._finish_run()
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            tools.close()
        return self._manifest

    async def _drive_run(self) -> None:
        """Drive the root node, then the graph it spawned, to exhaustion.

        The one part of `execute` that differs by how the run was entered:
        a takeover overrides this to revive an existing run instead of
        dispatching a root node.
        """
        outcome = await self._drive_node(self._root_dispatch())
        if outcome is Outcome.COMPLETE and not self.aborting():
            await self._drain_graphs()

    def _record_liveness(self) -> None:
        """Write this process's pid and a fresh heartbeat beside the manifest.

        First thing on execute, before anything else can go wrong, and
        periodically while the loop runs — so the manifest's `running` is a
        checkable claim rather than an article of faith (see `auto.liveness`).
        The file is never removed: a terminal status outranks it, and the last
        heartbeat is part of the run's history.
        """
        now = self._clock()
        if self._liveness is None:
            self._liveness = Liveness(
                pid=os.getpid(), started_at=now, heartbeat_at=now
            )
        else:
            self._liveness = self._liveness.model_copy(
                update={"heartbeat_at": now}
            )
        self._run.write_liveness(self._liveness)

    async def _keep_recording_liveness(self) -> None:
        """Refresh the heartbeat until the end of the run cancels this task."""
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            self._record_liveness()

    def _root_dispatch(self) -> Dispatch:
        """The root node carries the pasted prompt and lives on the manifest."""
        node = self._manifest.root_node
        entry = node.type
        # Only the grill and wayfinder routes dispatch a root session; the
        # takeover route's root is the reconciliation and never comes here.
        assert entry is not None
        return Dispatch(
            node=node,
            node_id=node.node_id,
            type=entry,
            message=f"{entry.skill_invocation} {node.prompt.strip()}",
            ticket=None,
            persist=lambda: self._run.write_manifest(self._manifest),
        )

    async def _drain_graphs(self) -> None:
        """Keep every ready node in flight, bounded, until none is left.

        The tick comes first on every pass: membership and edges are
        re-derived from the tracker files, which is how a ticket written
        mid-run joins the graph before the next dispatch is chosen. Ready
        nodes then launch until the concurrency cap is full. The cap bounds
        driven sessions only — orchestrator interventions run inside each
        node's own task, uncounted, because they are short and counting them
        would mean a busy run stops reacting to sessions that have just
        finished.

        Reaching the run spend ceiling stops dispatch the way an abort does,
        with one difference: the sessions already in flight are left to
        finish, interventions and all. A node that fails only keeps its
        dependents from ever becoming ready; everything else keeps
        dispatching. An error from the machinery itself stops dispatch too,
        and is re-raised once the nodes still in flight have come down.
        """
        cap = self._manifest.config.concurrency
        in_flight: set[asyncio.Task[Outcome]] = set()
        failure: BaseException | None = None
        while True:
            if failure is None and not self.aborting() and not self._over_ceiling():
                self._graphs.tick()
                while len(in_flight) < cap:
                    found = self._graphs.claim_next_ready()
                    if found is None:
                        break
                    in_flight.add(
                        asyncio.create_task(
                            self._drive_node(self._graph_dispatch(*found))
                        )
                    )
            if not in_flight:
                break
            done, in_flight = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                # Read from every task, kept from the first: an unretrieved
                # exception is a warning at teardown and a failure lost.
                exc = task.exception()
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    def _graph_dispatch(self, graph: Graph, node: GraphNode) -> Dispatch:
        entry = node.entry
        assert entry is not None  # ready() only hands out dispatchable nodes
        graph_id = graph.graph_id
        return Dispatch(
            node=node,
            node_id=f"{graph_id}/{node.node_id}",
            type=entry,
            message=f"{entry.skill_invocation} {node.ticket}",
            ticket=node.ticket,
            # Through the store by id: the graph object is replaced on every
            # tick, and a snapshot captured here would go stale mid-node.
            persist=lambda: self._graphs.persist_current(graph_id),
        )

    def _over_ceiling(self) -> bool:
        """Whether driven and orchestrator spend together reached the ceiling.

        Consulted only where dispatch is decided: reaching it stops new
        sessions from starting, never the ones already running.
        """
        spent = self._manifest.driven_spend_usd + self._manifest.orchestrator_spend_usd
        return spent >= self._manifest.config.run_budget_usd

    def _start_judging(self) -> ToolServer:
        """Stand the tools up and write the agent's prompt, once for the run.

        The stable half of the prompt is assembled here and nowhere else: every
        invocation appends this same string to its system prompt, so hundreds
        of them share one cached prefix. Anything per-node that leaked in here
        would quietly cost the run that prefix.
        """
        system_prompt = stable_system_prompt(
            self._manifest,
            tracker_doc=read_tracker_doc(Path(self._manifest.target_repo)),
        )
        self._run.write_orchestrator_prompt(system_prompt)
        tools = ToolServer(asyncio.get_running_loop())
        self._tool_server = tools
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

    async def _drive_node(self, dispatch: Dispatch) -> Outcome:
        """Drive one node from dispatch to a terminal status."""
        session_id = self._new_session_id()
        logger.debug(
            "session start: driven session %s runs skill %s for node %s "
            "(run %s)",
            session_id,
            dispatch.type.skill_invocation,
            dispatch.node_id,
            self._manifest.run_id,
        )
        dispatch.node.session_id = session_id
        dispatch.node.status = NodeStatus.IN_PROGRESS
        dispatch.persist()

        record = SessionRecord(
            session_id=session_id,
            node=dispatch.node_id,
            role=dispatch.type,
            ticket=dispatch.ticket,
            started_at=self._clock(),
            captured_transcript=self._run.relative_transcript_path(session_id),
        )
        self._run.write_session(record)

        self._driving.add(dispatch.node_id)
        self._refresh_gated()
        try:
            try:
                outcome, note = await self._drive(dispatch, record)
            except AutoError as exc:
                self._finish_node(dispatch, record, Outcome.FAILED, note=str(exc))
                raise
            self._finish_node(dispatch, record, outcome, note=note)
        finally:
            self._driving.discard(dispatch.node_id)
            self._refresh_gated()
        return outcome

    async def _drive(
        self, dispatch: Dispatch, record: SessionRecord
    ) -> tuple[Outcome, str | None]:
        """Dispatch the node, then alternate stale points and judgments.

        The dispatch carries the entry skill's invocation and its ticket or
        prompt and nothing else: no obligations, no reminders, no harness
        vocabulary.
        """
        if self.aborting():
            # The interrupt landed before dispatch: stopping dispatching means
            # this session is never started at all.
            return Outcome.ABORTED, None

        # Taken before the launch, so nothing the session writes can race its
        # way in: whatever could satisfy the owed set at this moment predates
        # the node and cannot be what it delivered.
        repo = Path(self._manifest.target_repo)
        baseline = owed.baseline(dispatch.type, repo)
        session = await self._launcher.launch(
            LaunchSpec(
                node_id=dispatch.node_id,
                session_id=record.session_id,
                cwd=repo,
                message=dispatch.message,
                max_budget_usd=self._manifest.config.session_budget_usd,
            )
        )
        self._live.add(session)
        tools = NodeTools(
            run=self._run,
            dispatch=dispatch,
            record=record,
            session=session,
            baseline=baseline,
            graphs=self._graphs,
            gates=self._gates,
            repo=repo,
        )
        # What the first stale point is measured against: a fresh node owes
        # everything its type owes, whatever the repo already carried.
        outstanding = self._take_stock(dispatch, tools, ())

        seen: list[StreamEvent] = []
        reader = _StreamReader(session.events())
        running = BackgroundWork()
        # Set when the last intervention asked nothing of the session, and the
        # loop is giving it a bounded chance to move anyway. It doubles as the
        # note a session that stays silent then fails with.
        quiet_note: str | None = None
        try:
            with self._run.open_transcript(record.session_id) as transcript:
                while True:
                    stale = await self._read_to_stale(
                        reader,
                        transcript,
                        seen,
                        running,
                        silence_budget=(
                            None if quiet_note is None else IDLE_GRACE_SECONDS
                        ),
                    )
                    if stale is None:
                        if self.aborting():
                            return Outcome.ABORTED, None
                        return Outcome.FAILED, quiet_note or NO_RESULT_NOTE
                    quiet_note = None
                    self._absorb(record, stale)
                    if self.aborting():
                        return Outcome.ABORTED, None
                    if record.telemetry.is_error:
                        return Outcome.FAILED, None

                    outstanding = self._take_stock(dispatch, tools, outstanding)
                    refusals = tools.owed_refusals

                    logger.debug(
                        "node %s went stale (session %s): still owes %s",
                        dispatch.node_id,
                        record.session_id,
                        ", ".join(owed.keys(outstanding)) or "nothing",
                    )
                    intervention = await self._intervene(dispatch, tools, seen)
                    while _parked(intervention):
                        # The node is parked at a gate. Nothing is read from
                        # the session — it is alive and idle — until a human
                        # answers; then a fresh agent delivers the answer, and
                        # is judged here exactly like any other intervention.
                        response = await self._await_answer(dispatch, tools)
                        if response is None:
                            return Outcome.ABORTED, None
                        if response.decision is GateDecision.CANNOT:
                            return Outcome.FAILED, self._close_on_cannot(
                                dispatch, tools, response
                            )
                        gate = self._take_answer_up(dispatch, tools, response)
                        intervention = await self._intervene(
                            dispatch,
                            tools,
                            seen,
                            trigger=InterventionTrigger.GATE_RESPONSE,
                            gate=gate,
                            response=response,
                        )
                    if _acted(intervention, COMPLETE_NODE):
                        return Outcome.COMPLETE, None
                    if _acted(intervention, FAIL_NODE):
                        # The reason the agent gave is already the record's.
                        return Outcome.FAILED, None
                    if tools.owed_refusals > refusals and self._spend_nudge_point(
                        dispatch
                    ):
                        # The agent may have nudged this turn as well; that
                        # message is inert, because the budget it was drawn
                        # against is gone and the session comes down with it.
                        return Outcome.FAILED, NUDGE_EXHAUSTED_NOTE.format(
                            budget=NUDGE_BUDGET, missing=owed.spelt_out(outstanding)
                        )
                    if not _acted(intervention, SEND_TO_SESSION):
                        # Nothing was asked of the session and nothing is
                        # running, so by the loop's own model no next stale
                        # point is coming. That model is the harness's, not the
                        # CLI's, so it is not acted on until the session has
                        # been given its grace to disprove it.
                        quiet_note = NO_ACTION_NOTE
                        logger.debug(
                            "node %s got a no-op intervention: waiting %gs for "
                            "the session to move on its own",
                            dispatch.node_id,
                            IDLE_GRACE_SECONDS,
                        )
        finally:
            await reader.aclose()
            await session.terminate()
            self._live.discard(session)

    async def _read_to_stale(
        self,
        reader: _StreamReader,
        transcript: TranscriptWriter,
        seen: list[StreamEvent],
        running: BackgroundWork,
        *,
        silence_budget: float | None = None,
    ) -> StreamEvent | None:
        """Read the stream, capturing it, and stop where the node may be judged.

        That is a turn boundary at which nothing is left running. A `result`
        arriving with background work outstanding is *held* rather than
        returned: the session is about to be woken by its own task, on this
        same stream, and the only thing the harness has to do is keep reading.
        The held result is conceded as stale only if the session then says
        nothing for `BACKGROUND_GRACE_SECONDS` — the task reported to nobody.

        `silence_budget` bounds an ordinary wait the same way, for the caller
        that has already been told nothing is coming. `None` waits as long as
        it takes.

        Returns the stale event, or `None` — the stream ended, the run is
        aborting, or the silence budget ran out.
        """
        held: StreamEvent | None = None
        silent = 0.0
        while True:
            event = await reader.next(STREAM_POLL_SECONDS)
            if event is None:
                if reader.ended:
                    # The process is gone. A result already in hand is the
                    # truer account of where it got to than no result at all.
                    return held
                if self.aborting():
                    return None
                silent += STREAM_POLL_SECONDS
                budget = BACKGROUND_GRACE_SECONDS if held is not None else silence_budget
                if budget is not None and silent >= budget:
                    if held is not None:
                        logger.debug(
                            "node's session held %d background task(s) but said "
                            "nothing for %gs: treating its turn end as stale",
                            len(running.outstanding),
                            silent,
                        )
                    return held
                continue

            silent = 0.0
            transcript.write(event)
            seen.append(event)
            if self._on_event is not None:
                self._on_event(self, event)
            running.absorb(event)
            if is_result(event):
                if not running:
                    return event
                # A turn boundary, not a stale point. Held rather than
                # discarded: if this session goes quiet without ever draining
                # its tasks, the last boundary it reached is what the node gets
                # judged on, and holding one is what bounds the wait at all.
                held = event
            if self.aborting():
                return None

    def _take_stock(
        self,
        dispatch: Dispatch,
        tools: NodeTools,
        previous: tuple[OwedArtifact, ...],
    ) -> tuple[OwedArtifact, ...]:
        """Read what the node still owes, and let progress restore its budget.

        Any shrinking of the owed set is progress — a node doing the right
        thing slowly is not a node to kill — so it starts the count again.
        """
        current = tools.still_owed()
        if owed.shrank(owed.keys(previous), owed.keys(current)):
            dispatch.node.nudge_count = 0
        dispatch.node.missing_artifacts = owed.keys(current)
        dispatch.persist()
        return current

    async def _await_answer(
        self, dispatch: Dispatch, tools: NodeTools
    ) -> GateResponse | None:
        """Wait at the node's open gate until a response file appears.

        The one place the harness reads what the website wrote. An unreadable
        file is no file — the poll simply looks again — and an abort ends the
        wait the way it ends everything else, with None.
        """
        gate = tools.open_gate
        assert gate is not None  # a gate-raising tool landed, so it set one
        logger.debug(
            "node %s parked at gate %s (%s); polling for a response",
            dispatch.node_id,
            gate.gate_id,
            gate.kind.value,
        )
        self._waiting.add(dispatch.node_id)
        self._refresh_gated()
        try:
            while not self.aborting():
                response = self._gates.response_to(gate)
                if response is not None:
                    logger.debug(
                        "gate %s answered `%s` for node %s",
                        gate.gate_id,
                        response.decision.value,
                        dispatch.node_id,
                    )
                    return response
                await asyncio.sleep(GATE_POLL_SECONDS)
            return None
        finally:
            self._waiting.discard(dispatch.node_id)
            self._refresh_gated()

    def _close_on_cannot(
        self, dispatch: Dispatch, tools: NodeTools, response: GateResponse
    ) -> str:
        """Close a task gate the user answered `cannot`. The node fails here.

        The one response the loop consumes instead of delivering (ADR-0008).
        `cannot` is only acceptable on a task-completion gate, and it means
        the facts the session has been waiting for will never exist — there
        is nothing to say to it that changes what happens next, and the
        decision is already the user's, so failing on it is arithmetic, not
        judgment. Their words are kept as the failure's account.
        """
        gate = tools.open_gate
        assert gate is not None
        self._gates.record_answered(gate)
        tools.open_gate = None
        dispatch.node.gate = None
        text = response.text.strip()
        return f"{CANNOT_NOTE}: {text}" if text else CANNOT_NOTE

    def _take_answer_up(
        self, dispatch: Dispatch, tools: NodeTools, response: GateResponse
    ) -> Gate:
        """Close the gate's books before the answer is delivered.

        The gate is stamped answered and the node comes off review-pending —
        whatever the delivery agent then does, nobody is waiting here any
        more, and a revision's fresh gate gets a fresh number.
        """
        gate = tools.open_gate
        assert gate is not None
        self._gates.record_answered(gate)
        tools.open_gate = None
        tools.response_undelivered = True
        dispatch.node.gate = None
        dispatch.node.status = NodeStatus.IN_PROGRESS
        dispatch.persist()
        return gate

    def _refresh_gated(self) -> None:
        """Keep the manifest's status honest about who the run is waiting on.

        `GATED` strictly means nothing can proceed until a human acts: every
        node in flight is parked at a gate. A partially blocked run stays
        `RUNNING`. A node that just completed may leave the run looking gated
        for the instant before its dependents dispatch — the next refresh,
        from the dispatched node itself, corrects it — and that sliver is
        accepted rather than coupling this read to dispatch's own state.
        Terminal statuses are `_finish_run`'s alone.
        """
        if self._manifest.status not in (RunStatus.RUNNING, RunStatus.GATED):
            return
        stalled = bool(self._waiting) and self._waiting >= self._driving
        status = RunStatus.GATED if stalled else RunStatus.RUNNING
        if self._manifest.status is not status:
            self._manifest.status = status
            self._run.write_manifest(self._manifest)

    def _spend_nudge_point(self, dispatch: Dispatch) -> bool:
        """Charge one nudge point against the budget. True when it is spent."""
        dispatch.node.nudge_count += 1
        dispatch.persist()
        return dispatch.node.nudge_count >= NUDGE_BUDGET

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
        dispatch: Dispatch,
        tools: NodeTools,
        seen: Sequence[StreamEvent],
        *,
        trigger: InterventionTrigger = InterventionTrigger.STALE,
        gate: Gate | None = None,
        response: GateResponse | None = None,
    ) -> InterventionRecord:
        """Invoke a fresh agent on this node's stale point or gate response."""
        assert self._agent is not None
        intervention = await self._agent.intervene(
            node=dispatch.node_id,
            trigger=trigger,
            message=intervention_message(
                self._manifest,
                node_id=dispatch.node_id,
                node_type=dispatch.type,
                node_status=dispatch.node.status,
                ticket=dispatch.ticket,
                trigger=trigger,
                trace=render_trace(dispatch.type, seen),
                gate=gate,
                response=response,
            ),
            tools=tools,
        )
        # Counted apart from the session's own spend, so what the harness costs
        # to run is measurable rather than folded into what it drove.
        self._manifest.orchestrator_spend_usd += intervention.telemetry.cost_usd or 0.0
        self._run.write_manifest(self._manifest)
        return intervention

    def _finish_node(
        self,
        dispatch: Dispatch,
        record: SessionRecord,
        outcome: Outcome,
        *,
        note: str | None = None,
    ) -> None:
        """Write one node's terminal state, wherever that state lives."""
        if self.aborting():
            outcome = Outcome.ABORTED
        if note is not None:
            record.summary = note
        record.status, dispatch.node.status = _STATUSES[outcome]
        record.ended_at = self._clock()
        logger.debug(
            "node %s finished %s (session %s)%s",
            dispatch.node_id,
            outcome.value,
            record.session_id,
            f": {note}" if note is not None else "",
        )
        self._run.write_session(record)
        dispatch.persist()

    def _finish_run(self, *, errored: bool = False) -> None:
        """Write the run's terminal status. The only place one lands.

        The run is done exactly when nothing was left to dispatch and
        everything dispatched — the root, and every node in every graph —
        completed. Anything short of that is a failure a script can see.
        """
        if self.aborting():
            status = RunStatus.ABORTED
        elif (
            errored
            or self._manifest.root_node.status is not NodeStatus.DONE
            or not self._graphs.all_done()
        ):
            status = RunStatus.FAILED
        else:
            status = RunStatus.DONE
        self._manifest.status = status
        self._manifest.ended_at = self._clock()
        logger.debug(
            "run %s finished %s: $%.4f driven, $%.4f orchestrator",
            self._manifest.run_id,
            status.value,
            self._manifest.driven_spend_usd,
            self._manifest.orchestrator_spend_usd,
        )
        self._run.write_manifest(self._manifest)


def _acted(intervention: InterventionRecord, tool: str) -> bool:
    """Whether a tool call of this name actually took effect."""
    return any(call.tool == tool and call.accepted for call in intervention.tool_calls)


_PARKING_TOOLS = (PROTOTYPE_READY, HAND_TO_USER, ESCALATE_QUESTION)
"""The tools that leave the node waiting at a gate. A ping is not one: it is
run-level and parks nothing."""


def _parked(intervention: InterventionRecord) -> bool:
    """Whether this intervention left the node waiting at a gate."""
    return any(_acted(intervention, tool) for tool in _PARKING_TOOLS)


def execute_run(
    prepared: PreparedRun,
    launcher: Launcher,
    *,
    clock: Clock = utcnow,
    session_id_factory: SessionIdFactory = new_session_id,
    on_event: EventHook | None = None,
    announce: Announce = print,
    handle_interrupts: bool = False,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
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
            announce=announce,
            heartbeat_seconds=heartbeat_seconds,
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
