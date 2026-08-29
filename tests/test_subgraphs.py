"""A node that spawns more tickets, and the run waiting on the whole subtree.

The exact shape #14 exists for: a validation ticket blocked on a grilling
node must not dispatch the moment that node's session ends — the work it
would validate is still unwritten, in the subgraph the grilling session
charted. Everything below the launcher seam is real: derivation, readiness
across every graph, the MCP tools, every persisted file.
"""

from __future__ import annotations

from pathlib import Path

from auto.model import Graph, NodeStatus, RunStatus
from auto.orchestrate import PreparedRun, execute_run, prepare_run
from tests.agents import (
    HarnessLauncher,
    ScriptedAgent,
    completes,
    emits_and_completes,
    fails,
)
from tests.conftest import SPEC_BODY, one_turn
from tests.test_dispatch import (
    EFFORT,
    charted,
    node_id,
    resolves,
    ticket_body,
)
from tests.test_orchestrate import a_request

SUB_EFFORT = "checkout"


def specced(*tickets: tuple[str, str]) -> dict[str, str]:
    """What a grilling session leaves behind: a spec, and tickets beside it."""
    files = {f".scratch/{SUB_EFFORT}/spec.md": SPEC_BODY}
    for name, body in tickets:
        files[f".scratch/{SUB_EFFORT}/issues/{name}"] = body
    return files


def sub_resolves(stem: str) -> dict[str, str]:
    return {
        f".scratch/{SUB_EFFORT}/issues/{stem}.md": ticket_body(status="resolved")
    }


def persisted(target_repo: Path, effort: str) -> Graph:
    return Graph.model_validate_json(
        (target_repo / ".scratch" / effort / "graph.json").read_text()
    )


def a_spawning_run(
    target_repo: Path,
    state_dir: Path,
    *,
    sub_node_agent: ScriptedAgent,
    sub_node_writes: dict[str, str],
) -> tuple[PreparedRun, HarnessLauncher]:
    """A run whose root charts a grilling ticket and a validation ticket
    blocked on it; the grilling node spawns the `checkout` subgraph."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {
            "root": one_turn(),
            node_id("01-plan"): one_turn("Specced."),
            f"{SUB_EFFORT}/01-impl": one_turn("Implemented."),
            node_id("02-validate"): one_turn("Validated."),
        },
        {
            "root": [emits_and_completes(EFFORT)],
            node_id("01-plan"): [emits_and_completes(SUB_EFFORT)],
            f"{SUB_EFFORT}/01-impl": [sub_node_agent],
            node_id("02-validate"): [completes("Validated the checkout work.")],
        },
        writes={
            "root": [
                charted(
                    ("01-plan.md", ticket_body(type_line="grilling")),
                    ("02-validate.md", ticket_body(blocked_by="01")),
                )
            ],
            node_id("01-plan"): [specced(("01-impl.md", ticket_body()))],
            f"{SUB_EFFORT}/01-impl": [sub_node_writes],
            node_id("02-validate"): [resolves("02-validate")],
        },
    )
    return prepared, launcher


def test_a_dependent_dispatches_only_once_the_spawned_subtree_is_complete(
    target_repo: Path, state_dir: Path
) -> None:
    prepared, launcher = a_spawning_run(
        target_repo,
        state_dir,
        sub_node_agent=completes("Implemented checkout."),
        sub_node_writes=sub_resolves("01-impl"),
    )
    manifest = execute_run(prepared, launcher)

    # The subgraph's node ran between the spawning node and its dependent:
    # 02-validate was not ready the moment 01-plan's session ended.
    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("01-plan"),
        f"{SUB_EFFORT}/01-impl",
        node_id("02-validate"),
    ]
    assert launcher.launched[2].message == (
        f"/implement .scratch/{SUB_EFFORT}/issues/01-impl.md"
    )
    assert manifest.status is RunStatus.DONE

    # The subgraph lives beside its tickets; the linkage is the spawning
    # node's pointer to that directory, and the directory's name is the id.
    root_graph = persisted(target_repo, EFFORT)
    plan = root_graph.node("01-plan")
    assert plan is not None
    assert plan.status is NodeStatus.DONE
    assert plan.graph == SUB_EFFORT
    sub = persisted(target_repo, SUB_EFFORT)
    assert sub.spawned_by == node_id("01-plan")
    assert sub.node("01-impl").status is NodeStatus.DONE  # type: ignore[union-attr]


def test_a_failure_in_the_subgraph_keeps_the_dependent_from_ever_dispatching(
    target_repo: Path, state_dir: Path
) -> None:
    """The spawning node itself completed, but its subtree did not — so the
    validation node never runs, and the run is not done."""
    prepared, launcher = a_spawning_run(
        target_repo,
        state_dir,
        sub_node_agent=fails("the checkout work cannot be done"),
        sub_node_writes={},
    )
    manifest = execute_run(prepared, launcher)

    assert [spec.node_id for spec in launcher.launched] == [
        "root",
        node_id("01-plan"),
        f"{SUB_EFFORT}/01-impl",
    ]
    assert manifest.status is RunStatus.FAILED
    root_graph = persisted(target_repo, EFFORT)
    assert root_graph.node("01-plan").status is NodeStatus.DONE  # type: ignore[union-attr]
    assert root_graph.node("02-validate").status is NodeStatus.PENDING  # type: ignore[union-attr]
    assert persisted(target_repo, SUB_EFFORT).node("01-impl").status is (  # type: ignore[union-attr]
        NodeStatus.FAILED
    )
