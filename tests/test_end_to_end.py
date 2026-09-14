"""A whole run, with nothing above the process boundary faked.

Two real subprocesses are launched by the real launcher — the driven session
and the ephemeral agent that judges it — and the agent reaches the harness
tools the way the real one will: an inline strict MCP config, a URL scoped to
this run and this node, JSON-RPC over HTTP into the loop process. Only the
binary is a stand-in, and only because a live `claude` costs money.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from auto.model import NodeStatus, Route, RunStatus
from auto.orchestrate import (
    NO_ACTION_NOTE,
    NUDGE_BUDGET,
    RunRequest,
    execute_run,
    prepare_run,
)
from auto.session.cli_launcher import ClaudeCliLauncher
from tests.conftest import CHARTED

FAKE_CLAUDE = Path(__file__).parent / "stubs" / "fake_claude.py"

Call = tuple[str, dict[str, Any]]


def a_run(target_repo: Path, state_dir: Path) -> RunRequest:
    return RunRequest(
        route=Route.WAYFINDER,
        prompt="Add search to the settings page.",
        target_repo=target_repo,
        state_dir=state_dir,
    )


def launcher() -> ClaudeCliLauncher:
    return ClaudeCliLauncher(executable=[sys.executable, str(FAKE_CLAUDE)])


def charts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The driven session writes the map and ticket a wayfinder node owes."""
    writes = tmp_path / "writes.json"
    writes.write_text(json.dumps(CHARTED))
    monkeypatch.setenv("FAKE_CLAUDE_WRITES", str(writes))


def judgments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *invocations: Sequence[Call],
) -> None:
    """What each successive orchestrator-agent invocation decides to do."""
    script = tmp_path / "judgments.json"
    script.write_text(json.dumps([list(calls) for calls in invocations]))
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))


def test_a_node_is_completed_through_a_real_mcp_call(
    target_repo: Path,
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charts(monkeypatch, tmp_path)
    judgments(monkeypatch, tmp_path, [("complete_node", {"summary": "Charted it."})])
    prepared = prepare_run(a_run(target_repo, state_dir))

    manifest = execute_run(prepared, launcher())

    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.status is NodeStatus.DONE
    assert prepared.run.session_records()[0].summary == "Charted it."
    intervention = prepared.run.intervention_records()[0]
    assert [call.tool for call in intervention.tool_calls] == ["complete_node"]
    assert intervention.telemetry.cost_usd == pytest.approx(0.01)
    assert manifest.orchestrator_spend_usd == pytest.approx(0.01)


def test_the_run_carries_on_when_the_agent_messages_the_session(
    target_repo: Path,
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session that ended mid-thought gets a follow-up and keeps going."""
    charts(monkeypatch, tmp_path)
    judgments(
        monkeypatch,
        tmp_path,
        [("send_to_session", {"message": "Which did you settle on?"})],
        [("complete_node", {"summary": "Charted it."})],
    )
    prepared = prepare_run(a_run(target_repo, state_dir))

    manifest = execute_run(prepared, launcher())

    transcript = prepared.run.transcript_path(
        prepared.run.session_records()[0].session_id
    ).read_text()
    assert "received: Which did you settle on?" in transcript
    assert manifest.status is RunStatus.DONE
    assert len(prepared.run.intervention_records()) == 2


def test_a_refused_second_exclusive_call_leaves_the_node_incomplete(
    target_repo: Path,
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exclusivity is the harness's rule, and it holds over the wire."""
    charts(monkeypatch, tmp_path)
    judgments(
        monkeypatch,
        tmp_path,
        [
            ("send_to_session", {"message": "Carry on."}),
            ("complete_node", {"summary": "Actually, done."}),
        ],
        [("complete_node", {"summary": "Now it really is done."})],
    )
    prepared = prepare_run(a_run(target_repo, state_dir))

    manifest = execute_run(prepared, launcher())

    calls = prepared.run.intervention_records()[0].tool_calls
    assert [(call.tool, call.accepted) for call in calls] == [
        ("send_to_session", True),
        ("complete_node", False),
    ]
    assert "at most one of" in (calls[1].refused or "")
    assert manifest.status is RunStatus.DONE


def test_a_node_that_owes_a_tracker_file_cannot_be_completed_over_the_wire(
    target_repo: Path,
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is written, so every completion is refused and the node fails.

    The precondition and the nudge budget, over real HTTP: the agent judges the
    session finished each time, the tool layer disagrees each time, and after
    three such stale points the harness gives up on the node.
    """
    nudge: list[Call] = [
        ("complete_node", {"summary": "It says it charted the map."}),
        ("send_to_session", {"message": "Where did you write the map down?"}),
    ]
    judgments(monkeypatch, tmp_path, nudge, nudge, nudge)
    prepared = prepare_run(a_run(target_repo, state_dir))

    manifest = execute_run(prepared, launcher())

    assert manifest.status is RunStatus.FAILED
    assert manifest.root_node.status is NodeStatus.FAILED
    assert manifest.root_node.nudge_count == NUDGE_BUDGET
    assert manifest.root_node.missing_artifacts == ["map", "tickets"]
    interventions = prepared.run.intervention_records()
    assert len(interventions) == NUDGE_BUDGET
    for intervention in interventions:
        refused = intervention.tool_calls[0]
        assert (refused.tool, refused.accepted) == ("complete_node", False)
        assert "map.md" in (refused.refused or "")
    assert not (target_repo / ".scratch").exists()


def test_an_agent_cannot_act_on_a_node_it_was_not_invoked_about(
    target_repo: Path,
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brief_graces: None,
) -> None:
    """Aim the same call at another node's address and there is nothing there."""
    judgments(monkeypatch, tmp_path, [("complete_node", {"summary": "Not mine."})])
    monkeypatch.setenv("FAKE_CLAUDE_TOOL_NODE", "some-other-node")
    prepared = prepare_run(a_run(target_repo, state_dir))

    manifest = execute_run(prepared, launcher())

    assert prepared.run.intervention_records()[0].tool_calls == []
    assert manifest.status is RunStatus.FAILED
    assert prepared.run.session_records()[0].summary == NO_ACTION_NOTE


def test_an_invocation_that_calls_nothing_leaves_the_node_alone(
    target_repo: Path,
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brief_graces: None,
) -> None:
    judgments(monkeypatch, tmp_path, [])
    prepared = prepare_run(a_run(target_repo, state_dir))

    execute_run(prepared, launcher())

    assert prepared.run.intervention_records()[0].tool_calls == []
    assert prepared.run.read_manifest().root_node.status is not NodeStatus.DONE
