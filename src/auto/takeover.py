"""Taking over an effort whose run stopped without finishing.

`auto takeover <effort-dir>` is the harness's second agent role, entered
manually and reconciling before it resumes: locate the effort's run by
scanning run manifests, refuse while a live orchestrator holds it, gather the
evidence deterministically, consult an ephemeral agent through effort-scoped
harness tools, and — once the effort is judged clean, corrected first where
the record and the repo disagree — revive the manifest and hand the pending
nodes to the stock orchestrator loop. The consultation's whole trail lands as
a numbered reconciliation record in the run directory.

An effort with tickets but no run at all is the implement-only entry point:
takeover mints the run itself, under the takeover route. Its root node
carries the effort path where an ordinary root carries the pasted prompt, its
root session is the reconciliation, and the root is done the moment the graph
— derived first-sight from the hand-written tickets — is adopted.

The split mirrors `auto run`: `prepare_takeover` leaves any run it located
untouched — locating, guarding, evidence — and resolves configuration fresh
(nothing is inherited from the run being continued), while `execute_takeover`
writes. The one prepare-time write is minting the run when none exists,
exactly as `prepare_run` lays out a fresh run's directory.
Revival is written *before* the agent is consulted, so the heartbeat guard
covers the consultation too: from the revival onward, a second takeover sees
a live orchestrator and refuses. The guard runs twice — at prepare, for a
fast refusal, and again at the first moment of execution — because two
takeovers prepared side by side would otherwise both pass; the sliver
between the re-check and the revival is accepted rather than locked over.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from auto.agent.invoke import READ_ONLY_TOOLS
from auto.agent.prompt import reconciliation_brief
from auto.config import ConfigOverrides, resolve_config
from auto.errors import TakeoverError, UsageError
from auto.gates import Announce
from auto.graph import ISSUES_DIR, GraphError, derive, load_persisted, ticket_stem
from auto.liveness import (
    HEARTBEAT_SECONDS,
    LIVE_CLAIMS,
    observed_status,
    orchestrator_alive,
)
from auto.model import (
    ROOT_NODE_ID,
    Correction,
    Graph,
    Manifest,
    NodeStatus,
    ReconciliationRecord,
    ReconciliationVerdict,
    ResolvedConfig,
    RootNode,
    Route,
    RunStatus,
    TicketCorrection,
)
from auto.orchestrate import (
    Clock,
    EventHook,
    InterruptHandler,
    Orchestrator,
    SessionIdFactory,
    new_session_id,
    utcnow,
)
from auto.owed import CLOSED_OUT_STATUS, CLOSED_OUT_VALUE, EFFORT_ROOT, STATUS_LINE
from auto.preflight import EFFORT_ROOT_DIR_NAME, PreflightResult, preflight
from auto.run import (
    RunDirectory,
    allocate_run_id,
    list_runs,
    load_run,
    runs_dir,
    write_atomically,
)
from auto.session.events import is_result, result_summary, telemetry_from_result
from auto.session.protocol import Launcher, LaunchSpec
from auto.tools.harness import TAKEOVER_TOOL_NAMES, ToolResult

TAKEOVER_AGENT_TOOLS = (*TAKEOVER_TOOL_NAMES, *READ_ONLY_TOOLS)
"""The consultation reads the target repo — the repo is ground truth, and the
evidence only points at it — and writes only through its effort-scoped tools:
a correction lands loop-side in the effort's graph snapshot, never through a
file tool of the agent's own."""

_UNSAFE_IN_A_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

def reopen_status_lines(text: str, status: str) -> tuple[str, str] | None:
    """Every closed-out `Status:` line in a ticket, rewritten to `status`.

    Returns the corrected text and the first lying value, or None when no
    line closes the ticket out — an open ticket tells no lie a first-sight
    derivation could import. Every lying line is rewritten, not just the
    first, because the derivation's own check matches anywhere in the file:
    a correction that left one behind would not have corrected anything.
    Whatever wrapped the value — `**Status:** done`, `**Status: done**` —
    is kept around the new one.
    """
    lied: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        value = match.group("value")
        if CLOSED_OUT_VALUE.match(value) is None:
            return match.group(0)
        lied.append(value)
        return match.group("prefix") + status + match.group("suffix")

    corrected = STATUS_LINE.sub(rewrite, text)
    if not lied:
        return None
    return corrected, lied[0]


@dataclass(frozen=True)
class TakeoverRequest:
    """What the operator asked for: an effort, and fresh configuration."""

    effort_dir: Path
    state_dir: Path
    overrides: ConfigOverrides = field(default_factory=ConfigOverrides)


@dataclass(frozen=True)
class PreparedTakeover:
    """A takeover that has located its run — or minted one when the effort had
    none — passed the guard, and read its evidence. A located run has not been
    touched: the manifest here is revived in memory — status running, ended
    cleared, configuration fresh — and written when execution begins."""

    run: RunDirectory
    manifest: Manifest
    effort: str
    evidence: list[str]
    warnings: list[str]
    created: bool = False
    """Whether this takeover minted the run: no run held the effort, so one
    was created under the takeover route — the implement-only entry point."""

    first_sight: Graph | None = None
    """The graph derived first-sight from the tickets, when no persisted
    snapshot exists yet. Persisted at the start of execution, so the
    consultation's corrections have a snapshot to land in."""


def prepare_takeover(
    request: TakeoverRequest, *, clock: Clock = utcnow
) -> PreparedTakeover:
    """Locate the effort's run — or mint one when none exists — refuse a held
    one, and gather the evidence."""
    effort_dir = request.effort_dir.expanduser().resolve()
    checked = preflight(effort_dir)
    if effort_dir.parent != checked.repo / EFFORT_ROOT_DIR_NAME:
        raise UsageError(
            f"{effort_dir} is not an effort directory: takeover points at "
            f"`<repo>/{EFFORT_ROOT_DIR_NAME}/<effort>`"
        )
    effort = effort_dir.name
    config = resolve_config(request.state_dir, request.overrides)
    located = _locate(request.state_dir, checked.repo, effort)
    if located is None:
        return _prepare_created(request, checked, effort, config, clock)
    run, manifest = located
    _refuse_a_held_run(run, manifest)
    if (
        manifest.route is not Route.TAKEOVER
        and manifest.root_node.status is not NodeStatus.DONE
    ):
        raise TakeoverError(
            f"run {manifest.run_id} stopped before its root session finished; "
            "a planning conversation cannot be resumed, so there is nothing "
            "for takeover to continue"
        )
    graph, first_sight = _effort_graph(checked.repo, effort, manifest)
    evidence = gather_evidence(run, manifest, graph)
    # The revival, in memory: back to running, the end erased, and the
    # configuration replaced wholesale — budgets and model are the operator's,
    # never inherited from the run being continued.
    manifest.status = RunStatus.RUNNING
    manifest.ended_at = None
    manifest.config = config
    return PreparedTakeover(
        run=run,
        manifest=manifest,
        effort=effort,
        evidence=evidence,
        warnings=checked.warnings,
        first_sight=graph if first_sight else None,
    )


def _locate(
    state_dir: Path, repo: Path, effort: str
) -> tuple[RunDirectory, Manifest] | None:
    """The effort's run, found by scanning every manifest, newest first — or
    None when no run claims the effort, which is the implement-only entry.

    Several runs claiming one effort is drift a later takeover will treat in
    full; continuing the newest is the one piece of that already needed here.
    """
    for manifest in list_runs(state_dir):
        if (
            manifest.target_repo == str(repo)
            and manifest.root_node.graph == effort
        ):
            return load_run(state_dir, manifest.run_id), manifest
    return None


def _prepare_created(
    request: TakeoverRequest,
    checked: PreflightResult,
    effort: str,
    config: ResolvedConfig,
    clock: Clock,
) -> PreparedTakeover:
    """No run holds the effort: mint one under the takeover route.

    The implement-only entry point — hand-written tickets, no planning
    session. The root node carries the effort path where an ordinary root
    carries the pasted prompt; its session is the reconciliation itself, done
    when the graph is adopted. The graph is derived first-sight from the
    tickets, so a closed-out `Status:` line imports as done exactly as it
    would at emission — and the reconciliation is where that trust is checked.
    """
    repo = checked.repo
    try:
        persisted: Graph | None = load_persisted(repo, effort)
    except GraphError:
        persisted = None
    if persisted is not None and persisted.spawned_by != ROOT_NODE_ID:
        raise UsageError(
            f"effort `{effort}` was spawned as a subgraph (by node "
            f"`{persisted.spawned_by}`), so some run's subtree already holds "
            "it; take over the effort at the root of that run instead"
        )
    graph = (
        persisted
        if persisted is not None
        else derive(repo, effort, spawned_by=ROOT_NODE_ID)
    )
    if not graph.nodes:
        raise UsageError(
            f"no run holds effort `{effort}` in {repo} (scanned "
            f"{runs_dir(request.state_dir)}) and no tickets are under "
            f"`{EFFORT_ROOT}/{effort}/{ISSUES_DIR}/`: nothing to take over"
        )
    created_at = clock()
    prompt = f"{EFFORT_ROOT}/{effort}"
    manifest = Manifest(
        run_id=allocate_run_id(request.state_dir, created_at, effort),
        route=Route.TAKEOVER,
        prompt=prompt,
        target_repo=str(repo),
        worktree=str(repo),
        branch=checked.branch,
        head=checked.head,
        dirty=checked.dirty,
        config=config,
        created_at=created_at,
        root_node=RootNode(type=None, prompt=prompt, graph=effort),
    )
    run = RunDirectory.create(request.state_dir, manifest)
    evidence = [
        f"run {manifest.run_id}: created by this takeover — effort "
        f"`{effort}` had tickets but no run"
    ] + _node_lines(run, repo, graph)
    return PreparedTakeover(
        run=run,
        manifest=manifest,
        effort=effort,
        evidence=evidence,
        warnings=checked.warnings,
        created=True,
        first_sight=None if persisted is not None else graph,
    )


def _effort_graph(
    repo: Path, effort: str, manifest: Manifest
) -> tuple[Graph, bool]:
    """The effort's graph, and whether it had to be derived first-sight.

    A located run normally left a snapshot beside the tickets; only a takeover
    run that crashed before its first snapshot landed is derived again,
    exactly as its creation did.
    """
    try:
        return load_persisted(repo, effort), False
    except GraphError:
        if manifest.route is not Route.TAKEOVER:
            raise
        return derive(repo, effort, spawned_by=ROOT_NODE_ID), True


def _refuse_a_held_run(run: RunDirectory, manifest: Manifest) -> None:
    """The heartbeat guard: two writers never race on one run.

    A manifest claiming a live orchestrator is checked against the process
    table. Alive refuses — there is deliberately no force flag — while
    running-but-dead is a crashed run, the prime takeover candidate.
    """
    liveness = run.read_liveness()
    if manifest.status in LIVE_CLAIMS and orchestrator_alive(liveness):
        assert liveness is not None  # a dead None would not be alive
        raise TakeoverError(
            f"run {manifest.run_id} is held by a live orchestrator "
            f"(pid {liveness.pid}, last heartbeat "
            f"{liveness.heartbeat_at.isoformat()}); takeover never races a "
            "live writer, and there is no flag to force it"
        )


def gather_evidence(
    run: RunDirectory, manifest: Manifest, graph: Graph
) -> list[str]:
    """What the takeover examined: one line per fact, read deterministically.

    Gathered by the harness before any agent exists, so the reconciliation
    record's account of what was looked at is arithmetic, not testimony. The
    agent judges from these lines and from the repo itself.
    """
    lines = [
        f"run {manifest.run_id}: manifest status "
        f"`{observed_status(manifest, run.read_liveness())}`"
        + (
            f", ended {manifest.ended_at.isoformat()}"
            if manifest.ended_at is not None
            else ""
        )
    ]
    return lines + _node_lines(run, Path(manifest.target_repo), graph)


def _node_lines(run: RunDirectory, repo: Path, graph: Graph) -> list[str]:
    return [
        f"node {node.node_id}: recorded `{node.status.value}`; "
        f"ticket `{node.ticket}` {_ticket_state(repo / node.ticket)}; "
        f"{_session_state(run, node.session_id)}"
        for node in graph.nodes
    ]


def _ticket_state(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "missing"
    if CLOSED_OUT_STATUS.search(text) is not None:
        return "closed out"
    return "open"


def _session_state(run: RunDirectory, session_id: str | None) -> str:
    if session_id is None:
        return "no session recorded"
    if run.session_path(session_id).is_file():
        return f"session {session_id} recorded"
    return f"session {session_id} named but unrecorded"


class EffortTools:
    """The loop side of the takeover tools, bound to one effort.

    Corrections write the effort's persisted graph snapshot: the consultation
    runs before the run adopts its graphs, so the snapshot is the only place a
    correction can land where the adoption will read it. The write goes
    through the store's own persist, keeping its single-writer guard.
    """

    RESETTABLE = (NodeStatus.DONE, NodeStatus.FAILED)
    """The two statuses a correction can dispute. A node the crash left
    mid-flight is returned to pending by revival arithmetic, not judgment."""

    def __init__(
        self, effort: str, repo: Path, persist: Callable[[Graph], None]
    ) -> None:
        self._effort = effort
        self._repo = repo
        self._persist = persist
        self.clean_summary: str | None = None
        """The agent's clean verdict, or None while — and if — none lands."""
        self.corrections: list[Correction] = []
        """Every correction that landed, in order."""
        self.ticket_corrections: list[TicketCorrection] = []
        """Every ticket `Status:` line corrected, in order."""

    async def report_effort_clean(self, summary: str) -> ToolResult:
        self.clean_summary = summary
        return ToolResult(
            f"recorded: effort `{self._effort}` was found clean, and its "
            "pending work will now resume"
        )

    async def reset_node(self, node: str, evidence: str) -> ToolResult:
        graph = load_persisted(self._repo, self._effort)
        target = graph.node(ticket_stem(node))
        if target is None:
            return ToolResult(
                f"`{node}` names no node in effort `{self._effort}`",
                is_error=True,
            )
        if target.status not in self.RESETTABLE:
            return ToolResult(
                f"node {target.node_id} is recorded `{target.status.value}`: "
                "only a done or failed node can be reset, and a node left "
                "mid-flight is returned to pending by revival on its own",
                is_error=True,
            )
        prior = target.status
        target.reset_to_pending()
        self.corrections.append(
            Correction(
                node=target.node_id,
                prior_status=prior,
                new_status=NodeStatus.PENDING,
                evidence=evidence,
            )
        )
        self._persist(graph)
        return ToolResult(
            f"node {target.node_id} reset: `{prior.value}` → `pending`; the "
            "resumed run will re-dispatch it"
        )

    async def correct_ticket_status(
        self, node: str, status: str, evidence: str
    ) -> ToolResult:
        """The one write the harness makes to a tracker file (ADR-0010): the
        lying `Status:` line, and a note appended so a reader sees why."""
        graph = load_persisted(self._repo, self._effort)
        target = graph.node(ticket_stem(node))
        if target is None:
            return ToolResult(
                f"`{node}` names no node in effort `{self._effort}`",
                is_error=True,
            )
        status = status.strip()
        if CLOSED_OUT_VALUE.match(status) is not None:
            return ToolResult(
                f"`{status}` closes the ticket out: takeover reopens a lying "
                "`Status:` line, and only a session doing the work closes one",
                is_error=True,
            )
        path = self._repo / target.ticket
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ToolResult(
                f"ticket `{target.ticket}` cannot be read", is_error=True
            )
        reopened = reopen_status_lines(text, status)
        if reopened is None:
            return ToolResult(
                f"ticket `{target.ticket}` carries no closed-out `Status:` "
                "line, so a first-sight derivation would not import it as "
                "done: there is no lie to correct",
                is_error=True,
            )
        corrected, prior = reopened
        write_atomically(path, _with_note(corrected, prior, status, evidence))
        self.ticket_corrections.append(
            TicketCorrection(
                node=target.node_id,
                ticket=target.ticket,
                prior_status=prior,
                new_status=status,
                evidence=evidence,
            )
        )
        return ToolResult(
            f"ticket {target.ticket} corrected: `Status: {prior}` → "
            f"`Status: {status}`, with a reconciliation note appended; a "
            "fresh derivation will no longer import it as done"
        )


def _with_note(text: str, prior: str, status: str, evidence: str) -> str:
    """The corrected ticket with its reconciliation note appended.

    The note is one line, its evidence collapsed to single spaces, so nothing
    an agent writes can smuggle a fresh `Status:` line — or anything else
    line-anchored — into the ticket.
    """
    return (
        text.rstrip("\n")
        + "\n\n---\n\n**Reconciliation note:** `auto takeover` corrected this "
        f"ticket's `Status:` line from `{prior}` to `{status}` — "
        + " ".join(evidence.split())
        + "\n"
    )


class Takeover(Orchestrator):
    """The stock loop, entered through revival instead of a root dispatch."""

    def __init__(
        self,
        prepared: PreparedTakeover,
        launcher: Launcher,
        *,
        clock: Clock = utcnow,
        session_id_factory: SessionIdFactory = new_session_id,
        on_event: EventHook | None = None,
        announce: Announce = print,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        super().__init__(
            prepared.run,
            prepared.manifest,
            launcher,
            clock=clock,
            session_id_factory=session_id_factory,
            on_event=on_event,
            announce=announce,
            heartbeat_seconds=heartbeat_seconds,
        )
        self._effort = prepared.effort
        self._evidence = prepared.evidence
        self._created = prepared.created
        self._first_sight = prepared.first_sight

    async def execute(self) -> Manifest:
        # The guard again, against the manifest as it is on disk *now*: a
        # competing takeover may have claimed the run since prepare read it.
        # Before any write, so a refused loser leaves the winner's run
        # directory untouched. The window between two prepares cannot be
        # closed without a lock; re-checking here shrinks it to almost
        # nothing.
        _refuse_a_held_run(self._run, self._run.read_manifest())
        return await super().execute()

    async def _drive_run(self) -> None:
        """Revive, reconcile, and only then hand over to the stock loop."""
        # The revival lands first, so from here on the run is held: the
        # manifest claims running and this process's heartbeat backs it.
        self._run.write_manifest(self._manifest)
        if self._first_sight is not None:
            # No snapshot was on disk, so the first-sight derivation becomes
            # one now — the consultation's corrections land in it, and the
            # adoption below reads it back.
            self._graphs.persist(self._first_sight)
        await self._reconcile()
        if self.aborting():
            return
        self._graphs.adopt(self._effort)
        self._finish_root()
        await self._drain_graphs()

    def _finish_root(self) -> None:
        """On the takeover route, adoption is the moment the root is done —
        the counterpart of an ordinary root completing on its emitted graph.
        Any other route's root finished long ago and is left alone."""
        if self._manifest.route is not Route.TAKEOVER:
            return
        root = self._manifest.root_node
        root.status = NodeStatus.DONE
        root.graph = self._effort
        self._run.write_manifest(self._manifest)

    async def _reconcile(self) -> None:
        """Consult one ephemeral agent over the evidence, and require its
        clean verdict — as a landed tool call — before anything resumes."""
        assert self._tool_server is not None  # execute() stood it up
        record = self._open_record()
        if self._manifest.route is Route.TAKEOVER:
            # The takeover route's root session is the reconciliation itself:
            # the consultation runs as the root, and the manifest says so
            # while it is running.
            root = self._manifest.root_node
            root.session_id = record.session_id
            root.status = NodeStatus.IN_PROGRESS
            self._run.write_manifest(self._manifest)
        tools = EffortTools(
            self._effort,
            Path(self._manifest.target_repo),
            self._graphs.persist,
        )
        with self._tool_server.takeover(
            self._run.run_id, self._effort, tools
        ) as scope:
            session = await self._launcher.launch(
                LaunchSpec(
                    node_id=f"takeover:{self._effort}",
                    session_id=record.session_id,
                    cwd=Path(self._manifest.target_repo),
                    message=reconciliation_brief(
                        self._manifest,
                        effort=self._effort,
                        evidence=self._evidence,
                        created=self._created,
                    ),
                    model=record.model,
                    mcp_config=self._tool_server.takeover_mcp_config(
                        self._run.run_id, self._effort
                    ),
                    allowed_tools=TAKEOVER_AGENT_TOOLS,
                    one_shot=True,
                )
            )
            stream = session.events()
            try:
                async for event in stream:
                    if is_result(event):
                        record.telemetry = telemetry_from_result(event)
                        record.prose = result_summary(event)
                        break
            finally:
                with contextlib.suppress(Exception):
                    await stream.aclose()
                await session.terminate()
            record.tool_calls = list(scope.tool_calls)
        record.corrections = list(tools.corrections)
        record.ticket_corrections = list(tools.ticket_corrections)
        # Corrections alone land no verdict: the clean report remains the one
        # act that says the state now agrees, so a consultation that corrected
        # and then trailed off still stops the takeover.
        if tools.clean_summary is not None:
            record.verdict = (
                ReconciliationVerdict.CORRECTED
                if record.corrections or record.ticket_corrections
                else ReconciliationVerdict.CLEAN
            )
        record.ended_at = self._clock()
        self._run.write_reconciliation(record)
        self._manifest.orchestrator_spend_usd += record.telemetry.cost_usd or 0.0
        self._run.write_manifest(self._manifest)
        if record.verdict is None and not self.aborting():
            raise TakeoverError(
                f"the takeover agent landed no clean verdict on effort "
                f"`{self._effort}`, so nothing was dispatched; its account is "
                f"in {self._run.reconciliation_path(record.reconciliation_id)}"
            )

    def _open_record(self) -> ReconciliationRecord:
        """The record, numbered run-wide and written before the agent runs —
        so a slow judgment is visible while it is being made."""
        sequence = 1 + max(
            (r.sequence for r in self._run.reconciliation_records()), default=0
        )
        stem = _UNSAFE_IN_A_FILENAME.sub("-", self._effort)
        record = ReconciliationRecord(
            reconciliation_id=f"{sequence:04d}-{stem}",
            sequence=sequence,
            effort=self._effort,
            examined=list(self._evidence),
            model=self._manifest.config.orchestrator_model,
            session_id=self._new_session_id(),
            started_at=self._clock(),
        )
        self._run.write_reconciliation(record)
        return record


def execute_takeover(
    prepared: PreparedTakeover,
    launcher: Launcher,
    *,
    clock: Clock = utcnow,
    session_id_factory: SessionIdFactory = new_session_id,
    on_event: EventHook | None = None,
    announce: Announce = print,
    handle_interrupts: bool = False,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> Manifest:
    """Drive a prepared takeover to the run's end. Blocks until it gets there."""

    async def main() -> Manifest:
        takeover = Takeover(
            prepared,
            launcher,
            clock=clock,
            session_id_factory=session_id_factory,
            on_event=on_event,
            announce=announce,
            heartbeat_seconds=heartbeat_seconds,
        )
        if handle_interrupts:
            InterruptHandler(takeover).install()
        return await takeover.execute()

    return asyncio.run(main())
