"""Acceptance: the reference corrupted effort, taken over in one invocation.

The corruption modeled here is the real one that motivated takeover — effort
`ai-news-pipeline` in the guinea-pig repo, runs `20260829-195546-*` and
`20260829-205742-*`. Five graph nodes were `done` with no work behind them:
their tickets carried closed-out `Status:` lines at first derivation and were
imported as done on trust. Two of those tickets were resolved during an
earlier, never-finalized run that still claimed `running` a week later, so
their sessions exist — but only in the orphan, their findings sitting on
unmerged branches. A failed node blocked two pending dependents forever, and
the lying tickets would have re-imported the same corruption on any fresh
derivation.

The demonstration: a single `auto takeover` invocation reconciles the whole
mess and drives the effort to completion, and the reconciliation record tells
the whole story — the runs examined, the aborted orphan, and every correction
with its evidence.

The consultation is scripted — weighing the repo's git history is the real
agent's judgment, out of reach at this seam — so the unmerged-branch
dimension of the reference lives in the evidence the script cites, which the
record must carry verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner, Result

from auto.graph import load_persisted
from auto.model import (
    Graph,
    GraphNode,
    Liveness,
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
from auto.run import RunDirectory, load_run
from auto.takeover import execute_takeover, prepare_takeover
from tests.agents import HarnessLauncher, ScriptedAgent, completes
from tests.conftest import dead_pid, one_turn
from tests.test_takeover import a_request

EFFORT = "ai-news-pipeline"
ORPHANED_RUN = f"20260829-195546-{EFFORT}"
CONTINUED_RUN = f"20260829-205742-{EFFORT}"

PHANTOM_STEMS = (
    "01-scope-sources",
    "02-research-feeds",
    "03-design-schema",
    "04-ingest-worker",
    "05-summarize",
)
"""Recorded done, tickets closed out, and the repo shows none of the work."""

ORPHAN_SESSIONS = {"01-scope-sources": "s-scope", "02-research-feeds": "s-feeds"}
"""The two phantoms whose sessions ran during the orphaned run — recorded
there and nowhere else."""

FAILED_STEM = "06-publish-page"
DEPENDENT_STEMS = ("07-style-page", "08-deploy")
ALL_STEMS = (*PHANTOM_STEMS, FAILED_STEM, *DEPENDENT_STEMS)

BLOCKERS: dict[str, list[str]] = {
    "01-scope-sources": [],
    "02-research-feeds": [],
    "03-design-schema": ["01-scope-sources"],
    "04-ingest-worker": ["02-research-feeds", "03-design-schema"],
    "05-summarize": ["04-ingest-worker"],
    "06-publish-page": ["05-summarize"],
    "07-style-page": ["06-publish-page"],
    "08-deploy": ["06-publish-page", "07-style-page"],
}


def a_ticket(stem: str, *, status: str) -> str:
    title = stem.split("-", 1)[1].replace("-", " ")
    blockers = ", ".join(s.split("-", 1)[0] for s in BLOCKERS[stem]) or "None"
    return (
        f"# {stem.split('-', 1)[0]}: {title}\n\n"
        f"**Blocked by:** {blockers}\n\n"
        f"**Status:** {status}\n"
    )


def ticket_path(stem: str) -> str:
    return f".scratch/{EFFORT}/issues/{stem}.md"


def the_reference_corruption(target_repo: Path, state_dir: Path) -> None:
    """The effort and both runs, laid out the way the corruption left them."""
    repo = target_repo.resolve()
    issues = repo / ".scratch" / EFFORT / "issues"
    issues.mkdir(parents=True, exist_ok=True)
    for stem in PHANTOM_STEMS:
        # The lie: closed out with no work behind it, imported done on trust.
        (issues / f"{stem}.md").write_text(a_ticket(stem, status="done"))
    for stem in (FAILED_STEM, *DEPENDENT_STEMS):
        (issues / f"{stem}.md").write_text(a_ticket(stem, status="ready-for-agent"))

    def node(stem: str, status: NodeStatus, session_id: str | None) -> GraphNode:
        return GraphNode(
            node_id=stem,
            ticket=ticket_path(stem),
            ticket_type=TicketType.IMPLEMENT,
            blocked_by=BLOCKERS[stem],
            status=status,
            session_id=session_id,
        )

    graph = Graph(
        graph_id=EFFORT,
        spawned_by="root",
        nodes=[
            *(
                node(stem, NodeStatus.DONE, ORPHAN_SESSIONS.get(stem))
                for stem in PHANTOM_STEMS
            ),
            node(FAILED_STEM, NodeStatus.FAILED, "s-publish"),
            *(node(stem, NodeStatus.PENDING, None) for stem in DEPENDENT_STEMS),
        ],
    )
    (repo / ".scratch" / EFFORT / "graph.json").write_text(
        graph.model_dump_json(indent=2) + "\n"
    )

    def manifest(
        run_id: str, created: datetime, status: RunStatus, ended: datetime | None
    ) -> Manifest:
        return Manifest(
            run_id=run_id,
            route=Route.WAYFINDER,
            prompt="Build the AI news pipeline.",
            target_repo=str(repo),
            worktree=str(repo),
            branch="main",
            config=ResolvedConfig(),
            created_at=created,
            ended_at=ended,
            status=status,
            root_node=RootNode(
                type=NodeType.WAYFINDER,
                prompt="Build the AI news pipeline.",
                status=NodeStatus.DONE,
                session_id="s-root",
                graph=EFFORT,
            ),
        )

    # The orphan: never finalized, still claiming `running` a week later —
    # its orchestrator long dead. It holds the sessions behind two phantoms.
    orphan_started = datetime(2026, 8, 29, 19, 55, 46, tzinfo=UTC)
    orphan = RunDirectory.create(
        state_dir, manifest(ORPHANED_RUN, orphan_started, RunStatus.RUNNING, None)
    )
    orphan.write_liveness(
        Liveness(
            pid=dead_pid(), started_at=orphan_started, heartbeat_at=orphan_started
        )
    )
    for stem, session_id in ORPHAN_SESSIONS.items():
        orphan.write_session(
            SessionRecord(
                session_id=session_id,
                node=f"{EFFORT}/{stem}",
                role=NodeType.IMPLEMENT,
                started_at=orphan_started,
                captured_transcript=f"transcripts/{session_id}.jsonl",
            )
        )

    # The newer run: the one takeover continues. It stopped failed, its only
    # session record the failed publish node's.
    continued_started = datetime(2026, 8, 29, 20, 57, 42, tzinfo=UTC)
    continued = RunDirectory.create(
        state_dir,
        manifest(
            CONTINUED_RUN, continued_started, RunStatus.FAILED, continued_started
        ),
    )
    continued.write_session(
        SessionRecord(
            session_id="s-publish",
            node=f"{EFFORT}/{FAILED_STEM}",
            role=NodeType.IMPLEMENT,
            started_at=continued_started,
            captured_transcript="transcripts/s-publish.jsonl",
        )
    )


def phantom_evidence(stem: str) -> str:
    evidence = (
        f"ticket {stem} was imported done on trust; "
        "the repo shows none of its work on main"
    )
    if stem in ORPHAN_SESSIONS:
        evidence += (
            " — its session ran during the orphaned run and its findings "
            "sit on an unmerged branch"
        )
    return evidence


def the_reference_consultation() -> ScriptedAgent:
    """The judgment the corruption calls for: every phantom reset and its
    lying ticket reopened, the failed node reset, then the clean report."""
    calls: list[tuple[str, dict[str, str]]] = []
    for stem in PHANTOM_STEMS:
        calls.append(("reset_node", {"node": stem, "evidence": phantom_evidence(stem)}))
        calls.append(
            (
                "correct_ticket_status",
                {
                    "node": stem,
                    "status": "ready-for-agent",
                    "evidence": phantom_evidence(stem),
                },
            )
        )
    calls.append(
        (
            "reset_node",
            {
                "node": FAILED_STEM,
                "evidence": "the repo shows no publish page and nothing "
                "preventing one: the failure was transient and the ticket "
                "is doable",
            },
        )
    )
    calls.append(
        (
            "report_effort_clean",
            {
                "summary": "Six nodes reset and five lying tickets reopened; "
                "the record now agrees with the repo."
            },
        )
    )
    return ScriptedAgent(calls=calls)


def the_reference_launcher() -> HarnessLauncher:
    """Replayed sessions ready to do all eight tickets for real, each closing
    its ticket out as a session doing the work would."""
    return HarnessLauncher(
        {f"{EFFORT}/{stem}": one_turn(f"Resolved {stem}.") for stem in ALL_STEMS},
        agents={
            f"takeover:{EFFORT}": [the_reference_consultation()],
            **{f"{EFFORT}/{stem}": [completes()] for stem in ALL_STEMS},
        },
        writes={
            f"{EFFORT}/{stem}": [{ticket_path(stem): a_ticket(stem, status="done")}]
            for stem in ALL_STEMS
        },
    )


def invoke_takeover(
    target_repo: Path, state_dir: Path, launcher: HarnessLauncher
) -> Result:
    from auto.cli import cli

    return CliRunner().invoke(
        cli,
        [
            "takeover",
            str(target_repo / ".scratch" / EFFORT),
            "--state-dir",
            str(state_dir),
        ],
        obj={"launcher": launcher},
        catch_exceptions=False,
    )


def test_one_takeover_invocation_drives_the_reference_corruption_to_completion(
    target_repo: Path, state_dir: Path
) -> None:
    """One `auto takeover <effort-dir>`: the corrupted effort ends done, every
    phantom redone, the blocked dependents unblocked and executed in
    dependency order, the orphan aborted, and the tickets telling the truth."""
    the_reference_corruption(target_repo, state_dir)
    launcher = the_reference_launcher()
    result = invoke_takeover(target_repo, state_dir, launcher)

    assert result.exit_code == 0, result.output
    assert f"taking over run {CONTINUED_RUN}" in result.output

    manifest = load_run(state_dir, CONTINUED_RUN).read_manifest()
    assert manifest.status is RunStatus.DONE
    assert manifest.ended_at is not None

    # Every node was executed — the five phantoms redone, the failed publish
    # retried, the two blocked dependents finally dispatched — respecting the
    # dependency edges.
    messages = [spec.message for spec in launcher.launched]
    assert sorted(messages) == sorted(
        f"/implement {ticket_path(stem)}" for stem in ALL_STEMS
    )
    position = {stem: messages.index(f"/implement {ticket_path(stem)}") for stem in ALL_STEMS}
    for stem, blockers in BLOCKERS.items():
        for blocker in blockers:
            assert position[blocker] < position[stem]

    # The recorded state now matches the repo: every node done, every ticket
    # closed out by the session that actually did the work.
    graph = load_persisted(target_repo.resolve(), EFFORT)
    assert all(node.status is NodeStatus.DONE for node in graph.nodes)
    for stem in ALL_STEMS:
        assert "**Status:** done" in (target_repo / ticket_path(stem)).read_text()

    # One effort, one run: the never-finalized orphan lost its claim and got
    # the end its crash never wrote.
    orphan = load_run(state_dir, ORPHANED_RUN).read_manifest()
    assert orphan.status is RunStatus.ABORTED
    assert orphan.ended_at is not None


def test_the_reconciliation_record_tells_the_whole_story(
    target_repo: Path, state_dir: Path
) -> None:
    """The record names the runs examined and the aborted orphan, locates the
    sessions that ran during the orphan, and accounts for every correction —
    graph and ticket — with its evidence."""
    the_reference_corruption(target_repo, state_dir)
    execute_takeover(
        prepare_takeover(a_request(target_repo, state_dir, effort=EFFORT)),
        the_reference_launcher(),
    )

    records = load_run(state_dir, CONTINUED_RUN).reconciliation_records()
    assert len(records) == 1
    record = records[0]

    # The runs examined: the continued run by its stopped status, the orphan
    # by its week-old `running` claim — observed crashed, and marked aborted.
    assert any(
        line.startswith(f"run {CONTINUED_RUN}") and "manifest status `failed`" in line
        for line in record.examined
    )
    orphan_lines = [
        line for line in record.examined if line.startswith(f"run {ORPHANED_RUN}")
    ]
    assert len(orphan_lines) == 1
    assert "crashed" in orphan_lines[0]
    assert "orphaned, and marked aborted as this takeover begins" in orphan_lines[0]

    # The phantoms as the harness saw them: done on paper, closed-out tickets,
    # and either no session at all or one recorded only in the orphan.
    for stem in PHANTOM_STEMS:
        (node_line,) = [
            line
            for line in record.examined
            if line.startswith(f"node {EFFORT}/{stem}:")
        ]
        assert "recorded `done`" in node_line
        assert "closed out" in node_line
        if stem in ORPHAN_SESSIONS:
            assert f"recorded in orphaned run {ORPHANED_RUN}" in node_line
        else:
            assert "no session recorded" in node_line
    (failed_line,) = [
        line
        for line in record.examined
        if line.startswith(f"node {EFFORT}/{FAILED_STEM}:")
    ]
    assert "recorded `failed`" in failed_line

    # Every correction, with its prior status, new status and evidence.
    assert record.verdict is ReconciliationVerdict.CORRECTED
    assert [
        (c.node, c.prior_status, c.new_status) for c in record.corrections
    ] == [
        *(
            (f"{EFFORT}/{stem}", NodeStatus.DONE, NodeStatus.PENDING)
            for stem in PHANTOM_STEMS
        ),
        (f"{EFFORT}/{FAILED_STEM}", NodeStatus.FAILED, NodeStatus.PENDING),
    ]
    # Every correction grounded in the repo — the failed node's included —
    # and the orphan-run phantoms' evidence carrying the reference's
    # unmerged-branch story.
    assert all("the repo shows" in c.evidence for c in record.corrections)
    for stem in ORPHAN_SESSIONS:
        correction = next(
            c for c in record.corrections if c.node == f"{EFFORT}/{stem}"
        )
        assert "unmerged branch" in correction.evidence

    # Every lying ticket reopened, and the lie named.
    assert [
        (c.node, c.ticket, c.prior_status, c.new_status)
        for c in record.ticket_corrections
    ] == [
        (f"{EFFORT}/{stem}", ticket_path(stem), "done", "ready-for-agent")
        for stem in PHANTOM_STEMS
    ]

    # The whole consultation landed as tool calls, none refused: each
    # phantom's reset and ticket correction, the failed node's reset, the
    # verdict.
    assert len(record.tool_calls) == 2 * len(PHANTOM_STEMS) + 2
    assert all(call.refused is None for call in record.tool_calls)
    assert record.tool_calls[-1].tool == "report_effort_clean"
