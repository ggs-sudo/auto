"""The run directory is the single-writer boundary: nothing outside `auto.run`
touches these files."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto.errors import UsageError
from auto.model import (
    Manifest,
    NodeType,
    ResolvedConfig,
    RootNode,
    Route,
    RunStatus,
    SessionRecord,
)
from auto.run import RunDirectory, allocate_run_id, list_runs, load_run, slugify

CREATED_AT = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def a_manifest(run_id: str = "20260828-120000-add-search") -> Manifest:
    return Manifest(
        run_id=run_id,
        route=Route.WAYFINDER,
        prompt="Add search to the settings page.",
        target_repo="/tmp/target",
        branch="main",
        config=ResolvedConfig(),
        created_at=CREATED_AT,
        root_node=RootNode(
            type=NodeType.WAYFINDER, prompt="Add search to the settings page."
        ),
    )


def a_session_record(session_id: str = "s-1") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        node="root",
        role=NodeType.WAYFINDER,
        started_at=CREATED_AT,
        captured_transcript=f"transcripts/{session_id}.jsonl",
    )


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Add search to the settings page.", "add-search-to-the-settings"),
        ("  Wire up OAuth (Google) ", "wire-up-oauth-google"),
        ("Ship it!", "ship-it"),
        ("", "run"),
        ("!!!", "run"),
        ("supercalifragilisticexpialidocious", "supercalifragilisticexpialido"),
    ],
)
def test_a_slug_is_readable_and_bounded(prompt: str, expected: str) -> None:
    assert slugify(prompt) == expected


def test_a_run_id_is_the_timestamp_and_the_slug(state_dir: Path) -> None:
    assert (
        allocate_run_id(state_dir, CREATED_AT, "Add search")
        == "20260828-120000-add-search"
    )


def test_a_colliding_run_id_gets_a_suffix(state_dir: Path) -> None:
    RunDirectory.create(
        state_dir, a_manifest("20260828-120000-add-search-to-the-settings")
    )
    assert (
        allocate_run_id(state_dir, CREATED_AT, "Add search to the settings page.")
        == "20260828-120000-add-search-to-the-settings-2"
    )


def test_creating_a_run_lays_out_its_directory(state_dir: Path) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    assert run.path == state_dir / "runs" / "20260828-120000-add-search"
    assert run.manifest_path.is_file()
    assert run.sessions_dir.is_dir()
    assert run.transcripts_dir.is_dir()


def test_a_manifest_round_trips_through_the_run_directory(state_dir: Path) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    assert run.read_manifest() == a_manifest()


def test_writing_a_manifest_replaces_the_previous_one(state_dir: Path) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    manifest = run.read_manifest()
    manifest.status = RunStatus.DONE
    run.write_manifest(manifest)
    assert run.read_manifest().status is RunStatus.DONE


def test_a_session_record_round_trips(state_dir: Path) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    run.write_session(a_session_record())
    assert run.read_session("s-1") == a_session_record()
    assert run.session_records() == [a_session_record()]


def test_session_records_are_listed_in_a_stable_order(state_dir: Path) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    for session_id in ("s-3", "s-1", "s-2"):
        run.write_session(a_session_record(session_id))
    assert [r.session_id for r in run.session_records()] == ["s-1", "s-2", "s-3"]


def test_a_transcript_captures_one_json_line_per_event(state_dir: Path) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    with run.open_transcript("s-1") as transcript:
        transcript.write({"type": "system", "subtype": "init"})
        transcript.write({"type": "result", "subtype": "success"})
    lines = run.transcript_path("s-1").read_text().splitlines()
    assert lines == [
        '{"type": "system", "subtype": "init"}',
        '{"type": "result", "subtype": "success"}',
    ]


def test_a_transcript_is_readable_while_it_is_still_being_written(
    state_dir: Path,
) -> None:
    run = RunDirectory.create(state_dir, a_manifest())
    with run.open_transcript("s-1") as transcript:
        transcript.write({"type": "system"})
        assert run.transcript_path("s-1").read_text() == '{"type": "system"}\n'


def test_runs_are_listed_newest_first(state_dir: Path) -> None:
    RunDirectory.create(state_dir, a_manifest("20260828-120000-first"))
    RunDirectory.create(state_dir, a_manifest("20260829-090000-second"))
    assert [m.run_id for m in list_runs(state_dir)] == [
        "20260829-090000-second",
        "20260828-120000-first",
    ]


def test_listing_runs_with_no_state_dir_is_empty_not_an_error(
    state_dir: Path,
) -> None:
    assert list_runs(state_dir) == []


def test_loading_an_unknown_run_is_a_usage_error(state_dir: Path) -> None:
    with pytest.raises(UsageError, match="no such run"):
        load_run(state_dir, "20260828-120000-nope")


def test_a_run_that_cannot_be_read_does_not_hide_the_others(
    state_dir: Path,
) -> None:
    RunDirectory.create(state_dir, a_manifest("20260828-120000-good"))
    broken = state_dir / "runs" / "20260829-090000-broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{not json")
    assert [m.run_id for m in list_runs(state_dir)] == ["20260828-120000-good"]
