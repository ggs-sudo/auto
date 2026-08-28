"""One session, launched for real above the seam, recorded afterwards."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from auto.config import ConfigOverrides
from auto.model import NodeStatus, NodeType, Route, RunStatus, SessionStatus
from auto.orchestrate import Orchestrator, RunRequest, execute_run, prepare_run
from auto.session.replay import ReplayLauncher
from tests.conftest import assistant_event, init_event, one_turn, result_event


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
    assert any("dirty" in w for w in prepare_run(a_request(target_repo, state_dir)).warnings)


def test_the_session_receives_the_entry_skill_invocation_and_nothing_else(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir, prompt="Add search."))
    launcher = ReplayLauncher({"root": one_turn()})
    execute_run(prepared, launcher)
    assert [spec.message for spec in launcher.launched] == ["/wayfinder Add search."]


def test_the_grill_route_enters_through_grill_with_docs(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(target_repo, state_dir, route=Route.GRILL, prompt="Add search.")
    )
    launcher = ReplayLauncher({"root": one_turn()})
    execute_run(prepared, launcher)
    assert launcher.launched[0].message == "/grill-with-docs Add search."


def test_the_session_launches_in_the_target_repo_with_the_spend_ceiling(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(
        a_request(target_repo, state_dir, overrides=ConfigOverrides(session_budget_usd=2.5))
    )
    launcher = ReplayLauncher({"root": one_turn()})
    execute_run(prepared, launcher)
    spec = launcher.launched[0]
    assert spec.cwd == target_repo.resolve()
    assert spec.max_budget_usd == 2.5
    assert spec.model is None


def test_nothing_is_sent_to_the_session_after_dispatch(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = ReplayLauncher({"root": one_turn()})
    execute_run(prepared, launcher)
    assert launcher.sent == []


def test_the_transcript_captures_every_event_verbatim(
    target_repo: Path, state_dir: Path
) -> None:
    import json

    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(prepared, ReplayLauncher({"root": one_turn()}))
    record = prepared.run.session_records()[0]
    lines = prepared.run.transcript_path(record.session_id).read_text().splitlines()
    assert [json.loads(line) for line in lines] == one_turn()


def test_the_session_record_points_at_its_transcript(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(prepared, ReplayLauncher({"root": one_turn()}))
    record = prepared.run.session_records()[0]
    assert record.captured_transcript == f"transcripts/{record.session_id}.jsonl"
    assert (prepared.run.path / record.captured_transcript).is_file()


def test_telemetry_from_the_result_event_lands_on_the_session_record(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    execute_run(
        prepared,
        ReplayLauncher({"root": one_turn("All done.", cost_usd=1.5, num_turns=9)}),
    )
    record = prepared.run.session_records()[0]
    assert record.telemetry.cost_usd == 1.5
    assert record.telemetry.num_turns == 9
    assert record.telemetry.stop_reason == "end_turn"
    assert record.summary == "All done."
    assert record.status is SessionStatus.SUCCEEDED
    assert record.role is NodeType.WAYFINDER
    assert record.node == "root"
    assert record.ticket is None
    assert record.ended_at is not None


def test_a_successful_session_completes_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(prepared, ReplayLauncher({"root": one_turn(cost_usd=1.5)}))
    assert manifest.status is RunStatus.DONE
    assert manifest.root_node.status is NodeStatus.DONE
    assert manifest.root_node.session_id == prepared.run.session_records()[0].session_id
    assert manifest.driven_spend_usd == 1.5
    assert manifest.ended_at is not None
    assert prepared.run.read_manifest() == manifest


def test_an_errored_result_fails_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    manifest = execute_run(
        prepared, ReplayLauncher({"root": one_turn("Budget exceeded.", is_error=True)})
    )
    assert manifest.status is RunStatus.FAILED
    assert manifest.root_node.status is NodeStatus.FAILED
    assert prepared.run.session_records()[0].status is SessionStatus.FAILED


def test_a_session_that_never_stales_fails_the_run(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = ReplayLauncher({"root": [init_event(), assistant_event("thinking")]})
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

    launcher = ReplayLauncher({"root": one_turn()})

    def observe(orchestrator: Orchestrator, event: dict[str, object]) -> None:
        manifest = prepared.run.read_manifest()
        records = prepared.run.session_records()
        if records:
            seen.append((manifest.status, records[0].status))

    execute_run(prepared, launcher, on_event=observe)
    assert (RunStatus.RUNNING, SessionStatus.RUNNING) in seen


def test_a_run_that_is_aborted_terminates_its_session_and_says_so(
    target_repo: Path, state_dir: Path
) -> None:
    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = ReplayLauncher({"root": one_turn()})

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
    execute_run(first, ReplayLauncher({"root": one_turn("Wrote the map.")}))
    captured = first.run.transcript_path(first.run.session_records()[0].session_id)

    second = prepare_run(a_request(target_repo, tmp_path / "state2"))
    manifest = execute_run(second, ReplayLauncher({"root": captured}))
    assert manifest.status is RunStatus.DONE
    assert second.run.session_records()[0].summary == "Wrote the map."


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
    orchestrator = Orchestrator(
        prepared.run, prepared.manifest, ReplayLauncher({"root": one_turn()})
    )
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
    assert exit_code_for(execute_run(prepared, ReplayLauncher({"root": one_turn()}))) == (
        EXIT_OK
    )

    failed = prepare_run(a_request(target_repo, state_dir))
    assert (
        exit_code_for(
            execute_run(failed, ReplayLauncher({"root": one_turn(is_error=True)}))
        )
        == EXIT_FAILED
    )


def test_an_abort_before_dispatch_starts_no_session_at_all(
    target_repo: Path, state_dir: Path
) -> None:
    """"Stops dispatching" has to hold before the launch, not only after it."""
    from auto.orchestrate import Orchestrator

    prepared = prepare_run(a_request(target_repo, state_dir))
    launcher = ReplayLauncher({"root": one_turn()})

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
        ReplayLauncher({"root": one_turn(cost_usd=2.5)}),
        on_event=abort_on_the_result,
    )
    assert manifest.status is RunStatus.ABORTED
    assert manifest.driven_spend_usd == 2.5
    assert prepared.run.session_records()[0].telemetry.cost_usd == 2.5
