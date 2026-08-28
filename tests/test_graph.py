"""Graph derivation, readiness and the store: the pure heart of dispatch.

Ticket parsing, edge derivation and readiness are the highest-risk and
cheapest-to-test part of the system, so they are tested directly, against
files in a temporary repo rather than through the loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto.graph import (
    GRAPH_FILENAME,
    GraphError,
    GraphStore,
    derive,
    graph_path,
    parse_blockers,
    parse_ticket_type,
    ready,
)
from auto.model import (
    Graph,
    NodeStatus,
    NodeType,
    TaskResolutionMode,
    TicketType,
)

EFFORT = "add-search"


def a_repo_with(tmp_path: Path, tickets: dict[str, str]) -> Path:
    repo = tmp_path / "target"
    for name, body in tickets.items():
        path = repo / ".scratch" / EFFORT / "issues" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return repo


def ticket(
    *,
    type_line: str | None = None,
    blocked_by: str | None = None,
    status: str = "ready-for-agent",
) -> str:
    lines = ["# A ticket", ""]
    if type_line is not None:
        lines.append(type_line)
    if blocked_by is not None:
        lines.append(blocked_by)
    lines.append(f"**Status:** {status}")
    return "\n".join(lines) + "\n"


# --- parsing ------------------------------------------------------------------


def test_the_type_line_is_read_bare_or_bolded() -> None:
    assert parse_ticket_type("Type: research\n") is TicketType.RESEARCH
    assert parse_ticket_type("**Type:** prototype\n") is TicketType.PROTOTYPE
    assert parse_ticket_type("**Type**: grilling\n") is TicketType.GRILLING
    assert parse_ticket_type("Type: task\n") is TicketType.TASK


def test_a_ticket_with_no_type_line_is_an_implementation_ticket() -> None:
    assert parse_ticket_type("# 01: Index the content\n") is TicketType.IMPLEMENT


def test_blockers_parse_numbers_stems_filenames_and_none() -> None:
    assert parse_blockers("Blocked by: 01, 02\n") == ["01", "02"]
    assert parse_blockers("**Blocked by:** None (can start immediately)\n") == []
    assert parse_blockers("Blocked by: 01-index.md, `02-rank`\n") == [
        "01-index",
        "02-rank",
    ]
    assert parse_blockers("# just a title\n") == []


# --- derivation ---------------------------------------------------------------


def test_membership_edges_and_types_come_from_the_ticket_files(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(
        tmp_path,
        {
            "01-index.md": ticket(),
            "02-rank.md": ticket(blocked_by="**Blocked by:** 01"),
            "03-look.md": ticket(type_line="Type: research"),
        },
    )
    graph = derive(repo, EFFORT, spawned_by="root")
    assert graph.graph_id == EFFORT
    assert graph.spawned_by == "root"
    assert [n.node_id for n in graph.nodes] == ["01-index", "02-rank", "03-look"]
    assert [n.ticket_type for n in graph.nodes] == [
        TicketType.IMPLEMENT,
        TicketType.IMPLEMENT,
        TicketType.RESEARCH,
    ]
    two = graph.node("02-rank")
    assert two is not None and two.blocked_by == ["01-index"]
    assert graph.node("01-index").ticket == f".scratch/{EFFORT}/issues/01-index.md"  # type: ignore[union-attr]


def test_a_ticket_first_seen_already_resolved_is_imported_as_done(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(
        tmp_path,
        {"01-index.md": ticket(status="resolved"), "02-rank.md": ticket()},
    )
    graph = derive(repo, EFFORT, spawned_by="root")
    assert graph.node("01-index").status is NodeStatus.DONE  # type: ignore[union-attr]
    assert graph.node("02-rank").status is NodeStatus.PENDING  # type: ignore[union-attr]


def test_harness_state_survives_rederivation_and_outranks_the_status_line(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    first = derive(repo, EFFORT, spawned_by="root")
    node = first.node("01-index")
    assert node is not None
    node.status = NodeStatus.IN_PROGRESS
    node.session_id = "s-1"
    node.nudge_count = 2

    # The session writes its ticket resolved; the harness has not completed
    # the node, and the harness is authoritative.
    (repo / ".scratch" / EFFORT / "issues" / "01-index.md").write_text(
        ticket(status="resolved")
    )
    second = derive(repo, EFFORT, spawned_by="root", previous=first)
    merged = second.node("01-index")
    assert merged is not None
    assert merged.status is NodeStatus.IN_PROGRESS
    assert merged.session_id == "s-1"
    assert merged.nudge_count == 2


def test_a_ticket_written_mid_run_joins_the_graph_on_the_next_derivation(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    first = derive(repo, EFFORT, spawned_by="root")
    (repo / ".scratch" / EFFORT / "issues" / "02-rank.md").write_text(
        ticket(blocked_by="Blocked by: 01")
    )
    second = derive(repo, EFFORT, spawned_by="root", previous=first)
    assert [n.node_id for n in second.nodes] == ["01-index", "02-rank"]
    assert second.node("02-rank").status is NodeStatus.PENDING  # type: ignore[union-attr]


def test_task_classifications_survive_rederivation(tmp_path: Path) -> None:
    repo = a_repo_with(tmp_path, {"01-do.md": ticket(type_line="Type: task")})
    first = derive(repo, EFFORT, spawned_by="root")
    first.node("01-do").task_mode = TaskResolutionMode.AGENT  # type: ignore[union-attr]
    second = derive(repo, EFFORT, spawned_by="root", previous=first)
    assert second.node("01-do").task_mode is TaskResolutionMode.AGENT  # type: ignore[union-attr]


# --- readiness ----------------------------------------------------------------


def test_a_node_is_ready_when_pending_and_every_blocker_is_done(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(
        tmp_path,
        {
            "01-index.md": ticket(),
            "02-rank.md": ticket(blocked_by="Blocked by: 01"),
        },
    )
    graph = derive(repo, EFFORT, spawned_by="root")
    assert [n.node_id for n in ready(graph)] == ["01-index"]

    graph.node("01-index").status = NodeStatus.DONE  # type: ignore[union-attr]
    assert [n.node_id for n in ready(graph)] == ["02-rank"]


def test_a_failed_blocker_keeps_its_dependents_unready(tmp_path: Path) -> None:
    repo = a_repo_with(
        tmp_path,
        {
            "01-index.md": ticket(),
            "02-rank.md": ticket(blocked_by="Blocked by: 01"),
            "03-look.md": ticket(),
        },
    )
    graph = derive(repo, EFFORT, spawned_by="root")
    graph.node("01-index").status = NodeStatus.FAILED  # type: ignore[union-attr]
    assert [n.node_id for n in ready(graph)] == ["03-look"]


def test_a_blocker_naming_no_ticket_keeps_the_node_blocked(tmp_path: Path) -> None:
    repo = a_repo_with(
        tmp_path, {"02-rank.md": ticket(blocked_by="Blocked by: 01, 09")}
    )
    graph = derive(repo, EFFORT, spawned_by="root")
    assert ready(graph) == []


def test_readiness_is_derived_never_persisted(tmp_path: Path) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    store = GraphStore(repo)
    store.emit(EFFORT, spawned_by="root")
    raw = graph_path(repo, EFFORT).read_text()
    assert "ready" not in raw


# --- task resolution modes ----------------------------------------------------


def test_each_task_mode_names_its_entry_skill(tmp_path: Path) -> None:
    repo = a_repo_with(
        tmp_path,
        {
            "01-a.md": ticket(type_line="Type: task"),
            "02-b.md": ticket(type_line="Type: task"),
            "03-c.md": ticket(type_line="Type: task"),
        },
    )
    store = GraphStore(repo)
    graph = store.emit(
        EFFORT,
        spawned_by="root",
        task_modes={
            "01-a": TaskResolutionMode.AGENT,
            "02-b": TaskResolutionMode.USER,
            "03-c": TaskResolutionMode.UNDEFINED,
        },
    )
    assert graph.node("01-a").entry is NodeType.IMPLEMENT  # type: ignore[union-attr]
    assert graph.node("02-b").entry is None  # type: ignore[union-attr]
    assert graph.node("03-c").entry is NodeType.GRILL_WITH_DOCS  # type: ignore[union-attr]
    # A user task cannot be dispatched, so it is never ready.
    assert [n.node_id for n in ready(graph)] == ["01-a", "03-c"]


# --- the store ----------------------------------------------------------------


def test_emit_writes_the_graph_beside_its_tickets(tmp_path: Path) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    store = GraphStore(repo)
    store.emit(EFFORT, spawned_by="root")
    path = graph_path(repo, EFFORT)
    assert path == repo / ".scratch" / EFFORT / GRAPH_FILENAME
    persisted = Graph.model_validate_json(path.read_text())
    assert persisted.graph_id == EFFORT
    assert persisted.spawned_by == "root"
    assert [n.node_id for n in persisted.nodes] == ["01-index"]


def test_emit_refuses_an_effort_with_no_tickets(tmp_path: Path) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    store = GraphStore(repo)
    with pytest.raises(GraphError, match="no tickets"):
        store.emit(EFFORT, spawned_by="root")


def test_emit_refuses_to_leave_a_task_unclassified(tmp_path: Path) -> None:
    repo = a_repo_with(
        tmp_path,
        {"01-do.md": ticket(type_line="Type: task"), "02-x.md": ticket()},
    )
    with pytest.raises(GraphError, match="01-do"):
        GraphStore(repo).emit(EFFORT, spawned_by="root")


def test_emit_refuses_a_classification_for_a_ticket_that_is_not_a_task(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    with pytest.raises(GraphError, match="not a `task` ticket"):
        GraphStore(repo).emit(
            EFFORT,
            spawned_by="root",
            task_modes={"01-index": TaskResolutionMode.AGENT},
        )


def test_emit_refuses_a_classification_for_a_ticket_that_does_not_exist(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    with pytest.raises(GraphError, match="09-ghost"):
        GraphStore(repo).emit(
            EFFORT,
            spawned_by="root",
            task_modes={"09-ghost": TaskResolutionMode.AGENT},
        )


def test_emitting_the_same_effort_twice_is_refused(tmp_path: Path) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    store = GraphStore(repo)
    store.emit(EFFORT, spawned_by="root")
    with pytest.raises(GraphError, match="already"):
        store.emit(EFFORT, spawned_by="root")


def test_the_tick_rederives_membership_and_persists_the_result(
    tmp_path: Path,
) -> None:
    repo = a_repo_with(tmp_path, {"01-index.md": ticket()})
    store = GraphStore(repo)
    store.emit(EFFORT, spawned_by="root")
    (repo / ".scratch" / EFFORT / "issues" / "02-rank.md").write_text(ticket())
    store.tick()
    persisted = Graph.model_validate_json(graph_path(repo, EFFORT).read_text())
    assert [n.node_id for n in persisted.nodes] == ["01-index", "02-rank"]


def test_next_ready_hands_out_one_node_first_by_number(tmp_path: Path) -> None:
    repo = a_repo_with(
        tmp_path, {"01-index.md": ticket(), "02-rank.md": ticket()}
    )
    store = GraphStore(repo)
    store.emit(EFFORT, spawned_by="root")
    found = store.next_ready()
    assert found is not None
    graph, node = found
    assert node.node_id == "01-index"

    node.status = NodeStatus.DONE
    found = store.next_ready()
    assert found is not None
    assert found[1].node_id == "02-rank"


def test_all_done_only_when_every_node_in_every_graph_is(tmp_path: Path) -> None:
    repo = a_repo_with(
        tmp_path, {"01-index.md": ticket(), "02-rank.md": ticket()}
    )
    store = GraphStore(repo)
    graph = store.emit(EFFORT, spawned_by="root")
    assert store.all_done() is False
    for node in graph.nodes:
        node.status = NodeStatus.DONE
    assert store.all_done() is True
