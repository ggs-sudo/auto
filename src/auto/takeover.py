"""Taking over an effort whose run stopped without finishing.

`auto takeover <effort-dir>` is the harness's second agent role, entered
manually and reconciling before it resumes: locate the effort's run by
scanning run manifests, refuse while a live orchestrator holds it, gather the
evidence deterministically, consult an ephemeral agent through effort-scoped
harness tools, and — once the effort is judged clean, corrected first where
the record and the repo disagree — revive the manifest and hand the pending
nodes to the stock orchestrator loop. The consultation's whole trail lands as
a numbered reconciliation record in the run directory.

The split mirrors `auto run`: `prepare_takeover` does everything read-only —
locating, guarding, evidence — and resolves configuration fresh (nothing is
inherited from the run being continued), while `execute_takeover` writes.
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
from auto.graph import load_persisted, ticket_stem
from auto.liveness import (
    HEARTBEAT_SECONDS,
    LIVE_CLAIMS,
    observed_status,
    orchestrator_alive,
)
from auto.model import (
    Correction,
    Graph,
    Manifest,
    NodeStatus,
    ReconciliationRecord,
    ReconciliationVerdict,
    RunStatus,
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
from auto.owed import CLOSED_OUT_STATUS
from auto.preflight import EFFORT_ROOT_DIR_NAME, preflight
from auto.run import RunDirectory, list_runs, load_run, runs_dir
from auto.session.events import is_result, result_summary, telemetry_from_result
from auto.session.protocol import Launcher, LaunchSpec
from auto.tools.harness import TAKEOVER_TOOL_NAMES, ToolResult

TAKEOVER_AGENT_TOOLS = (*TAKEOVER_TOOL_NAMES, *READ_ONLY_TOOLS)
"""The consultation reads the target repo — the repo is ground truth, and the
evidence only points at it — and writes only through its effort-scoped tools:
a correction lands loop-side in the effort's graph snapshot, never through a
file tool of the agent's own."""

_UNSAFE_IN_A_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class TakeoverRequest:
    """What the operator asked for: an effort, and fresh configuration."""

    effort_dir: Path
    state_dir: Path
    overrides: ConfigOverrides = field(default_factory=ConfigOverrides)


@dataclass(frozen=True)
class PreparedTakeover:
    """A takeover that has located its run, passed the guard, and read its
    evidence. Nothing on disk has been touched yet: the manifest here is
    revived in memory — status running, ended cleared, configuration fresh —
    and written when execution begins."""

    run: RunDirectory
    manifest: Manifest
    effort: str
    evidence: list[str]
    warnings: list[str]


def prepare_takeover(request: TakeoverRequest) -> PreparedTakeover:
    """Locate the effort's run, refuse a held one, and gather the evidence."""
    effort_dir = request.effort_dir.expanduser().resolve()
    checked = preflight(effort_dir)
    if effort_dir.parent != checked.repo / EFFORT_ROOT_DIR_NAME:
        raise UsageError(
            f"{effort_dir} is not an effort directory: takeover points at "
            f"`<repo>/{EFFORT_ROOT_DIR_NAME}/<effort>`"
        )
    effort = effort_dir.name
    config = resolve_config(request.state_dir, request.overrides)
    run, manifest = _locate(request.state_dir, checked.repo, effort)
    _refuse_a_held_run(run, manifest)
    if manifest.root_node.status is not NodeStatus.DONE:
        raise TakeoverError(
            f"run {manifest.run_id} stopped before its root session finished; "
            "a planning conversation cannot be resumed, so there is nothing "
            "for takeover to continue"
        )
    evidence = gather_evidence(run, manifest, effort)
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
    )


def _locate(
    state_dir: Path, repo: Path, effort: str
) -> tuple[RunDirectory, Manifest]:
    """The effort's run, found by scanning every manifest, newest first.

    Several runs claiming one effort is drift a later takeover will treat in
    full; continuing the newest is the one piece of that already needed here.
    """
    for manifest in list_runs(state_dir):
        if (
            manifest.target_repo == str(repo)
            and manifest.root_node.graph == effort
        ):
            return load_run(state_dir, manifest.run_id), manifest
    raise UsageError(
        f"no run holds effort `{effort}` as its root graph in {repo} "
        f"(scanned {runs_dir(state_dir)}); an effort spawned as a subgraph "
        "is not yet a takeover target"
    )


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
    run: RunDirectory, manifest: Manifest, effort: str
) -> list[str]:
    """What the takeover examined: one line per fact, read deterministically.

    Gathered by the harness before any agent exists, so the reconciliation
    record's account of what was looked at is arithmetic, not testimony. The
    agent judges from these lines and from the repo itself.
    """
    repo = Path(manifest.target_repo)
    lines = [
        f"run {manifest.run_id}: manifest status "
        f"`{observed_status(manifest, run.read_liveness())}`"
        + (
            f", ended {manifest.ended_at.isoformat()}"
            if manifest.ended_at is not None
            else ""
        )
    ]
    for node in load_persisted(repo, effort).nodes:
        lines.append(
            f"node {node.node_id}: recorded `{node.status.value}`; "
            f"ticket `{node.ticket}` {_ticket_state(repo / node.ticket)}; "
            f"{_session_state(run, node.session_id)}"
        )
    return lines


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
        await self._reconcile()
        if self.aborting():
            return
        self._graphs.adopt(self._effort)
        await self._drain_graphs()

    async def _reconcile(self) -> None:
        """Consult one ephemeral agent over the evidence, and require its
        clean verdict — as a landed tool call — before anything resumes."""
        assert self._tool_server is not None  # execute() stood it up
        record = self._open_record()
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
        # Corrections alone land no verdict: the clean report remains the one
        # act that says the state now agrees, so a consultation that corrected
        # and then trailed off still stops the takeover.
        if tools.clean_summary is not None:
            record.verdict = (
                ReconciliationVerdict.CORRECTED
                if record.corrections
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
