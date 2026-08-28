"""One node, driven for real above the seam: dispatched, watched to its stale
point, judged by an ephemeral agent that really calls the harness tools, and
recorded."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from auto.config import ConfigOverrides
from auto.model import (
    InterventionTrigger,
    NodeStatus,
    NodeType,
    Route,
    RunStatus,
    SessionStatus,
)
from auto.orchestrate import (
    NO_ACTION_NOTE,
    Orchestrator,
    RunRequest,
    execute_run,
    prepare_run,
)
from auto.session.replay import Recording
from auto.tools.harness import SERVER_NAME
from tests.agents import HarnessLauncher, ScriptedAgent, completes, says_nothing, sends
from tests.conftest import assistant_event, init_event, one_turn


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
    *agents: ScriptedAgent, session: Recording | None = None
) -> HarnessLauncher:
    """A run whose session goes stale once and whose agent then completes it."""
    return HarnessLauncher(
        {"root": one_turn() if session is None else session},
        agents if agents else (completes(),),
    )


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
    launcher = a_launcher()
    execute_run(prepared, launcher)
    assert launcher.launched[0].message == "/grill-with-docs Add search."


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
