"""The command line: how an operator starts a run and reads one back."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from auto.cli import main
from auto.model import Liveness, Route, RunStatus
from auto.orchestrate import RunRequest, prepare_run
from auto.run import list_runs
from auto.session.replay import ReplayLauncher
from tests.agents import HarnessLauncher, completes, tries_to_complete
from tests.conftest import CHARTED, SPECCED, dead_pid, make_target_repo, one_turn


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def invoke(
    runner: CliRunner,
    args: list[str],
    *,
    launcher: object = None,
    stdin: str | None = None,
) -> Result:
    from auto.cli import cli

    return runner.invoke(
        cli,
        args,
        input=stdin,
        obj={"launcher": launcher},
        catch_exceptions=False,
    )


def a_launcher(writes: dict[str, str] = CHARTED) -> HarnessLauncher:
    """A session that goes stale once, and an agent that calls it finished."""
    return HarnessLauncher(
        {"root": one_turn("Charted the map.")},
        [completes("Wrote the map.")],
        writes=[writes],
    )


def test_a_run_takes_its_prompt_inline(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    launcher = a_launcher()
    result = invoke(
        runner,
        [
            "run",
            "--route",
            "wayfinder",
            "-m",
            "Add search.",
            "--repo",
            str(target_repo),
            "--state-dir",
            str(state_dir),
        ],
        launcher=launcher,
    )
    assert result.exit_code == 0, result.output
    assert launcher.launched[0].message == "/wayfinder Add search."
    assert list_runs(state_dir)[0].status is RunStatus.DONE


def test_a_run_takes_its_prompt_from_a_file(
    runner: CliRunner, target_repo: Path, state_dir: Path, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "init_prompt.md"
    prompt_file.write_text("Add search to the settings page.\n")
    launcher = a_launcher()
    result = invoke(
        runner,
        [
            "run",
            "--route",
            "wayfinder",
            "-f",
            str(prompt_file),
            "--repo",
            str(target_repo),
            "--state-dir",
            str(state_dir),
        ],
        launcher=launcher,
    )
    assert result.exit_code == 0, result.output
    assert list_runs(state_dir)[0].prompt == "Add search to the settings page.\n"


def test_a_run_takes_its_prompt_from_piped_stdin(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    result = invoke(
        runner,
        [
            "run",
            "--route",
            "grill",
            "--repo",
            str(target_repo),
            "--state-dir",
            str(state_dir),
        ],
        launcher=a_launcher(SPECCED),
        stdin="Add search.\n",
    )
    assert result.exit_code == 0, result.output
    assert list_runs(state_dir)[0].prompt == "Add search.\n"


def test_the_target_repo_defaults_to_the_working_directory(
    runner: CliRunner, tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_target_repo(tmp_path / "cwd-repo")
    monkeypatch.chdir(repo)
    result = invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    assert result.exit_code == 0, result.output
    assert list_runs(state_dir)[0].target_repo == str(repo.resolve())


def test_giving_the_prompt_twice_is_refused(
    runner: CliRunner, target_repo: Path, state_dir: Path, tmp_path: Path
) -> None:
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("x")
    result = invoke(
        runner,
        [
            "run",
            "--route",
            "wayfinder",
            "-m",
            "Add search.",
            "-f",
            str(prompt_file),
            "--repo",
            str(target_repo),
            "--state-dir",
            str(state_dir),
        ],
        launcher=a_launcher(),
    )
    assert result.exit_code != 0
    assert "once" in result.output


def test_an_empty_prompt_is_refused(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    result = invoke(
        runner,
        [
            "run",
            "--route",
            "wayfinder",
            "-m",
            "   ",
            "--repo",
            str(target_repo),
            "--state-dir",
            str(state_dir),
        ],
        launcher=a_launcher(),
    )
    assert result.exit_code != 0
    assert "prompt" in result.output


def test_an_unknown_route_is_refused(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    result = invoke(
        runner,
        ["run", "--route", "vibes", "-m", "x", "--repo", str(target_repo)],
        launcher=a_launcher(),
    )
    assert result.exit_code != 0


def test_config_overrides_reach_the_manifest(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    result = invoke(
        runner,
        [
            "run",
            "--route",
            "wayfinder",
            "-m",
            "Add search.",
            "--repo",
            str(target_repo),
            "--state-dir",
            str(state_dir),
            "--concurrency",
            "2",
            "--session-budget",
            "1.5",
            "--run-budget",
            "9",
            "--orchestrator-model",
            "claude-sonnet-5",
        ],
        launcher=a_launcher(),
    )
    assert result.exit_code == 0, result.output
    config = list_runs(state_dir)[0].config
    assert (config.concurrency, config.session_budget_usd) == (2, 1.5)
    assert (config.run_budget_usd, config.orchestrator_model) == (9.0, "claude-sonnet-5")


def test_a_preflight_failure_is_a_message_not_a_traceback(
    runner: CliRunner, tmp_path: Path, state_dir: Path
) -> None:
    repo = make_target_repo(tmp_path / "no-tracker", tracker_doc=None)
    result = invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "x", "--repo", str(repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    assert result.exit_code == 1
    assert "issue-tracker.md" in result.output
    assert "Traceback" not in result.output


def test_a_dirty_working_tree_warns_and_the_run_proceeds(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    (target_repo / "wip.txt").write_text("x\n")
    result = invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "x", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    assert result.exit_code == 0, result.output
    assert "dirty" in result.output


def test_a_failed_run_exits_non_zero(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    result = invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "x", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=ReplayLauncher({"root": one_turn("Budget exceeded.", is_error=True)}),
    )
    assert result.exit_code == 1


def test_runs_lists_what_has_been_run(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    result = invoke(runner, ["runs", "--state-dir", str(state_dir)])
    assert result.exit_code == 0, result.output
    run_id = list_runs(state_dir)[0].run_id
    assert run_id in result.output
    assert "done" in result.output
    assert "wayfinder" in result.output


def test_runs_with_nothing_to_list_says_so(
    runner: CliRunner, state_dir: Path
) -> None:
    result = invoke(runner, ["runs", "--state-dir", str(state_dir)])
    assert result.exit_code == 0
    assert "no runs" in result.output


def test_show_prints_one_runs_state(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    run_id = list_runs(state_dir)[0].run_id
    result = invoke(runner, ["show", run_id, "--state-dir", str(state_dir)])
    assert result.exit_code == 0, result.output
    assert run_id in result.output
    assert "Add search." in result.output
    assert "Wrote the map." in result.output
    assert "root" in result.output


def test_show_says_what_a_node_still_owes_and_what_it_has_cost_it(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    """Checking on a stuck run over SSH should not mean reading a transcript."""
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo",
         str(target_repo), "--state-dir", str(state_dir)],
        launcher=HarnessLauncher(
            {"root": one_turn("I have thought about it.")},
            [tries_to_complete()],
        ),
    )
    run_id = list((state_dir / "runs").iterdir())[0].name
    result = invoke(runner, ["show", run_id, "--state-dir", str(state_dir)])
    assert "still owes map, tickets" in result.output
    assert "(nudge 1 of 3)" in result.output


def test_show_surfaces_what_the_orchestrator_decided(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    """An unattended decision has to be explainable afterwards."""
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    run_id = list_runs(state_dir)[0].run_id
    result = invoke(runner, ["show", run_id, "--state-dir", str(state_dir)])
    assert "intervention 0001-root" in result.output
    assert "stale" in result.output
    assert "complete_node" in result.output
    assert "claude-opus-5" in result.output


def test_show_as_json_carries_the_interventions_too(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    run_id = list_runs(state_dir)[0].run_id
    result = invoke(runner, ["show", run_id, "--json", "--state-dir", str(state_dir)])
    payload = json.loads(result.output)
    assert payload["interventions"][0]["node"] == "root"
    assert payload["interventions"][0]["tool_calls"][0]["tool"] == "complete_node"


def test_show_can_print_the_manifest_as_json(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    run_id = list_runs(state_dir)[0].run_id
    result = invoke(runner, ["show", run_id, "--json", "--state-dir", str(state_dir)])
    payload = json.loads(result.output)
    assert payload["manifest"]["run_id"] == run_id
    assert payload["sessions"][0]["node"] == "root"


def a_crashed_run(target_repo: Path, state_dir: Path) -> str:
    """A run directory the way a crash leaves it: the manifest still claims
    running, and the recorded orchestrator pid is dead. Returns the run id."""
    prepared = prepare_run(
        RunRequest(
            route=Route.WAYFINDER,
            prompt="Add search.",
            target_repo=target_repo,
            state_dir=state_dir,
        )
    )
    now = datetime.now(UTC)
    prepared.run.write_liveness(
        Liveness(pid=dead_pid(), started_at=now, heartbeat_at=now)
    )
    return prepared.manifest.run_id


def test_show_reports_a_dead_orchestrators_run_as_crashed(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    """A manifest claiming running is checked, not believed forever."""
    run_id = a_crashed_run(target_repo, state_dir)
    result = invoke(runner, ["show", run_id, "--state-dir", str(state_dir)])
    assert result.exit_code == 0, result.output
    assert f"{run_id}  crashed" in result.output
    assert "is dead" in result.output


def test_show_as_json_carries_the_liveness_and_the_verdict(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    run_id = a_crashed_run(target_repo, state_dir)
    result = invoke(runner, ["show", run_id, "--json", "--state-dir", str(state_dir)])
    payload = json.loads(result.output)
    assert payload["observed_status"] == "crashed"
    assert payload["manifest"]["status"] == "running"
    assert payload["liveness"]["pid"] > 0


def test_show_believes_a_run_whose_status_is_terminal(
    runner: CliRunner, target_repo: Path, state_dir: Path
) -> None:
    invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo), "--state-dir", str(state_dir)],
        launcher=a_launcher(),
    )
    run_id = list_runs(state_dir)[0].run_id
    result = invoke(runner, ["show", run_id, "--state-dir", str(state_dir)])
    assert f"{run_id}  done" in result.output
    assert "crashed" not in result.output


def test_show_of_an_unknown_run_is_a_message_not_a_traceback(
    runner: CliRunner, state_dir: Path
) -> None:
    result = invoke(runner, ["show", "nope", "--state-dir", str(state_dir)])
    assert result.exit_code == 1
    assert "no such run" in result.output


def test_the_state_dir_can_come_from_the_environment(
    runner: CliRunner, target_repo: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_STATE_DIR", str(state_dir))
    result = invoke(
        runner,
        ["run", "--route", "wayfinder", "-m", "Add search.", "--repo", str(target_repo)],
        launcher=a_launcher(),
    )
    assert result.exit_code == 0, result.output
    assert list_runs(state_dir)


def test_main_returns_an_exit_code_for_the_console_script() -> None:
    assert main(["--help"]) == 0
