"""The `auto` command line.

    auto run --route wayfinder -f init_prompt.md
    auto runs
    auto show 20260828-120000-add-search
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from auto.config import ConfigOverrides, state_dir_from_env
from auto.errors import AutoError, UsageError
from auto.model import InterventionRecord, Manifest, Route, SessionRecord
from auto.orchestrate import (
    EXIT_FAILED,
    NUDGE_BUDGET,
    RunRequest,
    execute_run,
    exit_code_for,
    prepare_run,
)
from auto.run import list_runs, load_run
from auto.session.cli_launcher import ClaudeCliLauncher
from auto.session.protocol import Launcher

_STATE_DIR_OPTION = click.option(
    "--state-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Where run state lives. Defaults to $AUTO_STATE_DIR, else ~/.auto.",
)


def _state_dir(given: Path | None) -> Path:
    return given.expanduser() if given is not None else state_dir_from_env()


class AutoGroup(click.Group):
    """Turns the harness's own errors into messages, wherever they are raised.

    On the group rather than in `main`, so the conversion is the same whether
    the CLI is entered from a console script or driven in a test.
    """

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except AutoError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(
    cls=AutoGroup, context_settings={"help_option_names": ["-h", "--help"]}
)
@click.version_option(package_name="auto-harness")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Drive Claude Code skills through `claude -p` to implement a feature."""
    ctx.ensure_object(dict)


@cli.command()
@click.option(
    "--route",
    type=click.Choice([route.value for route in Route]),
    required=True,
    help="Which skill the run enters through. Never inferred.",
)
@click.option("-m", "--message", "message", default=None, help="The prompt, inline.")
@click.option(
    "-f",
    "--file",
    "prompt_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the prompt from a file.",
)
@click.option(
    "--repo",
    type=click.Path(path_type=Path),
    default=None,
    help="The target repo. Defaults to the working directory.",
)
@click.option("--concurrency", type=int, default=None, help="Driven sessions at once.")
@click.option(
    "--session-budget",
    type=float,
    default=None,
    help="Spend ceiling for a single session, in USD.",
)
@click.option(
    "--run-budget",
    type=float,
    default=None,
    help="Spend ceiling for the whole run, in USD.",
)
@click.option(
    "--orchestrator-model",
    default=None,
    help="Model for orchestrator-agent invocations. Pinned, not inherited.",
)
@_STATE_DIR_OPTION
@click.pass_context
def run(
    ctx: click.Context,
    route: str,
    message: str | None,
    prompt_file: Path | None,
    repo: Path | None,
    concurrency: int | None,
    session_budget: float | None,
    run_budget: float | None,
    orchestrator_model: str | None,
    state_dir: Path | None,
) -> None:
    """Start a run from a pasted prompt."""
    request = RunRequest(
        route=Route(route),
        prompt=_read_prompt(message, prompt_file),
        target_repo=repo if repo is not None else Path.cwd(),
        state_dir=_state_dir(state_dir),
        overrides=ConfigOverrides(
            concurrency=concurrency,
            session_budget_usd=session_budget,
            run_budget_usd=run_budget,
            orchestrator_model=orchestrator_model,
        ),
    )

    prepared = prepare_run(request)
    for warning in prepared.warnings:
        click.echo(f"warning: {warning}", err=True)
    click.echo(f"run {prepared.manifest.run_id} in {prepared.run.path}")

    manifest = execute_run(prepared, _launcher(ctx), handle_interrupts=True)
    click.echo(
        f"{manifest.status.value}: {manifest.run_id} "
        f"(${manifest.driven_spend_usd:.4f} driven)"
    )
    ctx.exit(exit_code_for(manifest))


@cli.command(name="runs")
@_STATE_DIR_OPTION
def list_runs_command(state_dir: Path | None) -> None:
    """List runs, newest first."""
    manifests = list_runs(_state_dir(state_dir))
    if not manifests:
        click.echo("no runs yet")
        return
    width = max(len(manifest.run_id) for manifest in manifests)
    for manifest in manifests:
        click.echo(
            f"{manifest.run_id:<{width}}  {manifest.status.value:<8} "
            f"{manifest.route.value:<9} {manifest.target_repo}"
        )


@cli.command()
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Print the raw state as JSON.")
@_STATE_DIR_OPTION
def show(run_id: str, as_json: bool, state_dir: Path | None) -> None:
    """Print one run's state."""
    run_dir = load_run(_state_dir(state_dir), run_id)
    manifest = run_dir.read_manifest()
    sessions = run_dir.session_records()
    interventions = run_dir.intervention_records()
    if as_json:
        click.echo(
            json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "sessions": [s.model_dump(mode="json") for s in sessions],
                    "interventions": [i.model_dump(mode="json") for i in interventions],
                },
                indent=2,
            )
        )
        return
    _print_run(run_dir.path, manifest, sessions, interventions)


def _print_run(
    path: Path,
    manifest: Manifest,
    sessions: list[SessionRecord],
    interventions: list[InterventionRecord],
) -> None:
    click.echo(f"{manifest.run_id}  {manifest.status.value}")
    click.echo(f"  route        {manifest.route.value}")
    click.echo(f"  target repo  {manifest.target_repo}")
    click.echo(
        f"  repo state   {manifest.branch or '(detached)'} "
        f"@ {(manifest.head or '')[:12]}"
        f"{' (dirty)' if manifest.dirty else ''}"
    )
    click.echo(f"  started      {manifest.created_at.isoformat()}")
    if manifest.ended_at is not None:
        click.echo(f"  ended        {manifest.ended_at.isoformat()}")
    click.echo(
        f"  spend        ${manifest.driven_spend_usd:.4f} driven, "
        f"${manifest.orchestrator_spend_usd:.4f} orchestrator"
    )
    click.echo(f"  state        {path}")
    click.echo("  prompt")
    for line in manifest.prompt.rstrip("\n").splitlines() or [""]:
        click.echo(f"    {line}")

    node = manifest.root_node
    click.echo(f"\n  {node.node_id}  {node.type.value}  {node.status.value}")
    if node.graph is not None:
        click.echo(f"    spawned graph {node.graph}")
    if node.missing_artifacts:
        click.echo(
            f"    still owes {', '.join(node.missing_artifacts)}"
            f"  (nudge {node.nudge_count} of {NUDGE_BUDGET})"
        )
    # One session per node, so a session heads its node's block and the
    # node's interventions read beneath it, in the order the run drove them.
    for session in sorted(sessions, key=lambda record: record.started_at):
        turns = session.telemetry.num_turns or 0
        click.echo(
            f"    session {session.session_id}  {session.node}"
            f"  {session.status.value}"
            f"  ${session.telemetry.cost_usd or 0:.4f}"
            f"  {turns} turn{'' if turns == 1 else 's'}"
        )
        if session.summary:
            click.echo(f"      {session.summary}")
        for highlight in session.highlights:
            click.echo(f"      - {highlight}")
        for intervention in interventions:
            if intervention.node != session.node:
                continue
            click.echo(
                f"    intervention {intervention.intervention_id}"
                f"  {intervention.trigger.value}  {intervention.model}"
                f"  ${intervention.telemetry.cost_usd or 0:.4f}"
            )
            for call in intervention.tool_calls:
                verdict = "" if call.accepted else f"  (refused: {call.refused})"
                click.echo(f"      {call.tool}{verdict}")
            if not intervention.tool_calls:
                click.echo("      no action: the node is still working")


def _read_prompt(message: str | None, prompt_file: Path | None) -> str:
    """The prompt comes from exactly one of `-m`, `-f`, or piped stdin."""
    given = [source for source in (message, prompt_file) if source is not None]
    if len(given) > 1:
        raise UsageError("give the prompt once: -m or -f, not both")
    if message is not None:
        prompt = message
    elif prompt_file is not None:
        prompt = prompt_file.read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        raise UsageError("no prompt: pass -m, -f, or pipe one in on stdin")
    if not prompt.strip():
        raise UsageError("the prompt is empty")
    return prompt


def _launcher(ctx: click.Context) -> Launcher:
    """The real launcher, unless a caller injected one (which tests do)."""
    injected = ctx.find_root().obj.get("launcher")
    if injected is not None:
        launcher: Launcher = injected
        return launcher
    return ClaudeCliLauncher()


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Errors are messages, not tracebacks."""
    try:
        result: Any = cli.main(
            args=argv, obj={}, standalone_mode=False, prog_name="auto"
        )
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Abort:
        return 130
    except AutoError as exc:
        click.echo(f"error: {exc}", err=True)
        return EXIT_FAILED
    if isinstance(result, int):
        return result
    return 0
