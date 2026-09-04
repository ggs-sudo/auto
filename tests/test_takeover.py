"""Takeover, driven for real above the seam: an effort whose run stopped is
located, guarded, judged clean by a scripted agent over the real tool plumbing,
and resumed through the stock loop with replayed sessions."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto.config import ConfigOverrides
from auto.errors import TakeoverError, UsageError
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
from auto.run import RunDirectory
from auto.takeover import TakeoverRequest, execute_takeover, prepare_takeover
from tests.agents import HarnessLauncher, ScriptedAgent, completes, reports_clean, says_nothing, url_from
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
    (issues / "01-index.md").write_text(DONE_TICKET)
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
                status=NodeStatus.DONE,
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


def test_takeover_with_no_run_for_the_effort_is_refused(
    target_repo: Path, state_dir: Path
) -> None:
    effort_dir = target_repo / ".scratch" / EFFORT
    effort_dir.mkdir(parents=True)
    with pytest.raises(UsageError, match="no run holds"):
        prepare_takeover(a_request(target_repo, state_dir))


def test_a_directory_outside_the_effort_root_is_refused(
    target_repo: Path, state_dir: Path
) -> None:
    with pytest.raises(UsageError, match="not an effort directory"):
        prepare_takeover(
            TakeoverRequest(effort_dir=target_repo / "docs", state_dir=state_dir)
        )


def test_a_run_that_never_finished_its_root_is_refused(
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
    with pytest.raises(UsageError, match="no run holds"):
        # A root that emitted no graph never claimed the effort at all.
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
