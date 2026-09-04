"""Takeover, driven for real above the seam: an effort whose run stopped is
located, guarded, judged clean by a scripted agent over the real tool plumbing,
and resumed through the stock loop with replayed sessions."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auto.config import ConfigOverrides
from auto.errors import TakeoverError, UsageError
from auto.graph import derive
from auto.model import (
    Gate,
    GateKind,
    Graph,
    GraphNode,
    Manifest,
    NodeStatus,
    NodeType,
    ReconciliationVerdict,
    ResolvedConfig,
    RootNode,
    Route,
    RunStatus,
    SessionRecord,
    TicketType,
)
from auto.run import RunDirectory, list_runs
from auto.session.protocol import LaunchSpec
from auto.takeover import (
    TakeoverRequest,
    execute_takeover,
    prepare_takeover,
    reopen_status_lines,
)
from auto.session.replay import Recording
from tests.agents import (
    Agents,
    HarnessLauncher,
    ScriptedAgent,
    Writes,
    completes,
    corrects,
    reports_clean,
    says_nothing,
    url_from,
)
from tests.conftest import dead_pid, one_turn

EFFORT = "add-search"

MAP_BODY = "## Destination\n\nSearch on the settings page.\n"

DONE_TICKET = """# 01: Index the settings content

**Blocked by:** None (can start immediately)

**Status:** done
"""

OPEN_TICKET = """# 02: Render the results

**Blocked by:** 01

**Status:** ready-for-agent
"""

CLOSED_SECOND_TICKET = OPEN_TICKET.replace("ready-for-agent", "done")

PHANTOM_FIRST_TICKET = DONE_TICKET.replace("done", "ready-for-agent")
"""The first ticket still open: a graph recording its node done is lying."""

OLD_CONFIG = ResolvedConfig(
    concurrency=9, session_budget_usd=99.0, run_budget_usd=250.0
)
"""Deliberately unlike any default, so inheriting it would be visible."""


def a_stopped_run(
    target_repo: Path,
    state_dir: Path,
    *,
    effort: str = EFFORT,
    run_id: str = "20260901-120000-add-search",
    status: RunStatus = RunStatus.FAILED,
    liveness_pid: int | None = None,
    root_status: NodeStatus = NodeStatus.DONE,
    root_graph: str | None = EFFORT,
    first_ticket: str = DONE_TICKET,
    first_node_status: NodeStatus = NodeStatus.DONE,
    second_node_status: NodeStatus = NodeStatus.PENDING,
    second_node_session: str | None = None,
) -> RunDirectory:
    """A run directory and effort the way a stopped run leaves them: one node
    done with its ticket closed out, one still to do."""
    repo = target_repo.resolve()
    effort_dir = repo / ".scratch" / effort
    issues = effort_dir / "issues"
    issues.mkdir(parents=True, exist_ok=True)
    (effort_dir / "map.md").write_text(MAP_BODY)
    (issues / "01-index.md").write_text(first_ticket)
    (issues / "02-render.md").write_text(OPEN_TICKET)

    graph = Graph(
        graph_id=effort,
        spawned_by="root",
        nodes=[
            GraphNode(
                node_id="01-index",
                ticket=f".scratch/{effort}/issues/01-index.md",
                ticket_type=TicketType.IMPLEMENT,
                blocked_by=[],
                status=first_node_status,
                session_id="s-01",
            ),
            GraphNode(
                node_id="02-render",
                ticket=f".scratch/{effort}/issues/02-render.md",
                ticket_type=TicketType.IMPLEMENT,
                blocked_by=["01-index"],
                status=second_node_status,
                session_id=second_node_session,
            ),
        ],
    )
    (effort_dir / "graph.json").write_text(graph.model_dump_json(indent=2) + "\n")

    started = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    manifest = Manifest(
        run_id=run_id,
        route=Route.WAYFINDER,
        prompt="Add search.",
        target_repo=str(repo),
        worktree=str(repo),
        branch="main",
        config=OLD_CONFIG,
        created_at=started,
        ended_at=None if status in (RunStatus.RUNNING, RunStatus.GATED) else started,
        status=status,
        root_node=RootNode(
            type=NodeType.WAYFINDER,
            prompt="Add search.",
            status=root_status,
            session_id="s-root",
            graph=root_graph,
        ),
    )
    run = RunDirectory.create(state_dir, manifest)
    run.write_session(
        SessionRecord(
            session_id="s-01",
            node=f"{effort}/01-index",
            role=NodeType.IMPLEMENT,
            started_at=started,
            captured_transcript="transcripts/s-01.jsonl",
        )
    )
    if liveness_pid is not None:
        from auto.model import Liveness

        run.write_liveness(
            Liveness(pid=liveness_pid, started_at=started, heartbeat_at=started)
        )
    return run


def a_request(
    target_repo: Path,
    state_dir: Path,
    *,
    effort: str = EFFORT,
    overrides: ConfigOverrides | None = None,
) -> TakeoverRequest:
    return TakeoverRequest(
        effort_dir=target_repo / ".scratch" / effort,
        state_dir=state_dir,
        overrides=overrides or ConfigOverrides(),
    )


def a_takeover_launcher(
    *node_agents: ScriptedAgent, consultation: ScriptedAgent | None = None
) -> HarnessLauncher:
    """A consultation that finds the effort clean, and one replayed session
    that closes out the remaining ticket and is completed."""
    return HarnessLauncher(
        {f"{EFFORT}/02-render": one_turn("Rendered the results.")},
        agents={
            f"takeover:{EFFORT}": [
                consultation if consultation is not None else reports_clean()
            ],
            f"{EFFORT}/02-render": list(node_agents) or [completes()],
        },
        writes={
            f"{EFFORT}/02-render": [
                {f".scratch/{EFFORT}/issues/02-render.md": CLOSED_SECOND_TICKET}
            ]
        },
    )


def a_correcting_launcher(consultation: ScriptedAgent) -> HarnessLauncher:
    """A consultation of the test's choosing, and replayed sessions ready to
    redo both tickets, each closing its ticket out."""
    return HarnessLauncher(
        {
            f"{EFFORT}/01-index": one_turn("Indexed the settings content."),
            f"{EFFORT}/02-render": one_turn("Rendered the results."),
        },
        agents={
            f"takeover:{EFFORT}": [consultation],
            f"{EFFORT}/01-index": [completes()],
            f"{EFFORT}/02-render": [completes()],
        },
        writes={
            f"{EFFORT}/01-index": [
                {f".scratch/{EFFORT}/issues/01-index.md": DONE_TICKET}
            ],
            f"{EFFORT}/02-render": [
                {f".scratch/{EFFORT}/issues/02-render.md": CLOSED_SECOND_TICKET}
            ],
        },
    )


# --- locating and guarding --------------------------------------------------


def test_takeover_locates_the_efforts_run_by_scanning_manifests(
    target_repo: Path, state_dir: Path, tmp_path: Path
) -> None:
    from tests.conftest import make_target_repo

    other_repo = make_target_repo(tmp_path / "other")
    a_stopped_run(other_repo, state_dir, run_id="20260902-090000-other")
    run = a_stopped_run(target_repo, state_dir)
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    assert prepared.run.run_id == run.run_id
    assert prepared.effort == EFFORT


def test_takeover_continues_the_newest_run_claiming_the_effort(
    target_repo: Path, state_dir: Path
) -> None:
    a_stopped_run(target_repo, state_dir, run_id="20260830-080000-add-search")
    a_stopped_run(target_repo, state_dir, run_id="20260902-090000-add-search")
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    assert prepared.run.run_id == "20260902-090000-add-search"


def test_takeover_refuses_while_a_live_orchestrator_holds_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    a_stopped_run(
        target_repo, state_dir, status=RunStatus.RUNNING, liveness_pid=os.getpid()
    )
    with pytest.raises(TakeoverError, match="live orchestrator"):
        prepare_takeover(a_request(target_repo, state_dir))


def test_a_running_but_dead_run_is_crashed_and_taken_over(
    target_repo: Path, state_dir: Path
) -> None:
    a_stopped_run(
        target_repo, state_dir, status=RunStatus.RUNNING, liveness_pid=dead_pid()
    )
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    assert prepared.manifest.status is RunStatus.RUNNING
    assert any("crashed" in line for line in prepared.evidence)


def test_an_effort_with_no_tickets_and_no_run_is_refused(
    target_repo: Path, state_dir: Path
) -> None:
    effort_dir = target_repo / ".scratch" / EFFORT
    effort_dir.mkdir(parents=True)
    with pytest.raises(UsageError, match="nothing to take over"):
        prepare_takeover(a_request(target_repo, state_dir))
    assert list_runs(state_dir) == []  # refused before any run was laid out


def test_a_directory_outside_the_effort_root_is_refused(
    target_repo: Path, state_dir: Path
) -> None:
    with pytest.raises(UsageError, match="not an effort directory"):
        prepare_takeover(
            TakeoverRequest(effort_dir=target_repo / "docs", state_dir=state_dir)
        )


def test_a_run_that_never_finished_its_root_never_claimed_the_effort(
    target_repo: Path, state_dir: Path
) -> None:
    a_stopped_run(
        target_repo,
        state_dir,
        status=RunStatus.RUNNING,
        liveness_pid=dead_pid(),
        root_status=NodeStatus.IN_PROGRESS,
        root_graph=None,
    )
    # A root that emitted no graph never claimed the effort at all — so the
    # tickets on disk are ownerless, and takeover mints a fresh run for them.
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    assert prepared.created is True
    assert prepared.manifest.route is Route.TAKEOVER


def test_a_claiming_run_whose_root_never_finished_is_still_refused(
    target_repo: Path, state_dir: Path
) -> None:
    """A grill or wayfinder run that emitted its graph but never completed its
    root is a planning conversation that cannot be resumed."""
    a_stopped_run(
        target_repo,
        state_dir,
        status=RunStatus.RUNNING,
        liveness_pid=dead_pid(),
        root_status=NodeStatus.IN_PROGRESS,
    )
    with pytest.raises(TakeoverError, match="root session"):
        prepare_takeover(a_request(target_repo, state_dir))


def test_a_run_claimed_between_prepare_and_execute_is_refused_untouched(
    target_repo: Path, state_dir: Path
) -> None:
    """The guard runs again at the first moment of execution, and a refused
    loser leaves the winner's run directory exactly as it found it."""
    run = a_stopped_run(target_repo, state_dir)
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    # A competing takeover claims the run after our prepare read it.
    from auto.model import Liveness

    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
    claimed = run.read_manifest()
    claimed.status = RunStatus.RUNNING
    claimed.ended_at = None
    run.write_manifest(claimed)
    run.write_liveness(Liveness(pid=os.getpid(), started_at=now, heartbeat_at=now))

    with pytest.raises(TakeoverError, match="live orchestrator"):
        execute_takeover(prepared, a_takeover_launcher())
    assert run.read_manifest().status is RunStatus.RUNNING
    assert run.reconciliation_records() == []


# --- revival ----------------------------------------------------------------


def test_the_manifest_is_revived_with_fresh_configuration(
    target_repo: Path, state_dir: Path
) -> None:
    """Status back to running, the end erased, and nothing inherited from the
    old run's config: the resumed budgets and model are the operator's."""
    a_stopped_run(target_repo, state_dir)
    prepared = prepare_takeover(
        a_request(
            target_repo, state_dir, overrides=ConfigOverrides(concurrency=2)
        )
    )
    manifest = prepared.manifest
    assert manifest.status is RunStatus.RUNNING
    assert manifest.ended_at is None
    assert manifest.config.concurrency == 2
    assert manifest.config.run_budget_usd != OLD_CONFIG.run_budget_usd
    assert manifest.config.session_budget_usd != OLD_CONFIG.session_budget_usd


# --- the walking skeleton ---------------------------------------------------


def test_a_clean_effort_is_revived_and_resumed_to_completion(
    target_repo: Path, state_dir: Path
) -> None:
    run = a_stopped_run(target_repo, state_dir)
    launcher = a_takeover_launcher()
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )

    assert manifest.status is RunStatus.DONE
    assert manifest.ended_at is not None
    # Only the pending node was dispatched, through the stock loop.
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/02-render.md"
    ]
    # The takeover recorded its own liveness while it ran.
    liveness = run.read_liveness()
    assert liveness is not None and liveness.pid == os.getpid()


def test_the_reconciliation_record_carries_the_verdict_as_a_tool_call(
    target_repo: Path, state_dir: Path
) -> None:
    run = a_stopped_run(target_repo, state_dir)
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), a_takeover_launcher()
    )

    records = run.reconciliation_records()
    assert len(records) == 1
    record = records[0]
    assert record.reconciliation_id == "0001-add-search"
    assert record.verdict is ReconciliationVerdict.CLEAN
    assert [(call.tool, call.accepted) for call in record.tool_calls] == [
        ("report_effort_clean", True)
    ]
    assert record.ended_at is not None
    # What was examined was gathered by the harness, not the agent.
    assert any("manifest status `failed`" in line for line in record.examined)
    assert any("01-index" in line and "closed out" in line for line in record.examined)
    assert any("02-render" in line and "open" in line for line in record.examined)


def test_the_consultation_is_effort_scoped_read_only_and_one_shot(
    target_repo: Path, state_dir: Path
) -> None:
    a_stopped_run(target_repo, state_dir)
    launcher = a_takeover_launcher()
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )

    spec = launcher.interventions[0]
    assert spec.node_id == f"takeover:{EFFORT}"
    assert spec.one_shot is True
    assert spec.model == "claude-opus-5"
    assert f"/efforts/{EFFORT}/mcp" in url_from(spec.mcp_config)
    assert spec.allowed_tools is not None
    assert "mcp__harness__report_effort_clean" in spec.allowed_tools
    assert "mcp__harness__reset_node" in spec.allowed_tools
    assert "mcp__harness__correct_ticket_status" in spec.allowed_tools
    assert "mcp__harness__complete_node" not in spec.allowed_tools
    assert "Read" in spec.allowed_tools
    assert "Edit" not in spec.allowed_tools


def test_a_consultation_that_lands_no_verdict_stops_the_takeover(
    target_repo: Path, state_dir: Path
) -> None:
    run = a_stopped_run(target_repo, state_dir)
    launcher = a_takeover_launcher(
        consultation=says_nothing("Node 01 is done on paper only.")
    )
    with pytest.raises(TakeoverError, match="no clean verdict"):
        execute_takeover(
            prepare_takeover(a_request(target_repo, state_dir)), launcher
        )

    assert launcher.launched == []  # nothing was dispatched
    manifest = run.read_manifest()
    assert manifest.status is RunStatus.FAILED
    assert manifest.ended_at is not None
    record = run.reconciliation_records()[0]
    assert record.verdict is None
    assert record.prose == "Node 01 is done on paper only."


# --- graph corrections ------------------------------------------------------


def test_a_phantom_done_node_is_reset_to_pending_and_reexecuted(
    target_repo: Path, state_dir: Path
) -> None:
    """A node recorded done whose work the repo does not show goes back to
    pending, and the resumed run re-executes it."""
    a_stopped_run(target_repo, state_dir, first_ticket=PHANTOM_FIRST_TICKET)
    launcher = a_correcting_launcher(
        corrects(("01-index", "ticket 01 is still open and the repo shows no index"))
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )
    assert manifest.status is RunStatus.DONE
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/01-index.md",
        f"/implement .scratch/{EFFORT}/issues/02-render.md",
    ]


def test_a_reset_failed_node_unblocks_its_dependents(
    target_repo: Path, state_dir: Path
) -> None:
    """A failed node blocks its dependents for good; reset to pending, the
    work is redone and what depended on it proceeds."""
    a_stopped_run(
        target_repo,
        state_dir,
        first_ticket=PHANTOM_FIRST_TICKET,
        first_node_status=NodeStatus.FAILED,
    )
    launcher = a_correcting_launcher(
        corrects(("01-index", "the failure was transient; the ticket is doable"))
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )
    assert manifest.status is RunStatus.DONE
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/01-index.md",
        f"/implement .scratch/{EFFORT}/issues/02-render.md",
    ]


def test_every_correction_lands_in_the_reconciliation_record(
    target_repo: Path, state_dir: Path
) -> None:
    """Prior status, new status and the evidence, under a `corrected` verdict —
    so a quiet record is evidence of health, and a corrected one says what
    changed and why."""
    run = a_stopped_run(target_repo, state_dir, first_ticket=PHANTOM_FIRST_TICKET)
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)),
        a_correcting_launcher(
            corrects(("01-index", "ticket 01 is still open; the repo shows no index"))
        ),
    )

    record = run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CORRECTED
    assert [
        (c.node, c.prior_status, c.new_status, c.evidence)
        for c in record.corrections
    ] == [
        (
            "01-index",
            NodeStatus.DONE,
            NodeStatus.PENDING,
            "ticket 01 is still open; the repo shows no index",
        )
    ]
    assert [(call.tool, call.accepted) for call in record.tool_calls] == [
        ("reset_node", True),
        ("report_effort_clean", True),
    ]


def test_an_effort_needing_no_correction_keeps_the_clean_verdict(
    target_repo: Path, state_dir: Path
) -> None:
    run = a_stopped_run(target_repo, state_dir)
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), a_takeover_launcher()
    )
    record = run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CLEAN
    assert record.corrections == []


def test_a_refused_reset_is_recorded_not_dropped(
    target_repo: Path, state_dir: Path
) -> None:
    """A reset naming no node, or disputing a status only revival may touch,
    is refused — and the refusal is part of the record, not silence."""
    run = a_stopped_run(target_repo, state_dir)
    consultation = ScriptedAgent(
        calls=[
            ("reset_node", {"node": "09-imagined", "evidence": "no such work"}),
            ("reset_node", {"node": "02-render", "evidence": "still open"}),
            ("report_effort_clean", {"summary": "Nothing actually disagreed."}),
        ]
    )
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)),
        a_takeover_launcher(consultation=consultation),
    )

    record = run.reconciliation_records()[0]
    assert record.corrections == []
    assert record.verdict is ReconciliationVerdict.CLEAN
    refusals = [(call.tool, call.refused) for call in record.tool_calls]
    assert len(refusals) == 3
    assert refusals[0][0] == "reset_node"
    assert refusals[0][1] is not None and "names no node" in refusals[0][1]
    assert refusals[1][0] == "reset_node"
    assert refusals[1][1] is not None and "pending" in refusals[1][1]
    assert refusals[2] == ("report_effort_clean", None)


def test_an_effort_with_phantom_done_and_failed_nodes_is_taken_over_and_completes(
    target_repo: Path, state_dir: Path
) -> None:
    """The reference corruption in miniature: one node done on paper only, one
    failed, and a dependent blocked by both — corrected, re-dispatched, done."""
    run = a_stopped_run(target_repo, state_dir)
    repo = target_repo.resolve()
    issues = repo / ".scratch" / EFFORT / "issues"
    tickets = {
        "01-index": "# 01: Index\n\n**Blocked by:** None\n\n**Status:** ready-for-agent\n",
        "02-render": "# 02: Render\n\n**Blocked by:** None\n\n**Status:** ready-for-agent\n",
        "03-wire-up": "# 03: Wire up\n\n**Blocked by:** 01, 02\n\n**Status:** ready-for-agent\n",
    }
    for stem, body in tickets.items():
        (issues / f"{stem}.md").write_text(body)

    def node(stem: str, status: NodeStatus, blocked_by: list[str]) -> GraphNode:
        return GraphNode(
            node_id=stem,
            ticket=f".scratch/{EFFORT}/issues/{stem}.md",
            ticket_type=TicketType.IMPLEMENT,
            blocked_by=blocked_by,
            status=status,
            session_id="s-old" if status is not NodeStatus.PENDING else None,
        )

    graph = Graph(
        graph_id=EFFORT,
        spawned_by="root",
        nodes=[
            node("01-index", NodeStatus.DONE, []),
            node("02-render", NodeStatus.FAILED, []),
            node("03-wire-up", NodeStatus.PENDING, ["01-index", "02-render"]),
        ],
    )
    (repo / ".scratch" / EFFORT / "graph.json").write_text(
        graph.model_dump_json(indent=2) + "\n"
    )

    def closed(stem: str) -> dict[str, str]:
        return {
            f".scratch/{EFFORT}/issues/{stem}.md": tickets[stem].replace(
                "ready-for-agent", "done"
            )
        }

    launcher = HarnessLauncher(
        {
            f"{EFFORT}/01-index": one_turn("Indexed."),
            f"{EFFORT}/02-render": one_turn("Rendered."),
            f"{EFFORT}/03-wire-up": one_turn("Wired up."),
        },
        agents={
            f"takeover:{EFFORT}": [
                corrects(
                    ("01-index", "ticket 01 is open; the repo shows no index"),
                    ("02-render", "the render failure was transient"),
                )
            ],
            f"{EFFORT}/01-index": [completes()],
            f"{EFFORT}/02-render": [completes()],
            f"{EFFORT}/03-wire-up": [completes()],
        },
        writes={
            f"{EFFORT}/01-index": [closed("01-index")],
            f"{EFFORT}/02-render": [closed("02-render")],
            f"{EFFORT}/03-wire-up": [closed("03-wire-up")],
        },
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )

    assert manifest.status is RunStatus.DONE
    assert sorted(spec.message for spec in launcher.launched) == [
        f"/implement .scratch/{EFFORT}/issues/01-index.md",
        f"/implement .scratch/{EFFORT}/issues/02-render.md",
        f"/implement .scratch/{EFFORT}/issues/03-wire-up.md",
    ]
    # The dependent went last: its blockers had to be redone first.
    assert launcher.launched[-1].message.endswith("03-wire-up.md")
    record = run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CORRECTED
    assert [(c.node, c.prior_status) for c in record.corrections] == [
        ("01-index", NodeStatus.DONE),
        ("02-render", NodeStatus.FAILED),
    ]


def test_gate_numbering_continues_across_a_takeover(
    target_repo: Path, state_dir: Path
) -> None:
    """The gates on disk are the review history; a continued run never
    renumbers it."""
    run = a_stopped_run(target_repo, state_dir)
    run.write_gate(
        Gate(
            gate_id="0002-root",
            sequence=2,
            kind=GateKind.USER_PING,
            node=None,
            question="From the first life of this run.",
            raised_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC),
        )
    )
    launcher = a_takeover_launcher(
        ScriptedAgent(
            calls=[
                ("ping_user", {"message": "Resumed and finishing up."}),
                ("complete_node", {"summary": "Rendered."}),
            ]
        ),
        consultation=reports_clean(),
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )
    assert manifest.status is RunStatus.DONE
    assert [gate.sequence for gate in run.gate_records()] == [2, 3]


def test_reconciliation_records_are_numbered_across_takeovers(
    target_repo: Path, state_dir: Path
) -> None:
    run = a_stopped_run(target_repo, state_dir)
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), a_takeover_launcher()
    )
    # A second takeover over the now-finished run: everything is done, so it
    # reconciles, resumes, dispatches nothing, and ends done again.
    second = HarnessLauncher(
        {}, agents={f"takeover:{EFFORT}": [reports_clean()]}
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), second
    )
    assert manifest.status is RunStatus.DONE
    assert second.launched == []
    assert [record.reconciliation_id for record in run.reconciliation_records()] == [
        "0001-add-search",
        "0002-add-search",
    ]


# --- ticket corrections -----------------------------------------------------


PREMATURELY_CLOSED_TICKET = """# 02: Render the results

**Type:** implement

**Blocked by:** 01

**Status:** done
"""
"""A ticket closed out while its work never landed: the lie a from-scratch
derivation would import as done."""


class PeekingLauncher(HarnessLauncher):
    """A launcher that reads one file at each driven dispatch, so a test can
    see the ticket as the resumed session will — after the consultation's
    corrections, before the session overwrites it."""

    def __init__(
        self,
        peek: Path,
        sessions: Mapping[str, Recording],
        agents: Agents = (),
        writes: Writes | Mapping[str, Writes] = (),
    ) -> None:
        super().__init__(sessions, agents=agents, writes=writes)
        self._peek = peek
        self.peeked: list[str] = []

    async def launch(self, spec: LaunchSpec) -> Any:
        if not spec.one_shot:
            self.peeked.append(self._peek.read_text())
        return await super().launch(spec)


def corrects_ticket(
    node: str = "02-render",
    status: str = "ready-for-agent",
    evidence: str = "the repo shows no rendering; the ticket was closed without the work",
) -> tuple[str, dict[str, str]]:
    return (
        "correct_ticket_status",
        {"node": node, "status": status, "evidence": evidence},
    )


def test_a_lying_status_line_is_corrected_in_the_ticket_before_work_resumes(
    target_repo: Path, state_dir: Path
) -> None:
    """The Status line is rewritten and a note appended; everything else in
    the ticket — content, Type, Blocked by — is exactly as the session left
    it."""
    a_stopped_run(target_repo, state_dir)
    ticket = target_repo.resolve() / ".scratch" / EFFORT / "issues" / "02-render.md"
    ticket.write_text(PREMATURELY_CLOSED_TICKET)
    consultation = ScriptedAgent(
        calls=[
            corrects_ticket(),
            ("report_effort_clean", {"summary": "Corrected; all agrees now."}),
        ]
    )
    launcher = PeekingLauncher(
        ticket,
        {f"{EFFORT}/02-render": one_turn("Rendered the results.")},
        agents={
            f"takeover:{EFFORT}": [consultation],
            f"{EFFORT}/02-render": [completes()],
        },
        writes={
            f"{EFFORT}/02-render": [
                {f".scratch/{EFFORT}/issues/02-render.md": CLOSED_SECOND_TICKET}
            ]
        },
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )

    assert manifest.status is RunStatus.DONE
    corrected = launcher.peeked[0]
    assert "**Status:** ready-for-agent" in corrected
    assert "**Status:** done" not in corrected
    assert "Reconciliation note" in corrected
    assert "the repo shows no rendering" in corrected
    # Nothing else in the ticket was touched.
    assert corrected.startswith("# 02: Render the results")
    assert "**Type:** implement" in corrected
    assert "**Blocked by:** 01" in corrected


def test_a_ticket_correction_alone_lands_the_corrected_verdict(
    target_repo: Path, state_dir: Path
) -> None:
    """The record accounts for the ticket correction with its evidence, under
    a `corrected` verdict, even though no graph node changed."""
    run = a_stopped_run(target_repo, state_dir)
    ticket = target_repo.resolve() / ".scratch" / EFFORT / "issues" / "02-render.md"
    ticket.write_text(PREMATURELY_CLOSED_TICKET)
    consultation = ScriptedAgent(
        calls=[
            corrects_ticket(),
            ("report_effort_clean", {"summary": "Corrected; all agrees now."}),
        ]
    )
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)),
        a_takeover_launcher(consultation=consultation),
    )

    record = run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CORRECTED
    assert record.corrections == []
    assert [
        (c.node, c.ticket, c.prior_status, c.new_status, c.evidence)
        for c in record.ticket_corrections
    ] == [
        (
            "02-render",
            f".scratch/{EFFORT}/issues/02-render.md",
            "done",
            "ready-for-agent",
            "the repo shows no rendering; the ticket was closed without the work",
        )
    ]
    assert [(call.tool, call.accepted) for call in record.tool_calls] == [
        ("correct_ticket_status", True),
        ("report_effort_clean", True),
    ]


def test_a_corrected_ticket_no_longer_imports_as_done_on_a_fresh_derivation(
    target_repo: Path, state_dir: Path
) -> None:
    """The point of correcting the file itself: a from-scratch derivation —
    no held graph, first sight — reads the corrected line, not the lie."""
    a_stopped_run(target_repo, state_dir)
    repo = target_repo.resolve()
    ticket = repo / ".scratch" / EFFORT / "issues" / "02-render.md"
    ticket.write_text(PREMATURELY_CLOSED_TICKET)
    lied = derive(repo, EFFORT, spawned_by="root")
    node = lied.node("02-render")
    assert node is not None and node.status is NodeStatus.DONE

    # The consultation corrects the ticket and then trails off without a
    # verdict: the correction has already landed in the file, so even a
    # stopped takeover leaves the lie repaired.
    consultation = ScriptedAgent(
        calls=[corrects_ticket()], prose="Corrected the ticket; unsure beyond that."
    )
    with pytest.raises(TakeoverError, match="no clean verdict"):
        execute_takeover(
            prepare_takeover(a_request(target_repo, state_dir)),
            a_takeover_launcher(consultation=consultation),
        )

    fresh = derive(repo, EFFORT, spawned_by="root")
    node = fresh.node("02-render")
    assert node is not None and node.status is NodeStatus.PENDING


def test_a_phantom_done_nodes_ticket_lie_is_repaired_alongside_its_reset(
    target_repo: Path, state_dir: Path
) -> None:
    """The reference corruption: a node done on paper with its ticket closed
    out. The graph reset re-executes it; the ticket correction stops the lie
    from being re-imported. Both land in one record."""
    run = a_stopped_run(target_repo, state_dir)
    consultation = ScriptedAgent(
        calls=[
            ("reset_node", {"node": "01-index", "evidence": "the repo shows no index"}),
            corrects_ticket(
                node="01-index",
                evidence="the ticket was closed out with no index in the repo",
            ),
            ("report_effort_clean", {"summary": "Reset and corrected."}),
        ]
    )
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)),
        a_correcting_launcher(consultation),
    )

    assert manifest.status is RunStatus.DONE
    record = run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CORRECTED
    assert [(c.node, c.prior_status) for c in record.corrections] == [
        ("01-index", NodeStatus.DONE)
    ]
    assert [(c.node, c.prior_status, c.new_status) for c in record.ticket_corrections] == [
        ("01-index", "done", "ready-for-agent")
    ]
    assert [(call.tool, call.accepted) for call in record.tool_calls] == [
        ("reset_node", True),
        ("correct_ticket_status", True),
        ("report_effort_clean", True),
    ]


def test_a_refused_ticket_correction_is_recorded_not_dropped(
    target_repo: Path, state_dir: Path
) -> None:
    """A correction naming no node, correcting a ticket that tells no lie, or
    trying to close a ticket out is refused — recorded, and the file
    untouched."""
    run = a_stopped_run(target_repo, state_dir)
    issues = target_repo.resolve() / ".scratch" / EFFORT / "issues"
    consultation = ScriptedAgent(
        calls=[
            corrects_ticket(node="09-imagined"),
            corrects_ticket(node="02-render", evidence="it says done"),
            corrects_ticket(node="01-index", status="done", evidence="looks finished"),
            ("report_effort_clean", {"summary": "Nothing actually disagreed."}),
        ]
    )
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)),
        a_takeover_launcher(consultation=consultation),
    )

    # No refused call wrote anything: ticket 01 is verbatim what the session
    # left, and ticket 02 — redone by the resumed run — carries no note.
    assert (issues / "01-index.md").read_text() == DONE_TICKET
    assert "Reconciliation note" not in (issues / "02-render.md").read_text()
    record = run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CLEAN
    assert record.ticket_corrections == []
    refusals = [(call.tool, call.refused) for call in record.tool_calls]
    assert len(refusals) == 4
    assert refusals[0][1] is not None and "names no node" in refusals[0][1]
    assert refusals[1][1] is not None and "no closed-out" in refusals[1][1]
    assert refusals[2][1] is not None and "closes the ticket out" in refusals[2][1]
    assert refusals[3] == ("report_effort_clean", None)


def test_reopening_handles_every_status_line_shape() -> None:
    """Bolded or bare, any closed-out synonym, and every lying line at once —
    what remains must never match a first-sight import again."""
    bolded = reopen_status_lines("**Status:** done\n", "ready-for-agent")
    assert bolded is not None and bolded[0] == "**Status:** ready-for-agent\n"
    assert bolded[1] == "done"

    bare = reopen_status_lines("Status: Completed early\n", "open")
    assert bare is not None and bare[0] == "Status: open\n"
    assert bare[1] == "Completed early"

    wrapped = reopen_status_lines("**Status: done**\n", "ready-for-agent")
    assert wrapped is not None and wrapped[0] == "**Status: ready-for-agent**\n"
    assert wrapped[1] == "done"

    doubled = reopen_status_lines(
        "Status: resolved\n\nbody\n\n**Status:** closed\n", "ready-for-agent"
    )
    assert doubled is not None
    assert doubled[0] == (
        "Status: ready-for-agent\n\nbody\n\n**Status:** ready-for-agent\n"
    )

    assert reopen_status_lines("**Status:** ready-for-agent\n", "open") is None
    assert reopen_status_lines("# A ticket with no status line\n", "open") is None


# --- the no-run case: the takeover route ------------------------------------


def a_bare_effort(
    target_repo: Path,
    *,
    effort: str = EFFORT,
    tickets: Mapping[str, str] | None = None,
) -> Path:
    """An effort the implement-only entry point is for: hand-written tickets
    on disk, no graph and no run anywhere."""
    repo = target_repo.resolve()
    issues = repo / ".scratch" / effort / "issues"
    issues.mkdir(parents=True, exist_ok=True)
    for name, body in (
        tickets
        if tickets is not None
        else {"01-index.md": PHANTOM_FIRST_TICKET, "02-render.md": OPEN_TICKET}
    ).items():
        (issues / name).write_text(body)
    return repo / ".scratch" / effort


def a_created_run_launcher(
    consultation: ScriptedAgent | None = None,
) -> HarnessLauncher:
    """A consultation, and replayed sessions ready to resolve both hand-written
    tickets, each closing its ticket out."""
    return HarnessLauncher(
        {
            f"{EFFORT}/01-index": one_turn("Indexed the settings content."),
            f"{EFFORT}/02-render": one_turn("Rendered the results."),
        },
        agents={
            f"takeover:{EFFORT}": [
                consultation if consultation is not None else reports_clean()
            ],
            f"{EFFORT}/01-index": [completes()],
            f"{EFFORT}/02-render": [completes()],
        },
        writes={
            f"{EFFORT}/01-index": [
                {f".scratch/{EFFORT}/issues/01-index.md": DONE_TICKET}
            ],
            f"{EFFORT}/02-render": [
                {f".scratch/{EFFORT}/issues/02-render.md": CLOSED_SECOND_TICKET}
            ],
        },
    )


def test_takeover_with_no_run_creates_one_under_the_takeover_route(
    target_repo: Path, state_dir: Path
) -> None:
    """The root node carries the effort path where an ordinary root carries
    the pasted prompt, and the run is on disk — listed like any other."""
    a_bare_effort(target_repo)
    prepared = prepare_takeover(a_request(target_repo, state_dir))

    assert prepared.created is True
    manifest = prepared.manifest
    assert manifest.route is Route.TAKEOVER
    assert manifest.status is RunStatus.RUNNING
    assert manifest.prompt == f".scratch/{EFFORT}"
    root = manifest.root_node
    assert root.type is None
    assert root.prompt == f".scratch/{EFFORT}"
    assert root.graph == EFFORT
    assert root.status is NodeStatus.PENDING
    assert [m.run_id for m in list_runs(state_dir)] == [manifest.run_id]


def test_hand_written_tickets_run_to_completion_through_the_takeover_route(
    target_repo: Path, state_dir: Path
) -> None:
    a_bare_effort(target_repo)
    launcher = a_created_run_launcher()
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )

    assert manifest.status is RunStatus.DONE
    assert manifest.ended_at is not None
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/01-index.md",
        f"/implement .scratch/{EFFORT}/issues/02-render.md",
    ]


def test_the_created_runs_root_session_is_the_reconciliation(
    target_repo: Path, state_dir: Path
) -> None:
    """The reconciliation is the root's session, and adoption is the moment
    the root is done — the takeover route's counterpart of an ordinary root
    completing on its emitted graph."""
    a_bare_effort(target_repo)
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    run = prepared.run
    execute_takeover(prepared, a_created_run_launcher())

    manifest = run.read_manifest()
    records = run.reconciliation_records()
    assert len(records) == 1
    root = manifest.root_node
    assert root.session_id == records[0].session_id
    assert root.status is NodeStatus.DONE
    assert root.graph == EFFORT
    assert records[0].verdict is ReconciliationVerdict.CLEAN


def test_a_created_runs_consultation_is_briefed_on_hand_written_tickets(
    target_repo: Path, state_dir: Path
) -> None:
    """The brief must not claim a run stopped without finishing — none ever
    ran — and the evidence says the run was created by this takeover."""
    a_bare_effort(target_repo)
    launcher = a_created_run_launcher()
    execute_takeover(prepare_takeover(a_request(target_repo, state_dir)), launcher)

    brief = launcher.interventions[0].message
    assert "no run has ever driven" in brief
    assert "stopped without finishing" not in brief
    assert "created by this takeover" in brief


def test_a_hand_written_closed_out_ticket_is_imported_done_and_not_redispatched(
    target_repo: Path, state_dir: Path
) -> None:
    """First-sight derivation trusts a closed-out `Status:` line, exactly as
    an emitted graph would; the reconciliation is where that trust is checked."""
    a_bare_effort(
        target_repo,
        tickets={"01-index.md": DONE_TICKET, "02-render.md": OPEN_TICKET},
    )
    launcher = a_takeover_launcher()
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )

    assert manifest.status is RunStatus.DONE
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/02-render.md"
    ]


def test_a_first_sight_import_the_repo_contradicts_is_reset_and_redone(
    target_repo: Path, state_dir: Path
) -> None:
    """A hand-written ticket closed out with no work behind it: imported done
    on trust, disputed by the consultation, re-executed by the created run."""
    a_bare_effort(
        target_repo,
        tickets={"01-index.md": DONE_TICKET, "02-render.md": OPEN_TICKET},
    )
    prepared = prepare_takeover(a_request(target_repo, state_dir))
    launcher = a_created_run_launcher(
        consultation=corrects(
            ("01-index", "the ticket claims done but the repo shows no index")
        )
    )
    manifest = execute_takeover(prepared, launcher)

    assert manifest.status is RunStatus.DONE
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/01-index.md",
        f"/implement .scratch/{EFFORT}/issues/02-render.md",
    ]
    record = prepared.run.reconciliation_records()[0]
    assert record.verdict is ReconciliationVerdict.CORRECTED


def test_an_effort_spawned_as_a_subgraph_is_not_minted_a_run(
    target_repo: Path, state_dir: Path
) -> None:
    """A persisted graph spawned by a node belongs to some run's subtree; the
    effort is taken over at the root of that run, never adopted as an orphan."""
    a_bare_effort(target_repo)
    graph = Graph(
        graph_id=EFFORT,
        spawned_by="other-effort/03-wire-up",
        nodes=[
            GraphNode(
                node_id="01-index",
                ticket=f".scratch/{EFFORT}/issues/01-index.md",
                ticket_type=TicketType.IMPLEMENT,
            )
        ],
    )
    (target_repo.resolve() / ".scratch" / EFFORT / "graph.json").write_text(
        graph.model_dump_json(indent=2) + "\n"
    )
    with pytest.raises(UsageError, match="subgraph"):
        prepare_takeover(a_request(target_repo, state_dir))
    assert list_runs(state_dir) == []


def test_a_stopped_created_run_is_taken_over_again_not_duplicated(
    target_repo: Path, state_dir: Path
) -> None:
    """The takeover route's root is the reconciliation, so a takeover that
    stopped without a verdict is simply reconciled again — the existing run is
    located and continued, never re-minted."""
    a_bare_effort(target_repo)
    first = HarnessLauncher(
        {}, agents={f"takeover:{EFFORT}": [says_nothing("Cannot tell.")]}
    )
    with pytest.raises(TakeoverError, match="no clean verdict"):
        execute_takeover(prepare_takeover(a_request(target_repo, state_dir)), first)

    prepared = prepare_takeover(a_request(target_repo, state_dir))
    assert prepared.created is False
    manifest = execute_takeover(prepared, a_created_run_launcher())

    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.status is NodeStatus.DONE
    assert len(list_runs(state_dir)) == 1
    assert [
        record.reconciliation_id for record in prepared.run.reconciliation_records()
    ] == ["0001-add-search", "0002-add-search"]


def test_a_node_the_crash_left_in_flight_is_returned_to_pending_and_redone(
    target_repo: Path, state_dir: Path
) -> None:
    """A continued run holds no live sessions, so nothing can actually be in
    progress: revival arithmetic, ahead of any judgment."""
    a_stopped_run(
        target_repo,
        state_dir,
        status=RunStatus.RUNNING,
        liveness_pid=dead_pid(),
        second_node_status=NodeStatus.IN_PROGRESS,
        second_node_session="s-02",
    )
    launcher = a_takeover_launcher()
    manifest = execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir)), launcher
    )
    assert manifest.status is RunStatus.DONE
    assert [spec.message for spec in launcher.launched] == [
        f"/implement .scratch/{EFFORT}/issues/02-render.md"
    ]
