"""The models are the source of truth; these tests hold `schemas/` to them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from auto.model import (
    DECISIONS_FOR,
    Gate,
    GateDecision,
    GateKind,
    GateResponse,
    Graph,
    GraphNode,
    Manifest,
    NodeStatus,
    NodeType,
    ResolvedConfig,
    RootNode,
    Route,
    RunStatus,
    SessionRecord,
    SessionStatus,
    Telemetry,
)
from auto.model import TaskResolutionMode, TicketType
from auto.model.export import SCHEMAS, rendered, stale, write


def a_manifest() -> Manifest:
    return Manifest(
        run_id="20260828-120000-add-search",
        route=Route.WAYFINDER,
        prompt="Add search to the settings page.\n",
        target_repo="/tmp/target",
        worktree="/tmp/target",
        branch="main",
        head="abc123",
        dirty=True,
        config=ResolvedConfig(),
        created_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        root_node=RootNode(
            type=NodeType.WAYFINDER,
            prompt="Add search to the settings page.\n",
            status=NodeStatus.IN_PROGRESS,
            session_id="1e0e0d3c-0000-4000-8000-000000000000",
        ),
    )


def a_session_record() -> SessionRecord:
    return SessionRecord(
        session_id="1e0e0d3c-0000-4000-8000-000000000000",
        node="root",
        role=NodeType.WAYFINDER,
        started_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        captured_transcript="transcripts/1e0e0d3c-0000-4000-8000-000000000000.jsonl",
        telemetry=Telemetry(cost_usd=0.42, num_turns=3),
    )


def test_manifest_round_trips_through_json() -> None:
    original = a_manifest()
    assert Manifest.model_validate_json(original.model_dump_json()) == original


def test_session_record_round_trips_through_json() -> None:
    original = a_session_record()
    assert (
        SessionRecord.model_validate_json(original.model_dump_json()) == original
    )


def test_manifest_defaults_leave_a_run_running_and_unspent() -> None:
    manifest = a_manifest()
    assert manifest.status is RunStatus.RUNNING
    assert manifest.ended_at is None
    assert manifest.driven_spend_usd == 0.0
    assert manifest.orchestrator_spend_usd == 0.0


def test_session_record_starts_running_with_no_highlights() -> None:
    record = a_session_record()
    assert record.status is SessionStatus.RUNNING
    assert record.highlights == []


def a_graph() -> Graph:
    return Graph(
        graph_id="add-search",
        spawned_by="root",
        nodes=[
            GraphNode(
                node_id="01-index",
                ticket=".scratch/add-search/issues/01-index.md",
                ticket_type=TicketType.IMPLEMENT,
            ),
            GraphNode(
                node_id="02-keys",
                ticket=".scratch/add-search/issues/02-keys.md",
                ticket_type=TicketType.TASK,
                task_mode=TaskResolutionMode.USER,
                blocked_by=["01-index"],
            ),
        ],
    )


def test_a_graph_round_trips_through_json() -> None:
    original = a_graph()
    assert Graph.model_validate_json(original.model_dump_json()) == original


def test_a_graph_node_knows_its_entry_skill() -> None:
    graph = a_graph()
    assert graph.node("01-index").entry is NodeType.IMPLEMENT  # type: ignore[union-attr]
    # A user task dispatches like agent work; its session hits the human-only
    # wall and waits at the task-completion gate.
    assert graph.node("02-keys").entry is NodeType.IMPLEMENT  # type: ignore[union-attr]


def a_gate() -> Gate:
    return Gate(
        gate_id="0001-add-search-02-proto",
        sequence=1,
        kind=GateKind.PROTOTYPE_REVIEW,
        node="add-search/02-proto",
        question="Does the settings-search prototype feel right?",
        artifact=".scratch/add-search/prototype/index.html",
        raised_at=datetime(2026, 8, 28, 12, 30, tzinfo=UTC),
    )


def test_a_gate_round_trips_through_json() -> None:
    original = a_gate()
    assert Gate.model_validate_json(original.model_dump_json()) == original


def test_a_gate_starts_unanswered_and_need_not_point_at_an_artifact() -> None:
    gate = a_gate()
    assert gate.answered_at is None
    assert (
        Gate(
            gate_id="0002-root",
            sequence=2,
            kind=GateKind.ESCALATED_QUESTION,
            node="root",
            question="Which vendor?",
            raised_at=datetime(2026, 8, 28, 12, 31, tzinfo=UTC),
        ).artifact
        is None
    )


def test_all_four_gate_kinds_are_in_the_schema() -> None:
    """Every kind a run can wait on is vocabulary, with kind-specific verbs —
    the website builds one gate shell against the schema, not the loop."""
    assert {kind.value for kind in GateKind} == {
        "prototype-review",
        "task-completion",
        "escalated-question",
        "user-ping",
    }
    assert {kind: sorted(d.value for d in DECISIONS_FOR[kind]) for kind in GateKind} == {
        GateKind.PROTOTYPE_REVIEW: ["approve", "revise"],
        GateKind.TASK_COMPLETION: ["cannot", "done"],
        GateKind.ESCALATED_QUESTION: ["answer"],
        GateKind.USER_PING: ["dismiss"],
    }


def test_a_response_is_a_decision_plus_free_text_the_harness_never_parses() -> None:
    response = GateResponse.model_validate_json(
        '{"decision": "revise", "text": "combine A and C"}'
    )
    assert response.decision is GateDecision.REVISE
    assert response.text == "combine A and C"
    # Approving needs no words at all.
    assert GateResponse(decision=GateDecision.APPROVE).text == ""


def test_a_route_names_its_entry_skill() -> None:
    assert Route.GRILL.entry_skill is NodeType.GRILL_WITH_DOCS
    assert Route.WAYFINDER.entry_skill is NodeType.WAYFINDER


def test_a_node_type_invokes_its_skill_by_slash_command() -> None:
    assert NodeType.GRILL_WITH_DOCS.skill_invocation == "/grill-with-docs"


def test_checked_in_schemas_match_the_models(repo_root: Path) -> None:
    outdated = stale(repo_root / "schemas")
    assert not outdated, (
        f"stale schemas: {outdated}. Run: uv run python -m auto.model.export"
    )


def test_every_model_gets_a_schema_file(repo_root: Path) -> None:
    on_disk = {path.name for path in (repo_root / "schemas").glob("*.schema.json")}
    assert on_disk == set(SCHEMAS)


def test_generated_schemas_are_valid_json_and_forbid_extra_properties(
    tmp_path: Path,
) -> None:
    write(tmp_path)
    for name in rendered():
        schema = json.loads((tmp_path / name).read_text())
        assert schema["additionalProperties"] is False
        assert schema["type"] == "object"
