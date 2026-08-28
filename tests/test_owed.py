"""What a node type owes, and what of it is missing from the target repo.

The cheapest part of the harness to get wrong and the cheapest to check: pure
functions over a table and a directory tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto.model import NodeType
from auto.owed import (
    OWED,
    OwedArtifact,
    baseline,
    keys,
    missing,
    render_table,
    shrank,
    spelt_out,
)


def deliver(repo: Path, path: str, body: str = "# something\n") -> Path:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body)
    return file


# --- the table ---------------------------------------------------------------


def test_every_node_type_has_an_owed_artifact_table() -> None:
    assert set(OWED) == set(NodeType)
    assert all(artifacts for artifacts in OWED.values())


def test_each_artifact_is_named_once_within_a_node_type() -> None:
    for node_type, artifacts in OWED.items():
        names = [artifact.key for artifact in artifacts]
        assert len(names) == len(set(names)), node_type


def test_the_table_renders_for_the_agents_prompt_in_the_repos_own_paths() -> None:
    rendered = render_table()
    for node_type in NodeType:
        assert node_type.skill_invocation in rendered
    assert ".scratch/<effort>/issues/" in rendered
    assert "map.md" in rendered


# --- what is missing ---------------------------------------------------------


def test_a_charting_session_owes_a_map_and_tickets(tmp_path: Path) -> None:
    assert keys(missing(NodeType.WAYFINDER, tmp_path)) == ["map", "tickets"]

    deliver(tmp_path, ".scratch/add-search/map.md")
    assert keys(missing(NodeType.WAYFINDER, tmp_path)) == ["tickets"]

    deliver(tmp_path, ".scratch/add-search/issues/01-index.md")
    assert missing(NodeType.WAYFINDER, tmp_path) == ()


def test_a_grilling_session_owes_a_spec_and_tickets(tmp_path: Path) -> None:
    assert keys(missing(NodeType.GRILL_WITH_DOCS, tmp_path)) == ["spec", "tickets"]

    deliver(tmp_path, ".scratch/add-search/spec.md")
    deliver(tmp_path, ".scratch/add-search/issues/01-index.md")
    assert missing(NodeType.GRILL_WITH_DOCS, tmp_path) == ()


def test_an_earlier_efforts_leftovers_do_not_satisfy_a_fresh_node(
    tmp_path: Path,
) -> None:
    """Delivery since dispatch: what predates the node cannot be its work."""
    deliver(tmp_path, ".scratch/older-effort/map.md")
    deliver(tmp_path, ".scratch/older-effort/issues/01-x.md")
    since = baseline(NodeType.WAYFINDER, tmp_path)

    assert keys(missing(NodeType.WAYFINDER, tmp_path, since=since)) == [
        "map",
        "tickets",
    ]

    deliver(tmp_path, ".scratch/add-search/map.md")
    deliver(tmp_path, ".scratch/add-search/issues/01-index.md")
    assert missing(NodeType.WAYFINDER, tmp_path, since=since) == ()


def test_a_rerun_that_rewrites_the_same_effort_directory_still_delivers(
    tmp_path: Path,
) -> None:
    """The baseline is a fingerprint, not a path set: a rewrite counts."""
    import os

    map_file = deliver(tmp_path, ".scratch/add-search/map.md", "old\n")
    deliver(tmp_path, ".scratch/add-search/issues/01-x.md", "old\n")
    since = baseline(NodeType.WAYFINDER, tmp_path)
    assert keys(missing(NodeType.WAYFINDER, tmp_path, since=since)) == [
        "map",
        "tickets",
    ]

    map_file.write_text("# rewritten\n")
    os.utime(map_file, ns=(0, map_file.stat().st_mtime_ns + 1))
    assert keys(missing(NodeType.WAYFINDER, tmp_path, since=since)) == ["tickets"]


def test_without_a_baseline_whatever_is_on_disk_counts(tmp_path: Path) -> None:
    deliver(tmp_path, ".scratch/any/map.md")
    deliver(tmp_path, ".scratch/any/issues/01-x.md")
    assert missing(NodeType.WAYFINDER, tmp_path) == ()


def test_the_nodes_own_ticket_is_judged_on_its_edits_not_on_being_new(
    tmp_path: Path,
) -> None:
    """The ticket exists before dispatch by design; content is the check."""
    ticket = ".scratch/add-search/issues/01-index.md"
    deliver(tmp_path, ticket, "Status: ready-for-agent\n")
    since = baseline(NodeType.IMPLEMENT, tmp_path)

    assert keys(
        missing(NodeType.IMPLEMENT, tmp_path, ticket=ticket, since=since)
    ) == ["resolved"]

    deliver(tmp_path, ticket, "Status: done\n")
    assert missing(NodeType.IMPLEMENT, tmp_path, ticket=ticket, since=since) == ()


def test_an_implementation_session_owes_its_own_ticket_resolved(
    tmp_path: Path,
) -> None:
    ticket = ".scratch/add-search/issues/01-index.md"
    deliver(tmp_path, ticket, "# 01: Index\n\n**Status:** ready-for-agent\n")
    assert keys(missing(NodeType.IMPLEMENT, tmp_path, ticket=ticket)) == ["resolved"]

    deliver(tmp_path, ticket, "# 01: Index\n\nStatus: resolved\n")
    assert missing(NodeType.IMPLEMENT, tmp_path, ticket=ticket) == ()


def test_the_status_line_is_read_in_the_shapes_skills_actually_write_it(
    tmp_path: Path,
) -> None:
    ticket = ".scratch/add-search/issues/01-index.md"
    for body in (
        "**Status:** resolved\n",
        "Status: resolved\n",
        "status:  Resolved\n",
        # An implementation ticket's terminal word is the skill's own choice.
        "Status: done\n",
        "**Status:** completed\n",
    ):
        deliver(tmp_path, ticket, body)
        assert missing(NodeType.IMPLEMENT, tmp_path, ticket=ticket) == ()


def test_a_research_session_owes_its_answer_as_well_as_the_status_line(
    tmp_path: Path,
) -> None:
    ticket = ".scratch/add-search/issues/02-research.md"
    deliver(tmp_path, ticket, "# 02\n\nStatus: claimed\n")
    assert keys(missing(NodeType.RESEARCH, tmp_path, ticket=ticket)) == [
        "answer",
        "resolved",
    ]

    deliver(tmp_path, ticket, "# 02\n\nStatus: claimed\n\n## Answer\n\nPostgres.\n")
    assert keys(missing(NodeType.RESEARCH, tmp_path, ticket=ticket)) == ["resolved"]


def test_a_prototype_session_owes_the_same_as_a_research_one(tmp_path: Path) -> None:
    ticket = ".scratch/add-search/issues/03-proto.md"
    deliver(tmp_path, ticket, "# 03\n\n## Answer\n\nVariant E.\n\nStatus: resolved\n")
    assert missing(NodeType.PROTOTYPE, tmp_path, ticket=ticket) == ()


def test_an_obligation_that_cannot_be_checked_is_not_one_the_harness_holds(
    tmp_path: Path,
) -> None:
    """A ticket-shaped obligation on a node that has no ticket: the root."""
    assert missing(NodeType.IMPLEMENT, tmp_path, ticket=None) == ()


def test_a_ticket_the_session_deleted_is_missing_rather_than_delivered(
    tmp_path: Path,
) -> None:
    ticket = ".scratch/add-search/issues/01-index.md"
    assert keys(missing(NodeType.IMPLEMENT, tmp_path, ticket=ticket)) == ["resolved"]


# --- shrinking ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ({"map", "tickets"}, {"tickets"}, True),
        ({"map", "tickets"}, set(), True),
        ({"map", "tickets"}, {"map", "tickets"}, False),
        (set(), set(), False),
        ({"tickets"}, {"map", "tickets"}, False),
        ({"map"}, {"tickets"}, False),
    ],
)
def test_the_owed_set_shrinks_only_when_something_left_it(
    previous: set[str], current: set[str], expected: bool
) -> None:
    assert shrank(previous, current) is expected


# --- how it reads ------------------------------------------------------------


def test_missing_artifacts_are_spelt_out_for_the_agent_that_asked() -> None:
    spelt = spelt_out(OWED[NodeType.WAYFINDER])
    assert "map" in spelt
    assert ".scratch/<effort>/issues/" in spelt


def test_an_artifact_reads_as_the_tracker_doc_describes_it() -> None:
    artifact = OwedArtifact(key="map", owes="the map", within=".scratch/*/map.md")
    assert spelt_out((artifact,)) == "the map"
