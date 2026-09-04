"""One node, driven for real above the seam: dispatched, watched to its stale
point, judged by an ephemeral agent that really calls the harness tools, and
recorded."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auto.config import ConfigOverrides
from auto.liveness import CRASHED, observed_status, orchestrator_alive
from auto.model import (
    InterventionTrigger,
    Liveness,
    NodeStatus,
    NodeType,
    Route,
    RunStatus,
    SessionStatus,
)
from auto.orchestrate import (
    NO_ACTION_NOTE,
    NUDGE_BUDGET,
    Orchestrator,
    RunRequest,
    execute_run,
    prepare_run,
)
from auto.owed import RESOLVED_STATUS
from auto.session.protocol import LaunchSpec
from auto.session.replay import Recording
from auto.tools.harness import SERVER_NAME
from tests.agents import (
    HarnessLauncher,
    ScriptedAgent,
    completes,
    fails,
    says_nothing,
    sends,
    tries_to_complete,
)
from tests.conftest import (
    CHARTED,
    MAP_BODY,
    SPECCED,
    assistant_event,
    dead_pid,
    init_event,
    one_turn,
)


def a_request(
    target_repo: Path,
    state_dir: Path,
    *,
    route: Route = Route.WAYFINDER,
    prompt: str = "Add search to the settings page.",
    overrides: ConfigOverrides | None = None,
) -> RunRequest:
    return RunRequest(
        route=route,
        prompt=prompt,
        target_repo=target_repo,
        state_dir=state_dir,
        overrides=overrides or ConfigOverrides(),
    )


def a_launcher(
    *agents: ScriptedAgent,
    session: Recording | None = None,
    writes: Sequence[Mapping[str, str]] = (CHARTED,),
) -> HarnessLauncher:
    """A run whose session goes stale once and whose agent then completes it.

    Its first turn charts what a wayfinder node owes, so completion is allowed:
    a session that leaves nothing behind is the nudging tests' subject, not
    every other test's accident.
    """
    return HarnessLauncher(
        {"root": one_turn() if session is None else session},
        agents if agents else (completes(),),
        writes=writes,
    )


def turns(count: int) -> list[dict[str, object]]:
    """A session that goes stale `count` times, saying something new each time."""
    return [event for n in range(count) for event in one_turn(f"Turn {n + 1}.")]


def test_preparing_a_run_records_the_prompt_verbatim_and_the_repo_state(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(target_repo, state_dir, prompt="  Add search.\n\nWith facets.\n")
    )
    manifest = prepared.run.read_manifest()
    assert manifest.prompt == "  Add search.\n\nWith facets.\n"
    assert manifest.route is Route.WAYFINDER
    assert manifest.target_repo == str(target_repo.resolve())
    assert manifest.worktree == str(target_repo.resolve())
    assert manifest.branch == "main"
    assert manifest.dirty is False
    assert manifest.status is RunStatus.RUNNING
    assert manifest.root_node.type is NodeType.WAYFINDER
    assert manifest.root_node.status is NodeStatus.PENDING


def test_the_resolved_configuration_is_copied_into_the_manifest(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(
            target_repo,
            state_dir,
            overrides=ConfigOverrides(concurrency=7, session_budget_usd=3.5),
        )
    )
    config = prepared.run.read_manifest().config
    assert config.concurrency == 7
    assert config.session_budget_usd == 3.5


def test_preflight_warnings_reach_the_caller(
    target_repo: Path, state_dir: Path
) -> None:
    (target_repo / "wip.txt").write_text("x\n")
    assert any(
        "dirty" in w for w in prepare_run(a_request(target_repo, state_dir)).warnings
    )


def test_the_session_receives_the_entry_skill_invocation_and_nothing_else(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir, prompt="Add search."))
    launcher = a_launcher()
    execute_run(prepared, launcher)
    assert [spec.message for spec in launcher.launched] == ["/wayfinder Add search."]


def test_the_grill_route_enters_through_grill_with_docs(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(target_repo, state_dir, route=Route.GRILL, prompt="Add search.")
    )
    launcher = a_launcher(writes=(SPECCED,))
    manifest = execute_run(prepared, launcher)
    assert launcher.launched[0].message == "/grill-with-docs Add search."
    # A grilling chain owes a spec where a map would be, and is completed on it.
    assert manifest.status is RunStatus.DONE


def test_the_session_launches_in_the_target_repo_with_the_spend_ceiling(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(
            target_repo, state_dir, overrides=ConfigOverrides(session_budget_usd=2.5)
        )
    )
    launcher = a_launcher()
    execute_run(prepared, launcher)
    spec = launcher.launched[0]
    assert spec.cwd == target_repo.resolve()
    assert spec.max_budget_usd == 2.5
    assert spec.model is None


def test_a_driven_session_is_given_no_harness_tools_and_no_extra_prompt(
    target_repo: Path, state_dir: Path
) -> None:
    """The tools exist for the orchestrator agent alone."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher()
    execute_run(prepared, launcher)
    spec = launcher.launched[0]
    assert spec.mcp_config is None
    assert spec.append_system_prompt is None
    assert spec.one_shot is False


def test_nothing_reaches_the_session_unless_an_intervention_sends_it(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher()
    execute_run(prepared, launcher)
    assert launcher.sent == []


def test_the_transcript_captures_every_event_verbatim(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(prepared, a_launcher())
    record = prepared.run.session_records()[0]
    lines = prepared.run.transcript_path(record.session_id).read_text().splitlines()
    assert [json.loads(line) for line in lines] == one_turn()


def test_the_session_record_points_at_its_transcript(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(prepared, a_launcher())
    record = prepared.run.session_records()[0]
    assert record.captured_transcript == f"transcripts/{record.session_id}.jsonl"
    assert (prepared.run.path / record.captured_transcript).is_file()


def test_telemetry_from_the_result_event_lands_on_the_session_record(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(
        prepared,
        a_launcher(session=one_turn("All done.", cost_usd=1.5, num_turns=9)),
    )
    record = prepared.run.session_records()[0]
    assert record.telemetry.cost_usd == 1.5
    assert record.telemetry.num_turns == 9
    assert record.telemetry.stop_reason == "end_turn"
    assert record.status is SessionStatus.SUCCEEDED
    assert record.role is NodeType.WAYFINDER
    assert record.node == "root"
    assert record.ticket is None
    assert record.ended_at is not None


def test_the_sessions_own_words_are_recorded_at_each_stale_point(
    target_repo: Path, state_dir: Path
) -> None:
    """Until the node is judged finished, the record carries what it said."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    seen: list[str | None] = []

    def snapshot(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        if event.get("type") == "system":
            seen.append(prepared.run.session_records()[0].summary)

    execute_run(
        prepared,
        a_launcher(
            sends("Carry on."),
            completes("Charted the map and wrote four tickets."),
            session=[*one_turn("First."), *one_turn("Second.")],
        ),
        on_event=snapshot,
    )
    assert seen[-1] == "First."


def test_completing_a_node_replaces_the_summary_with_the_orchestrators_own(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(
        prepared,
        a_launcher(completes("Charted the map."), session=one_turn("All done.")),
    )
    assert prepared.run.session_records()[0].summary == "Charted the map."


def test_only_complete_node_completes_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(
        prepared, a_launcher(completes(), session=one_turn(cost_usd=1.5))
    )
    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.status is NodeStatus.DONE
    assert manifest.root_node.session_id == prepared.run.session_records()[0].session_id
    assert manifest.driven_spend_usd == 1.5
    assert manifest.ended_at is not None
    assert prepared.run.read_manifest() == manifest


def test_send_to_session_carries_a_session_that_stopped_mid_thought_onward(
    target_repo: Path, state_dir: Path
) -> None:
    """The demo case: a turn that ended early gets a follow-up and continues."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher(
        sends("Which of those two did you settle on?"),
        completes("Map charted."),
        session=[*one_turn("Two options…"), *one_turn("The second one.")],
    )
    manifest = execute_run(prepared, launcher)
    assert launcher.sent == [("root", "Which of those two did you settle on?")]
    assert manifest.status is RunStatus.DONE
    assert len(prepared.run.intervention_records()) == 2


def test_an_intervention_that_calls_no_tool_is_valid_and_recorded(
    target_repo: Path, state_dir: Path
) -> None:
    """Patience is expressible: the agent may decline to act."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, a_launcher(says_nothing("It is still going.")))
    record = prepared.run.intervention_records()[0]
    assert record.tool_calls == []
    assert record.prose == "It is still going."
    assert manifest.root_node.status is not NodeStatus.DONE
    assert prepared.run.session_records()[0].summary == NO_ACTION_NOTE


def test_highlights_accumulate_on_the_session_record_as_the_node_lives(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(
        prepared,
        a_launcher(
            sends("Carry on.", highlights=["chose Postgres full-text search"]),
            completes("Map charted.", highlights=["four tickets, two parallel"]),
            session=[*one_turn("First."), *one_turn("Second.")],
        ),
    )
    assert prepared.run.session_records()[0].highlights == [
        "chose Postgres full-text search",
        "four tickets, two parallel",
    ]


def test_a_fresh_agent_is_invoked_for_every_intervention(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher(
        sends("Carry on."),
        completes(),
        session=[*one_turn("First."), *one_turn("Second.")],
    )
    execute_run(prepared, launcher)
    records = prepared.run.intervention_records()
    assert len(records) == 2
    assert len({record.session_id for record in records}) == 2
    assert [spec.session_id for spec in launcher.interventions] == [
        record.session_id for record in records
    ]


def test_every_invocation_is_recorded_with_what_it_saw_and_did(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(
            target_repo,
            state_dir,
            overrides=ConfigOverrides(orchestrator_model="claude-opus-5"),
        )
    )
    execute_run(
        prepared,
        a_launcher(ScriptedAgent(calls=[("complete_node", {"summary": "Done."})],
                                 prose="The map is closed.", cost_usd=0.07)),
    )
    record = prepared.run.intervention_records()[0]
    assert record.node == "root"
    assert record.trigger is InterventionTrigger.STALE
    assert record.model == "claude-opus-5"
    assert record.prose == "The map is closed."
    assert [(c.tool, c.accepted) for c in record.tool_calls] == [("complete_node", True)]
    assert record.telemetry.cost_usd == 0.07
    assert record.ended_at is not None


def test_orchestrator_spend_is_counted_apart_from_driven_spend(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(
        prepared,
        a_launcher(
            sends("Carry on.", **{}),
            completes(),
            session=[
                *one_turn("First.", cost_usd=1.0),
                *one_turn("Second.", cost_usd=2.0),
            ],
        ),
    )
    assert manifest.driven_spend_usd == pytest.approx(3.0)
    assert manifest.orchestrator_spend_usd == pytest.approx(0.04)


def test_the_orchestrator_model_is_pinned_by_configuration(
    target_repo: Path, state_dir: Path
) -> None:
    """Never inherited from the user's editor config: a run is reproducible."""
    prepared = prepare_run(
        a_request(
            target_repo,
            state_dir,
            overrides=ConfigOverrides(orchestrator_model="claude-sonnet-5"),
        )
    )
    launcher = a_launcher()
    execute_run(prepared, launcher)
    assert launcher.interventions[0].model == "claude-sonnet-5"
    assert prepared.run.intervention_records()[0].model == "claude-sonnet-5"


def test_the_agent_is_launched_unable_to_write_to_the_target_repo(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher()
    execute_run(prepared, launcher)
    allowed = launcher.interventions[0].allowed_tools or ()
    assert "mcp__harness__complete_node" in allowed
    assert "Write" not in allowed


def test_the_invocation_is_scoped_to_one_run_and_one_node_by_its_url(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher()
    execute_run(prepared, launcher)
    spec = launcher.interventions[0]
    assert spec.one_shot is True
    config = json.loads(spec.mcp_config or "{}")
    assert list(config["mcpServers"]) == [SERVER_NAME]
    assert config["mcpServers"][SERVER_NAME]["url"].endswith(
        f"/runs/{prepared.manifest.run_id}/nodes/root/mcp"
    )


def test_the_stable_prompt_is_written_once_and_only_the_node_and_trace_vary(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir, prompt="Add search."))
    launcher = a_launcher(
        sends("Carry on."),
        completes(),
        session=[*one_turn("First."), *one_turn("Second.")],
    )
    execute_run(prepared, launcher)

    written = prepared.run.orchestrator_prompt_path.read_text()
    assert "Add search." in written
    prompts = [spec.append_system_prompt for spec in launcher.interventions]
    assert prompts == [written, written]
    messages = [spec.message for spec in launcher.interventions]
    assert messages[0] != messages[1]
    assert "First." in messages[0]
    assert "Second." in messages[1]


def test_a_monitored_node_is_judged_on_its_whole_conversation(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher(
        sends("Carry on."),
        completes(),
        session=[*one_turn("First."), *one_turn("Second.")],
    )
    execute_run(prepared, launcher)
    assert "First." in launcher.interventions[1].message


def test_an_errored_result_fails_the_run_without_asking_the_agent(
    target_repo: Path, state_dir: Path
) -> None:
    """A CLI-level error is not a judgment call."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(
        prepared,
        HarnessLauncher({"root": one_turn("Budget exceeded.", is_error=True)}, []),
    )
    assert manifest.status is RunStatus.FAILED
    assert manifest.root_node.status is NodeStatus.FAILED
    assert prepared.run.session_records()[0].status is SessionStatus.FAILED
    assert prepared.run.intervention_records() == []


def test_a_session_that_never_stales_fails_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": [init_event(), assistant_event("thinking")]}, []
    )
    manifest = execute_run(prepared, launcher)
    assert manifest.status is RunStatus.FAILED
    assert prepared.run.session_records()[0].status is SessionStatus.FAILED
    assert prepared.run.session_records()[0].telemetry.cost_usd is None


def test_the_session_record_exists_while_the_session_is_still_running(
    target_repo: Path, state_dir: Path
) -> None:
    """The website tails a run; it cannot wait for the run to end to see it."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    seen: list[tuple[RunStatus, SessionStatus]] = []

    def observe(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        manifest = prepared.run.read_manifest()
        records = prepared.run.session_records()
        if records:
            seen.append((manifest.status, records[0].status))

    execute_run(prepared, a_launcher(), on_event=observe)
    assert (RunStatus.RUNNING, SessionStatus.RUNNING) in seen


def test_a_run_that_is_aborted_terminates_its_session_and_says_so(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher({"root": one_turn()}, [])

    def abort_on_first_event(
        orchestrator: Orchestrator, event: dict[str, object]
    ) -> None:
        orchestrator.request_abort()

    manifest = execute_run(prepared, launcher, on_event=abort_on_first_event)
    assert manifest.status is RunStatus.ABORTED
    assert manifest.ended_at is not None
    assert prepared.run.session_records()[0].status is SessionStatus.FAILED
    assert launcher.sessions[0].closed is True


def test_a_captured_transcript_replays_as_a_fixture_without_conversion(
    target_repo: Path, state_dir: Path, tmp_path: Path
) -> None:
    first = prepare_run(a_request(target_repo, state_dir))
    execute_run(first, a_launcher(session=one_turn("Wrote the map.")))
    captured = first.run.transcript_path(first.run.session_records()[0].session_id)

    second = prepare_run(a_request(target_repo, tmp_path / "state2"))
    launcher = a_launcher(completes("Map."), session=captured)
    manifest = execute_run(second, launcher)
    assert manifest.status is RunStatus.DONE
    assert "Wrote the map." in launcher.interventions[0].message
    assert second.run.session_records()[0].summary == "Map."


def test_run_directory_writes_are_refused_off_the_loop_thread(
    target_repo: Path, state_dir: Path
) -> None:
    import threading

    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = prepared.run.read_manifest()
    failure: list[BaseException] = []

    def write_from_another_thread() -> None:
        try:
            prepared.run.write_manifest(manifest)
        except BaseException as exc:  # noqa: BLE001
            failure.append(exc)

    thread = threading.Thread(target=write_from_another_thread)
    thread.start()
    thread.join()
    assert failure and isinstance(failure[0], RuntimeError)


def test_preflight_failure_creates_no_run_directory(
    tmp_path: Path, state_dir: Path
) -> None:
    from auto.errors import PreflightError
    from tests.conftest import make_target_repo

    repo = make_target_repo(tmp_path / "no-tracker", tracker_doc=None)
    with pytest.raises(PreflightError):
        prepare_run(a_request(repo, state_dir))
    assert not (state_dir / "runs").exists()


def test_the_first_interrupt_stops_the_run_and_a_second_kills_now(
    target_repo: Path, state_dir: Path
) -> None:
    from auto.orchestrate import EXIT_INTERRUPTED, InterruptHandler

    prepared = prepare_run(a_request(target_repo, state_dir))
    orchestrator = Orchestrator(prepared.run, prepared.manifest, a_launcher())
    exits: list[int] = []
    handler = InterruptHandler(
        orchestrator, exit_now=exits.append, announce=lambda _: None
    )

    handler()
    assert orchestrator.aborting() is True
    assert exits == []

    handler()
    assert exits == [EXIT_INTERRUPTED]


def test_the_exit_code_says_how_the_run_ended(
    target_repo: Path, state_dir: Path
) -> None:
    from auto.orchestrate import EXIT_FAILED, EXIT_OK, exit_code_for

    prepared = prepare_run(a_request(target_repo, state_dir))
    assert exit_code_for(execute_run(prepared, a_launcher())) == EXIT_OK

    failed = prepare_run(a_request(target_repo, state_dir))
    assert (
        exit_code_for(
            execute_run(
                failed, HarnessLauncher({"root": one_turn(is_error=True)}, [])
            )
        )
        == EXIT_FAILED
    )


def test_an_abort_before_dispatch_starts_no_session_at_all(
    target_repo: Path, state_dir: Path
) -> None:
    """"Stops dispatching" has to hold before the launch, not only after it."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = a_launcher()

    async def abort_then_execute() -> None:
        orchestrator = Orchestrator(prepared.run, prepared.manifest, launcher)
        orchestrator.request_abort()
        await orchestrator.execute()

    asyncio.run(abort_then_execute())
    assert launcher.launched == []
    assert prepared.run.read_manifest().status is RunStatus.ABORTED


def test_a_result_arriving_with_an_abort_still_records_what_it_cost(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))

    def abort_on_the_result(
        orchestrator: Orchestrator, event: dict[str, object]
    ) -> None:
        if event.get("type") == "result":
            orchestrator.request_abort()

    manifest = execute_run(
        prepared,
        HarnessLauncher({"root": one_turn(cost_usd=2.5)}, []),
        on_event=abort_on_the_result,
    )
    assert manifest.status is RunStatus.ABORTED
    assert manifest.driven_spend_usd == 2.5
    assert prepared.run.session_records()[0].telemetry.cost_usd == 2.5


# --- owed artifacts, nudging and failure -------------------------------------


def test_a_node_cannot_be_completed_while_it_still_owes_a_tracker_file(
    target_repo: Path, state_dir: Path
) -> None:
    """The precondition is the tool layer's, not advice in a prompt."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": turns(2)},
        (tries_to_complete(then="Where did you write the map?"), completes()),
        writes=[{}, CHARTED],
    )
    manifest = execute_run(prepared, launcher)

    refusal, delivered = launcher.tool_results[0], launcher.tool_results[1]
    assert refusal["isError"] is True
    assert delivered["isError"] is False
    assert manifest.status is RunStatus.DONE
    first = prepared.run.intervention_records()[0]
    assert [(call.tool, call.accepted) for call in first.tool_calls] == [
        ("complete_node", False),
        ("send_to_session", True),
    ]


def test_the_refusal_says_which_artifacts_are_absent(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": turns(2)},
        (tries_to_complete(then="Write the map, then the tickets."), completes()),
        writes=[{}, CHARTED],
    )
    execute_run(prepared, launcher)

    refusal = launcher.tool_results[0]["content"][0]["text"]
    assert "map.md" in refusal
    assert ".scratch/<effort>/issues/" in refusal


def test_a_nudged_session_that_writes_what_it_owed_is_then_completed(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(
        prepared,
        HarnessLauncher(
            {"root": turns(2)},
            (tries_to_complete(then="Please write it down."), completes("Charted.")),
            writes=[{}, CHARTED],
        ),
    )
    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.missing_artifacts == []
    # The nudge worked, so the count it cost was given back.
    assert manifest.root_node.nudge_count == 0


def test_three_consecutive_stale_points_with_no_shrink_fail_the_node(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": turns(4)},
        (
            tries_to_complete(then="Where are the tickets?"),
            tries_to_complete(then="Still nothing on disk — write them."),
            tries_to_complete(),
        ),
        writes=[{}],
    )
    manifest = execute_run(prepared, launcher)

    assert manifest.root_node.status is NodeStatus.FAILED
    assert manifest.status is RunStatus.FAILED
    assert manifest.root_node.nudge_count == NUDGE_BUDGET
    assert len(prepared.run.intervention_records()) == 3
    summary = prepared.run.session_records()[0].summary or ""
    assert "map.md" in summary
    assert manifest.root_node.missing_artifacts == ["map", "tickets"]


def test_a_node_whose_owed_set_shrinks_keeps_its_full_nudge_budget(
    target_repo: Path, state_dir: Path
) -> None:
    """Partial progress is progress: the count starts again from zero."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": turns(5)},
        (
            *(tries_to_complete(then="Carry on.") for _ in range(4)),
            tries_to_complete(),
        ),
        # Nothing at all, then the map alone — the owed set shrinks once and
        # then stops, so the budget is spent from that point, not before it.
        writes=[{}, {}, {".scratch/add-search/map.md": MAP_BODY}],
    )
    manifest = execute_run(prepared, launcher)

    assert manifest.root_node.status is NodeStatus.FAILED
    assert manifest.root_node.nudge_count == NUDGE_BUDGET
    assert manifest.root_node.missing_artifacts == ["tickets"]
    # Two nudge points before the map arrived, three after it: the shrink in
    # between cost the budget nothing.
    assert len(prepared.run.intervention_records()) == 5


def test_fail_node_marks_terminal_failure_with_a_reason(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(
        prepared,
        a_launcher(fails("the repo has no settings page to add search to")),
    )
    assert manifest.root_node.status is NodeStatus.FAILED
    assert manifest.status is RunStatus.FAILED
    record = prepared.run.session_records()[0]
    assert record.status is SessionStatus.FAILED
    assert record.summary == "the repo has no settings page to add search to"
    assert record.ended_at is not None


def test_a_run_ends_failed_when_nothing_is_left_to_dispatch_and_something_failed(
    target_repo: Path, state_dir: Path
) -> None:
    from auto.orchestrate import EXIT_FAILED, exit_code_for

    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, a_launcher(fails("nothing to be done")))
    assert exit_code_for(manifest) == EXIT_FAILED
    assert manifest.ended_at is not None


def test_the_orchestrator_never_writes_a_tracker_file_itself(
    target_repo: Path, state_dir: Path
) -> None:
    """Not even to rescue a node it is about to fail for want of one."""
    before = sorted(p.relative_to(target_repo) for p in target_repo.rglob("*"))
    execute_run(
        prepare_run(a_request(target_repo, state_dir)),
        HarnessLauncher(
            {"root": turns(4)},
            tuple(tries_to_complete(then="Write it down.") for _ in range(3)),
            writes=[{}],
        ),
    )
    assert sorted(p.relative_to(target_repo) for p in target_repo.rglob("*")) == before
    assert not (target_repo / ".scratch").exists()


def test_what_the_node_still_owes_is_recorded_as_the_run_goes(
    target_repo: Path, state_dir: Path
) -> None:
    """The count means nothing without the set it is counting."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    seen: list[list[str]] = []

    def snapshot(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        if event.get("type") == "result":
            seen.append(prepared.run.read_manifest().root_node.missing_artifacts)

    execute_run(
        prepared,
        HarnessLauncher(
            {"root": turns(2)},
            (tries_to_complete(then="Write it down."), completes()),
            writes=[{}, CHARTED],
        ),
        on_event=snapshot,
    )
    assert seen[0] == ["map", "tickets"]
    assert prepared.run.read_manifest().root_node.missing_artifacts == []


def test_an_implementation_node_owes_its_ticket_resolved() -> None:
    """The table covers every node type, not only the two a root node can be."""
    from auto.owed import OWED, keys

    assert keys(OWED[NodeType.IMPLEMENT]) == ["resolved"]
    assert RESOLVED_STATUS.search("**Status:** resolved") is not None


# --- orchestrator liveness ---------------------------------------------------


class SlowedSession:
    """A driven session that takes real loop time over its turn.

    Replayed events arrive instantly, which never yields long enough for the
    heartbeat task to fire; a short sleep before each event does.
    """

    def __init__(self, inner: Any, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    @property
    def session_id(self) -> str:
        sid: str = self._inner.session_id
        return sid

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        async for event in self._inner.events():
            await asyncio.sleep(self._delay)
            yield event

    async def send(self, message: str) -> None:
        await self._inner.send(message)

    async def close(self) -> None:
        await self._inner.close()

    async def terminate(self) -> None:
        await self._inner.terminate()

    def kill(self) -> None:
        self._inner.kill()


class SlowedLauncher:
    """Slows driven sessions down; agent invocations pass through untouched."""

    def __init__(self, inner: HarnessLauncher, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    async def launch(self, spec: LaunchSpec) -> Any:
        session = await self._inner.launch(spec)
        return session if spec.one_shot else SlowedSession(session, self._delay)


def a_liveness(pid: int) -> Liveness:
    now = datetime.now(UTC)
    return Liveness(pid=pid, started_at=now, heartbeat_at=now)


def test_a_live_run_records_its_orchestrators_pid_and_heartbeat(
    target_repo: Path, state_dir: Path
) -> None:
    """While the loop runs, the run directory says which process holds it."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    seen: list[Liveness | None] = []

    def snapshot(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        seen.append(prepared.run.read_liveness())

    execute_run(prepared, a_launcher(), on_event=snapshot)
    assert seen and all(liveness is not None for liveness in seen)
    assert seen[0] is not None and seen[0].pid == os.getpid()
    assert seen[0].heartbeat_at >= seen[0].started_at
    # The file outlives the run: a terminal manifest status outranks it, and
    # the last heartbeat is part of the run's history.
    assert prepared.run.read_liveness() is not None


def test_a_live_runs_running_claim_is_believed(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    seen: list[str] = []

    def snapshot(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        seen.append(
            observed_status(
                prepared.run.read_manifest(), prepared.run.read_liveness()
            )
        )

    execute_run(prepared, a_launcher(), on_event=snapshot)
    assert RunStatus.RUNNING.value in seen
    assert CRASHED not in seen


def test_the_heartbeat_refreshes_while_the_loop_runs(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    beats: list[datetime] = []

    def snapshot(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        liveness = prepared.run.read_liveness()
        if liveness is not None:
            beats.append(liveness.heartbeat_at)

    execute_run(
        prepared,
        SlowedLauncher(a_launcher(), delay=0.1),
        on_event=snapshot,
        heartbeat_seconds=0.01,
    )
    assert len(set(beats)) > 1


def test_a_running_manifest_with_a_dead_orchestrator_reads_as_crashed(
    target_repo: Path, state_dir: Path
) -> None:
    """The dead fixture: the manifest claims running; the recorded pid is gone.

    Exactly what a crash leaves behind — no terminal status was ever written —
    and the distinction is read off the process table, not guessed from age.
    """
    prepared = prepare_run(a_request(target_repo, state_dir))
    prepared.run.write_liveness(a_liveness(dead_pid()))
    liveness = prepared.run.read_liveness()
    manifest = prepared.run.read_manifest()
    assert manifest.status is RunStatus.RUNNING
    assert orchestrator_alive(liveness) is False
    assert observed_status(manifest, liveness) == CRASHED


def test_a_running_manifest_with_no_liveness_recorded_reads_as_crashed(
    target_repo: Path, state_dir: Path
) -> None:
    """A live orchestrator writes its liveness before it drives anything, so a
    running claim with no record behind it has no live loop either way."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    assert prepared.run.read_liveness() is None
    assert observed_status(prepared.run.read_manifest(), None) == CRASHED


def test_a_gated_claim_is_checked_the_same_way(
    target_repo: Path, state_dir: Path
) -> None:
    """Gated still means an orchestrator is alive, polling for the answer."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = prepared.run.read_manifest().model_copy(
        update={"status": RunStatus.GATED}
    )
    assert observed_status(manifest, a_liveness(dead_pid())) == CRASHED
    assert observed_status(manifest, a_liveness(os.getpid())) == "gated"


def test_a_terminal_status_stands_whatever_became_of_the_process(
    target_repo: Path, state_dir: Path
) -> None:
    """Only a claim of liveness is checked; a finished run's status is a fact."""
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, a_launcher())
    assert manifest.status is RunStatus.DONE
    # The orchestrator's process dies eventually; the verdict must not change.
    assert observed_status(manifest, a_liveness(dead_pid())) == "done"
    assert observed_status(manifest, None) == "done"


def test_leftovers_from_an_earlier_run_do_not_complete_a_fresh_node(
    target_repo: Path, state_dir: Path
) -> None:
    """Story 34: the graph never claims something the repo cannot back up."""
    for path, body in CHARTED.items():
        file = target_repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body)

    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = HarnessLauncher(
        {"root": turns(2)},
        (tries_to_complete(then="Write the map down."), completes()),
        # The session writes the same paths again, as a re-run really would.
        writes=[{}, CHARTED],
    )
    manifest = execute_run(prepared, launcher)

    first = prepared.run.intervention_records()[0]
    assert [(c.tool, c.accepted) for c in first.tool_calls] == [
        ("complete_node", False),
        ("send_to_session", True),
    ]
    assert manifest.status is RunStatus.DONE
