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
runs/<run-id>/run.json                        # manifest: prompt, route, repo state, config, status
runs/<run-id>/orchestrator-prompt.md          # the agent's stable prompt, written once
runs/<run-id>/sessions/<session-id>.json      # one record per session
runs/<run-id>/transcripts/<session-id>.jsonl  # captured stream-json, verbatim
runs/<run-id>/interventions/<intervention-id>.json  # one record per agent invocation
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

## The orchestrator agent

Nothing is asked of a driven session. It runs a stock skill, is never told the
harness exists, and reports nothing. Everything the harness needs that the
session did not do on its own happens afterwards, as an **intervention**: the
session's turn ends, and a **fresh** `claude -p` agent reads its trace and acts.
One agent per intervention, never one per run — a single long-lived one would
accumulate every node's context and could not watch parallel nodes.

Its prompt is split by volatility. The stable half — the answer policy, the
chaining rules, and the run's seed prompt framed as a message the orchestrator
itself wrote — is assembled once per run into `orchestrator-prompt.md` and
appended to the default system prompt on every invocation, so hundreds of them
share a cached prefix. Only the node under judgment and its trace vary. A
monitored node (grilling, wayfinder) is read in full; an autonomous one is read
from the previous stale point, with a pathologically long turn truncated from
the middle so its opening intent and its closing writes both survive.

The agent's prose has no effect. Every effect is a **harness tool**, served over
MCP from inside the loop process:

- `send_to_session` delivers a message to the live session, which carries on
  with its context intact.
- `complete_node` marks terminal success.

They are mutually exclusive within one intervention, enforced by the harness
rather than requested in the prompt. Each intervention gets an inline, strict
MCP config whose URL names the run and the node under judgment, so an agent
cannot act on a node it was not invoked about: enforcement by addressing, with
no node argument for it to get wrong. The agent is launched under a tool
allowlist rather than a permission bypass — the harness tools plus `Read`,
`Glob` and `Grep` — so "its prose changes nothing" is a fact about what it can
do rather than a request in its prompt: it reads the target repo and never
writes to it. Its model is pinned in configuration and recorded on every
intervention, never inherited from your editor config, and its spend is counted
apart from what it drove.

An intervention that calls no tool is valid and means the node is still
working. Today that is where a run stops: a headless session that has gone
stale does nothing further on its own, so with no message sent and no
completion there is no next stale point to wait for, and the run ends failed
with that recorded as the reason. The nudge budget that turns "still working"
into a run that keeps going belongs to the next ticket.

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

### Two unverified assumptions

**Driven sessions take their opening message on stdin.** They are launched with
`--input-format stream-json` and the message is written to stdin, because that
is what keeps a headless session alive past its `result` event so the
orchestrator can message it later. That a slash-command skill invocation
expands when it arrives that way — rather than as a `-p "<prompt>"` positional,
where it is documented and verified — has not been confirmed against a live
CLI. If it turns out not to, the fix is local to `auto.session.cli_launcher`:
send the first turn positionally and keep stdin for the rest. Ephemeral
orchestrator-agent invocations already take their message positionally, since
they have nothing to stay alive for.

**The tool server answers MCP without an event stream.** `auto.tools.server` is
a small hand-written streamable-HTTP endpoint rather than the MCP SDK, because
the SDK brings an ASGI stack for two tools and does not make per-request URL
scoping easy. It answers `POST` with `application/json`, which the transport
allows for a request that gets a single response, and `405`s the `GET` that
would open a server-initiated stream. That the CLI's MCP client is happy with
both has not been confirmed against a live one; `tests/stubs/fake_claude.py`
speaks the same protocol and exercises the whole path, but it is not the real
client. See ADR-0003.
