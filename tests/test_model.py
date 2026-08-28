"""The models are the source of truth; these tests hold `schemas/` to them."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from auto.model import (
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
