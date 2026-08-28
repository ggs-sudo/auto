"""Independent nodes side by side, bounded by the cap and the spend ceiling.

The point of a harness over hand-started sessions: ready nodes dispatch
concurrently, up to a configurable cap that bounds driven sessions only, and
the run's spend ceiling stops dispatch — never the sessions already running —
once driven and orchestrator spend together reach it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto.config import ConfigOverrides
from auto.model import Graph, NodeStatus, RunStatus, SessionStatus
from auto.orchestrate import execute_run, prepare_run
from tests.agents import HarnessLauncher, completes, emits_and_completes, tries_to_complete
from tests.conftest import one_turn
from tests.test_dispatch import (
    EFFORT,
    charted,
    node_id,
    resolves,
    ticket_body,
)
from tests.test_orchestrate import a_request


def independent_run(
    target_repo: Path,
    state_dir: Path,
    stems: list[str],
    *,
    overrides: ConfigOverrides | None = None,
    node_cost_usd: float = 0.42,
    root_cost_usd: float = 0.42,
) -> tuple[object, HarnessLauncher]:
    """A run whose root charts `stems` as unblocked tickets, each resolved by
    a session that goes stale once and an agent that completes it."""
    prepared = prepare_run(a_request(target_repo, state_dir, overrides=overrides))
    launcher = HarnessLauncher(
        {
            "root": one_turn(cost_usd=root_cost_usd),
            **{node_id(s): one_turn(cost_usd=node_cost_usd) for s in stems},
        },
        {
            "root": [emits_and_completes(EFFORT)],
            **{node_id(s): [completes()] for s in stems},
        },
        writes={
            "root": [charted(*((f"{s}.md", ticket_body()) for s in stems))],
            **{node_id(s): [resolves(s)] for s in stems},
        },
    )
    return prepared, launcher


def persisted_graph(target_repo: Path) -> Graph:
    return Graph.model_validate_json(
        (target_repo / ".scratch" / EFFORT / "graph.json").read_text()
    )


def test_independent_nodes_dispatch_concurrently_capped_at_four_by_default(
    target_repo: Path, state_dir: Path
) -> None:
    stems = ["01-a", "02-b", "03-c", "04-d", "05-e"]
    prepared, launcher = independent_run(target_repo, state_dir, stems)
    manifest = execute_run(prepared, launcher)  # type: ignore[arg-type]

    assert manifest.status is RunStatus.DONE
    assert launcher.max_live_driven == 4
    assert {spec.node_id for spec in launcher.launched} == {
        "root",
        *(node_id(s) for s in stems),
    }
    graph = persisted_graph(target_repo)
    assert all(node.status is NodeStatus.DONE for node in graph.nodes)


def test_the_cap_is_configurable(target_repo: Path, state_dir: Path) -> None:
    stems = ["01-a", "02-b", "03-c"]
    prepared, launcher = independent_run(
        target_repo, state_dir, stems, overrides=ConfigOverrides(concurrency=2)
    )
    manifest = execute_run(prepared, launcher)  # type: ignore[arg-type]
    assert manifest.status is RunStatus.DONE
    assert launcher.max_live_driven == 2


def test_interventions_are_not_counted_against_the_cap(
    target_repo: Path, state_dir: Path
) -> None:
    """With a cap of one, every intervention fires while a driven session
    holds the whole cap — if interventions were counted, nothing could ever
    be judged and the run would never move."""
    prepared = prepare_run(
        a_request(target_repo, state_dir, overrides=ConfigOverrides(concurrency=1))
    )
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-a"): [*one_turn("Done?"), *one_turn("Done.")],
            node_id("02-b"): one_turn(),
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id("01-a"): [
                tries_to_complete(then="Close the ticket out, please."),
                completes(),
            ],
            node_id("02-b"): [completes()],
        },
        writes={
            "root": [charted(("01-a.md", ticket_body()), ("02-b.md", ticket_body()))],
            node_id("01-a"): [{}, resolves("01-a")],
            node_id("02-b"): [resolves("02-b")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert manifest.status is RunStatus.DONE
    assert launcher.max_live_driven == 1
    assert launcher.interventions_live_driven == [1, 1, 1, 1]


def test_reaching_the_ceiling_stops_dispatch_and_lets_running_sessions_finish(
    target_repo: Path, state_dir: Path
) -> None:
    """The third node never starts; the two already running end complete."""
    stems = ["01-a", "02-b", "03-c"]
    prepared, launcher = independent_run(
        target_repo,
        state_dir,
        stems,
        overrides=ConfigOverrides(concurrency=2, run_budget_usd=0.5),
        root_cost_usd=0.1,
        node_cost_usd=0.4,
    )
    manifest = execute_run(prepared, launcher)  # type: ignore[arg-type]

    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("01-a"),
        node_id("02-b"),
    ]
    graph = persisted_graph(target_repo)
    assert graph.node("01-a").status is NodeStatus.DONE  # type: ignore[union-attr]
    assert graph.node("02-b").status is NodeStatus.DONE  # type: ignore[union-attr]
    assert graph.node("03-c").status is NodeStatus.PENDING  # type: ignore[union-attr]
    # Stopped short of its graph, the run is not done, and a script can see it.
    assert manifest.status is RunStatus.FAILED
    # Driven and orchestrator spend both counted toward the ceiling, apart.
    assert manifest.driven_spend_usd == pytest.approx(0.9)
    assert manifest.orchestrator_spend_usd == pytest.approx(0.06)


def test_a_root_that_exhausts_the_ceiling_dispatches_no_graph_node(
    target_repo: Path, state_dir: Path
) -> None:
    prepared, launcher = independent_run(
        target_repo,
        state_dir,
        ["01-a"],
        overrides=ConfigOverrides(run_budget_usd=0.3),
        root_cost_usd=0.42,
    )
    manifest = execute_run(prepared, launcher)  # type: ignore[arg-type]
    assert [spec.node_id for spec in launcher.launched] == ["root"]
    assert manifest.status is RunStatus.FAILED
    assert persisted_graph(target_repo).node("01-a").status is NodeStatus.PENDING  # type: ignore[union-attr]


def test_concurrent_interventions_leave_every_record_intact(
    target_repo: Path, state_dir: Path
) -> None:
    """Two nodes nudged side by side: their interventions overlap, and every
    run-directory file still parses whole — nothing interleaved."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-a"): [*one_turn("Done?"), *one_turn("Done.")],
            node_id("02-b"): [*one_turn("Done?"), *one_turn("Done.")],
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id("01-a"): [tries_to_complete(then="Close 01 out."), completes()],
            node_id("02-b"): [tries_to_complete(then="Close 02 out."), completes()],
        },
        writes={
            "root": [charted(("01-a.md", ticket_body()), ("02-b.md", ticket_body()))],
            node_id("01-a"): [{}, resolves("01-a")],
            node_id("02-b"): [{}, resolves("02-b")],
        },
    )
    manifest = execute_run(prepared, launcher)

    assert manifest.status is RunStatus.DONE
    assert launcher.max_live_interventions == 2

    records = prepared.run.intervention_records()
    assert sorted(record.node for record in records) == sorted(
        ["root", node_id("01-a"), node_id("01-a"), node_id("02-b"), node_id("02-b")]
    )
    assert all(record.ended_at is not None for record in records)
    sessions = prepared.run.session_records()
    assert len(sessions) == 3
    assert all(record.status is SessionStatus.SUCCEEDED for record in sessions)
