# auto

The `auto` harness: a CLI that drives stock Claude Code skills through
`claude -p` to carry a pasted feature prompt to implemented work in a target
repo. See `CONTEXT.md` for the glossary — the terms there are used exactly —
and `docs/adr/` for the decisions behind the design.

## Using it

```bash
auto run --route wayfinder -f init_prompt.md   # or -m "...", or pipe on stdin
auto runs                                      # every run, newest first
auto show 20260828-120000-add-search           # one run's state (--json too)
```

The target repo defaults to the working directory; `--repo` points elsewhere.
The route is always explicit — `wayfinder` enters through `/wayfinder`, `grill`
through `/grill-with-docs` — because the harness never guesses which entry
skill a prompt deserves.

A run refuses to start if the target repo is not a git repository, has no
`docs/agents/issue-tracker.md`, or does not gitignore `.scratch/`. A dirty
working tree warns and proceeds. Nothing is ever created in the target repo:
the harness drives repos that are already set up.

## Run state

Run state lives under `$AUTO_STATE_DIR`, or `~/.auto` if that is unset:

```
runs/<run-id>/run.json                       # manifest: prompt, route, repo state, config, status
runs/<run-id>/sessions/<session-id>.json     # one record per session
runs/<run-id>/transcripts/<session-id>.jsonl # captured stream-json, verbatim
```

Configuration comes from defaults in code, then `$AUTO_STATE_DIR/config.toml`,
then per-invocation flags; whatever resolved is copied into the manifest.

```toml
# ~/.auto/config.toml
concurrency = 4
session_budget_usd = 10.0
run_budget_usd = 100.0
orchestrator_model = "claude-opus-5"
```

Interrupting a run stops dispatching, brings its session down and marks the run
aborted; a second interrupt kills immediately.

## Development

```bash
uv sync
uv run pytest
uv run mypy
uv run python -m auto.model.export          # regenerate schemas/ from the models
uv run python -m auto.model.export --check  # what the test does
```

Install so `auto` on PATH points at this working tree:

```bash
uv tool install --editable .
uv tool update-shell   # if ~/.local/bin is not already on PATH
```

The Pydantic models under `src/auto/model/` are the source of truth; the JSON
Schemas in `schemas/` are generated from them and checked in, and a test fails
when they drift.

### The one seam

Everything the harness does that is not pure computation goes through
`auto.session.protocol` — launching a `claude -p` process and exchanging
stream-json with it. There are two implementations: `ClaudeCliLauncher` and
`ReplayLauncher`. Because the seam sits that high, a test drives a whole run
for real — the CLI, the loop, dispatch, every run-directory write — without
ever calling Claude.

Recorded fixtures are captured transcripts: `transcripts/<session-id>.jsonl`
from a real run is a fixture with no conversion step.

`$AUTO_CLAUDE_BIN` points the real launcher at something other than `claude` on
PATH. `tests/stubs/fake_claude.py` is a stand-in that speaks the same stream-json,
which is how the end-to-end path is exercised without spending anything.

### One unverified assumption

Sessions are launched with `--input-format stream-json` and the opening message
is written to stdin, because that is what keeps a headless session alive past
its `result` event so the orchestrator can message it later. That a
slash-command skill invocation expands when it arrives that way — rather than
as a `-p "<prompt>"` positional, where it is documented and verified — has not
been confirmed against a live CLI. If it turns out not to, the fix is local to
`auto.session.cli_launcher`: send the first turn positionally and keep stdin
for the rest.
