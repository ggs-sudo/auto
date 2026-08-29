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
auto serve                                     # the monitoring website, on :2886
```

`auto serve` is a separate long-lived process over the runs directory,
independent of any run: finished and crashed runs stay viewable, and every
run lists in one place. It watches the tree and pushes changes; `--poll` is
the fallback if watching proves unreliable, behind identical endpoints. The
site ships prebuilt inside the package (no node toolchain at runtime); see
`web/README.md` for the dev loop, and `python -m auto.fixture <dir>` for a
generated state directory to point it at.

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
chaining rules, the owed-artifact table beside the target repo's own tracker
doc verbatim, and the run's seed prompt framed as a message the orchestrator
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
- `complete_node` marks terminal success. It is refused while the node still
  owes a tracker file.
- `fail_node` marks terminal failure, with a reason.

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
working. It is also where a node stops: a headless session that has gone stale
does nothing further on its own, so with no message sent and no completion
there is no next stale point to wait for, and the node fails with that recorded
as the reason.

## Owed artifacts and nudging

The harness knows what each node type should leave behind — its **tracker
files** and the edits inside them. The session never does, and is never told:
a dispatch carries the skill invocation and its ticket and nothing else, so
every obligation is checked *after* a turn has ended.

That table (`auto.owed`) is used exactly twice. It is rendered into the
orchestrator agent's prompt beside the target repo's own tracker doc, so a
nudge reads like a user explaining the repo's convention rather than the
harness leaking through. And it is a precondition in the tool layer:
`complete_node` is **refused** while anything is missing and says what is
absent, so an agent that was going to complete the node anyway cannot, and its
only remaining move is to message the session. Disagreement between a ticket's
`Status:` line and harness node state therefore exists only mid-nudge, never as
persisted state.

Patience is finite and its arithmetic is the loop's. Three consecutive stale
points at which a completion was refused and the owed set did not shrink fail
the node; any shrinking is progress and starts the count again, so a node doing
the right thing slowly is never killed for it. Only refused completions count —
a session still being interviewed is working, not stalling — which is ADR-0004.
`auto show` prints what a node still owes and what it has cost it.

A failed node is a failed node, not a failed run: it blocks only what
depends on it, and a run ends failed when nothing is left to dispatch and
something failed. Today the harness drives exactly one node — the root — so
its failure is the whole run's; the graph that gives "only what depends on it"
something to mean arrives with the next ticket.

The owed check is delivery since dispatch: a baseline of what could already
satisfy the node's artifacts is fingerprinted before its session launches, so
leftovers from an earlier effort — or an earlier run — never complete a node
that wrote nothing, while a re-run that rewrites the same effort directory
still counts.

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

`uv tool update-shell` edits your shell config; open a new terminal (or
`source` the updated file) before `auto` resolves.

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
