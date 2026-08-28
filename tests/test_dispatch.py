"""From an emitted graph to an exhausted one, driven for real above the seam.

The root node charts tickets, its agent emits the graph, and the loop then
dispatches ready nodes one at a time — each driven, judged and recorded
exactly as the root was. Everything below the launcher seam is real: the
derivation from tracker files, the MCP tools, every write.
"""

from __future__ import annotations

from pathlib import Path

from auto.model import (
    Graph,
    NodeStatus,
    NodeType,
    RunStatus,
    TicketType,
)
from auto.orchestrate import execute_run, prepare_run
from tests.agents import (
    HarnessLauncher,
    ScriptedAgent,
    completes,
    emits_and_completes,
    fails,
    tries_to_complete,
)
from tests.conftest import MAP_BODY, one_turn
from tests.test_orchestrate import a_request

EFFORT = "add-search"


def charted(*tickets: tuple[str, str]) -> dict[str, str]:
    """What the root session leaves behind: a map, and the given tickets."""
    files = {f".scratch/{EFFORT}/map.md": MAP_BODY}
    for name, body in tickets:
        files[f".scratch/{EFFORT}/issues/{name}"] = body
    return files


def ticket_body(
    *,
    type_line: str | None = None,
    blocked_by: str = "None (can start immediately)",
    status: str = "ready-for-agent",
) -> str:
    lines = ["# A ticket", ""]
    if type_line is not None:
        lines.append(f"**Type:** {type_line}")
    lines += [f"**Blocked by:** {blocked_by}", f"**Status:** {status}"]
    return "\n".join(lines) + "\n"


RESOLVED = ticket_body(status="resolved")


def node_id(stem: str) -> str:
    return f"{EFFORT}/{stem}"


def ticket_path(stem: str) -> str:
    return f".scratch/{EFFORT}/issues/{stem}.md"


def resolves(stem: str) -> dict[str, str]:
    """What an implementation session leaves behind: its ticket closed out."""
    return {ticket_path(stem): RESOLVED}


def test_the_run_dispatches_the_charted_ticket_and_finishes(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir, prompt="Add search."))
    launcher = HarnessLauncher(
        {
            "root": one_turn("Charted."),
            node_id("01-index"): one_turn("Implemented."),
        },
        (emits_and_completes(EFFORT), completes("Indexed the settings.")),
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [resolves("01-index")],
        },
    )
    manifest = execute_run(prepared, launcher)

    assert [spec.message for spec in launcher.launched] == [
        "/wayfinder Add search.",
        f"/implement {ticket_path('01-index')}",
    ]
    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.status is NodeStatus.DONE
    assert manifest.root_node.graph == EFFORT

    # The graph lives beside its tickets, named by the effort directory.
    persisted = Graph.model_validate_json(
        (target_repo / ".scratch" / EFFORT / "graph.json").read_text()
    )
    assert persisted.graph_id == EFFORT
    assert persisted.spawned_by == "root"
    node = persisted.node("01-index")
    assert node is not None
    assert node.status is NodeStatus.DONE
    assert node.ticket_type is TicketType.IMPLEMENT

    # The node's session is recorded like any other, joined by session id.
    records = {record.node: record for record in prepared.run.session_records()}
    record = records[node_id("01-index")]
    assert record.session_id == node.session_id
    assert record.role is NodeType.IMPLEMENT
    assert record.ticket == ticket_path("01-index")
    assert record.summary == "Indexed the settings."


def test_the_graph_is_kept_out_of_version_control(
    target_repo: Path, state_dir: Path
) -> None:
    """`.scratch/` is gitignored by preflight's demand; the graph sits inside."""
    import subprocess

    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": one_turn(), node_id("01-index"): one_turn()},
        (emits_and_completes(EFFORT), completes()),
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [resolves("01-index")],
        },
    )
    execute_run(prepared, launcher)
    tracked = subprocess.run(
        ["git", "-C", str(target_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".scratch" not in tracked


def test_a_blocked_ticket_waits_for_its_blocker_one_at_a_time(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-index"): one_turn(),
            node_id("02-rank"): one_turn(),
        },
        (emits_and_completes(EFFORT), completes(), completes()),
        writes={
            "root": [
                charted(
                    ("01-index.md", ticket_body()),
                    ("02-rank.md", ticket_body(blocked_by="01")),
                )
            ],
            node_id("01-index"): [resolves("01-index")],
            node_id("02-rank"): [resolves("02-rank")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("01-index"),
        node_id("02-rank"),
    ]
    assert manifest.status is RunStatus.DONE


def test_a_ticket_first_seen_already_resolved_is_imported_as_done(
    target_repo: Path, state_dir: Path
) -> None:
    """Harness node status is authoritative for dispatch, from the start."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": one_turn(), node_id("02-rank"): one_turn()},
        (emits_and_completes(EFFORT), completes()),
        writes={
            "root": [
                charted(
                    ("01-index.md", ticket_body(status="resolved")),
                    ("02-rank.md", ticket_body(blocked_by="01")),
                )
            ],
            node_id("02-rank"): [resolves("02-rank")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("02-rank"),
    ]
    assert manifest.status is RunStatus.DONE
    persisted = Graph.model_validate_json(
        (target_repo / ".scratch" / EFFORT / "graph.json").read_text()
    )
    imported = persisted.node("01-index")
    assert imported is not None
    assert imported.status is NodeStatus.DONE
    assert imported.session_id is None


def test_a_typed_ticket_enters_through_its_own_skill(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    answered = (
        "# A ticket\n\n**Type:** research\n\n**Status:** resolved\n\n"
        "## Answer\n\nUse the built-in index.\n"
    )
    launcher = HarnessLauncher(
        {"root": one_turn(), node_id("01-look"): one_turn()},
        (emits_and_completes(EFFORT), completes()),
        writes={
            "root": [
                charted(("01-look.md", ticket_body(type_line="research")))
            ],
            node_id("01-look"): [{ticket_path("01-look"): answered}],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert launcher.launched[1].message == f"/research {ticket_path('01-look')}"
    assert manifest.status is RunStatus.DONE


def test_an_undefined_task_is_resolved_by_a_grilling_session_in_its_place(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    speccing = {
        ".scratch/settle-scope/spec.md": "## Problem Statement\n\nUnclear.\n",
        ".scratch/settle-scope/issues/01-decide.md": ticket_body(),
    }
    launcher = HarnessLauncher(
        {"root": one_turn(), node_id("01-settle"): one_turn()},
        (
            emits_and_completes(
                EFFORT, tasks=[{"ticket": "01-settle", "mode": "undefined"}]
            ),
            completes(),
        ),
        writes={
            "root": [
                charted(("01-settle.md", ticket_body(type_line="task")))
            ],
            node_id("01-settle"): [speccing],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert launcher.launched[1].message == (
        f"/grill-with-docs {ticket_path('01-settle')}"
    )
    assert manifest.status is RunStatus.DONE


def test_a_user_task_is_never_dispatched_and_leaves_the_run_failed(
    target_repo: Path, state_dir: Path
) -> None:
    """Until gates exist, work only a human can do has no way to happen."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": one_turn(), node_id("02-index"): one_turn()},
        (
            emits_and_completes(
                EFFORT, tasks=[{"ticket": "01-keys", "mode": "user"}]
            ),
            completes(),
        ),
        writes={
            "root": [
                charted(
                    ("01-keys.md", ticket_body(type_line="task")),
                    ("02-index.md", ticket_body()),
                )
            ],
            node_id("02-index"): [resolves("02-index")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("02-index"),
    ]
    assert manifest.status is RunStatus.FAILED
    persisted = Graph.model_validate_json(
        (target_repo / ".scratch" / EFFORT / "graph.json").read_text()
    )
    assert persisted.node("01-keys").status is NodeStatus.PENDING  # type: ignore[union-attr]


def test_a_failed_node_blocks_only_what_depended_on_it(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-index"): one_turn(),
            node_id("03-facets"): one_turn(),
        },
        (
            emits_and_completes(EFFORT),
            fails("the index library does not build on this platform"),
            completes(),
        ),
        writes={
            "root": [
                charted(
                    ("01-index.md", ticket_body()),
                    ("02-rank.md", ticket_body(blocked_by="01")),
                    ("03-facets.md", ticket_body()),
                )
            ],
            node_id("03-facets"): [resolves("03-facets")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("01-index"),
        node_id("03-facets"),
    ]
    assert manifest.status is RunStatus.FAILED
    persisted = Graph.model_validate_json(
        (target_repo / ".scratch" / EFFORT / "graph.json").read_text()
    )
    assert persisted.node("01-index").status is NodeStatus.FAILED  # type: ignore[union-attr]
    assert persisted.node("02-rank").status is NodeStatus.PENDING  # type: ignore[union-attr]
    assert persisted.node("03-facets").status is NodeStatus.DONE  # type: ignore[union-attr]


def test_a_ticket_written_mid_run_joins_the_graph_on_the_next_tick(
    target_repo: Path, state_dir: Path
) -> None:
    """Nothing restarts: the tick re-derives membership from the files."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-index"): one_turn(),
            node_id("02-extra"): one_turn(),
        },
        (emits_and_completes(EFFORT), completes(), completes()),
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            # The first session resolves its own ticket and writes a new one.
            node_id("01-index"): [
                {**resolves("01-index"), ticket_path("02-extra"): ticket_body()}
            ],
            node_id("02-extra"): [resolves("02-extra")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert [spec.message for spec in launcher.launched][1:] == [
        f"/implement {ticket_path('01-index')}",
        f"/implement {ticket_path('02-extra')}",
    ]
    assert manifest.status is RunStatus.DONE


def test_a_graph_node_is_nudged_and_completed_like_the_root(
    target_repo: Path, state_dir: Path
) -> None:
    """Owed artifacts hold per node: an implement node owes its ticket closed."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-index"): [*one_turn("Done?"), *one_turn("Done.")],
        },
        (
            emits_and_completes(EFFORT),
            tries_to_complete(then="Update the ticket's status line, please."),
            completes(),
        ),
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [{}, resolves("01-index")],
        },
    )
    manifest = execute_run(prepared, launcher)
    assert manifest.status is RunStatus.DONE
    refusal = launcher.tool_results[2]
    assert refusal["isError"] is True
    assert "Status:" in refusal["content"][0]["text"]
    assert launcher.sent == [
        (node_id("01-index"), "Update the ticket's status line, please.")
    ]


def test_emit_graph_is_refused_for_a_node_that_spawns_none(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": one_turn(), node_id("01-index"): one_turn()},
        (
            emits_and_completes(EFFORT),
            ScriptedAgent(
                calls=[
                    ("emit_graph", {"effort": EFFORT}),
                    ("complete_node", {"summary": "Implemented."}),
                ]
            ),
        ),
        writes={
            "root": [charted(("01-index.md", ticket_body()))],
            node_id("01-index"): [resolves("01-index")],
        },
    )
    manifest = execute_run(prepared, launcher)
    refused = launcher.tool_results[2]
    assert refused["isError"] is True
    assert "spawns no graph" in refused["content"][0]["text"]
    assert manifest.status is RunStatus.DONE


def test_an_emit_that_leaves_a_task_unclassified_is_refused(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": [*one_turn("Charted."), *one_turn("Classified.")]},
        (
            ScriptedAgent(
                calls=[
                    ("emit_graph", {"effort": EFFORT}),
                    ("send_to_session", {"message": "One more look."}),
                ]
            ),
            emits_and_completes(
                EFFORT, tasks=[{"ticket": "01-keys", "mode": "user"}]
            ),
        ),
        writes={
            "root": [charted(("01-keys.md", ticket_body(type_line="task")))]
        },
    )
    execute_run(prepared, launcher)
    refused = launcher.tool_results[0]
    assert refused["isError"] is True
    assert "01-keys" in refused["content"][0]["text"]
    accepted = launcher.tool_results[2]
    assert accepted["isError"] is False


def test_a_root_completed_without_a_graph_still_ends_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    """No node left to dispatch is terminal, even when nothing was emitted."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": one_turn()},
        (completes(),),
        writes={"root": [charted(("01-index.md", ticket_body()))]},
    )
    manifest = execute_run(prepared, launcher)
    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.graph is None
    assert len(launcher.launched) == 1
