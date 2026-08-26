# Research: `claude -p` headless mechanics

Resolves [#2](https://github.com/ggs-sudo/auto/issues/2). Sources: official Claude Code docs
([headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference),
[sessions](https://code.claude.com/docs/en/sessions), [skills](https://code.claude.com/docs/en/skills),
[permission modes](https://code.claude.com/docs/en/permission-modes)), `claude --help`, and local experiments
run against Claude Code **v2.1.231** on 2026-08-26. (Note: `docs.claude.com/en/docs/claude-code/*` now 301-redirects
to `code.claude.com/docs/en/*`.)

## 1. Chaining one logical conversation across invocations

Every `claude -p` run creates (or extends) a persisted session with a UUID session id.

- **Capture the id**: run with `--output-format json` and read `.session_id` from the single JSON
  object on stdout:
  ```bash
  session_id=$(claude -p "Start a review" --output-format json | jq -r '.session_id')
  ```
  In `stream-json` output, every event (including the `system/init` first event and the final
  `result` event) carries `session_id`.
- **Resume that session**: `claude -p "next prompt" --resume "$session_id"`. Verified locally:
  the follow-up run answered from the prior context and the result JSON reported the *same*
  `session_id`, so one id names the whole logical conversation across N invocations — exactly the
  chaining needed for grill → `/to-spec` → `/to-tickets`.
- **`--continue` / `-c`**: resumes the most recent conversation *in the current directory*.
  Plain `claude --continue` (interactive) skips `-p`/SDK sessions; `claude -p --continue` includes
  them (docs: headless § Continue conversations, sessions § Resume a session). For an orchestrator
  running several agents, prefer explicit `--resume <id>` — `--continue` is racy when more than one
  session shares a directory.
- **Cross-directory resume**: since v2.1.223, `claude --resume <session-id>` searches the current
  project directory and its git worktrees first, then every other project on the machine, so the
  resume doesn't have to run from the original cwd.
- **`--session-id <uuid>`**: pre-assign the session id (must be a valid UUID) so the orchestrator
  can know the id before launch instead of parsing it from output.
- **`--fork-session`**: with `--resume`/`--continue`, copies the conversation into a *new* session
  id instead of appending to the original — useful for branching (e.g. two ticket-generation
  attempts from one grilled context).
- Resuming restores conversation history, model, and agent, but **not** launch flags like
  `--mcp-config`, `--settings`, `--add-dir`; pass those again on every invocation. `plan` and
  `bypassPermissions` modes are never restored — re-pass the permission flags each time too.
- `num_turns` in the result JSON counts the current invocation only, not the whole session
  (observed: a resumed run reported `num_turns: 1`).

## 2. Invoking slash-command-only skills headlessly

**Yes, it works.** Docs (headless § Auto-approve tools note): "User-invoked skills and custom
commands work in `-p` mode: include `/skill-name` in the prompt string and Claude Code expands it
before running." Only terminal-only built-ins like `/login` are unavailable.

Verified locally: a project skill at `.claude/skills/echo-test/SKILL.md` with
`disable-model-invocation: true` ran fine via `claude -p "/echo-test hello-world"`.
`disable-model-invocation` only stops *Claude* from auto-loading the skill; explicit `/name`
invocation — including in a `-p` prompt — is precisely the intended trigger. The transcript shows
the expansion mechanics: the user message becomes
`<command-message>…</command-message>\n<command-name>/echo-test</command-name>\n<command-args>hello-world</command-args>`,
followed by an injected message containing the SKILL.md body and `ARGUMENTS: …`.

Resolution (docs: skills § Where skills live):

| Level | Location | Command |
| --- | --- | --- |
| Personal | `~/.claude/skills/<name>/SKILL.md` | `/name` |
| Project | `.claude/skills/<name>/SKILL.md` (cwd up to repo root) | `/name` |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | `/plugin-name:name` |

- **Precedence on a name clash: personal beats project** ("with a `deploy` skill in both
  `~/.claude/skills/` and your project's `.claude/skills/`, `/deploy` runs the personal one").
  A project skill beats a *bundled* skill of the same name; skills beat `.claude/commands/` files.
- Project skills load from `.claude/skills/` in the starting directory and every parent up to the
  repo root — starting `claude` in a worktree/subdirectory still picks up root-level skills.
- `--add-dir` directories are an exception to "additional dirs grant file access only": their
  `.claude/skills/` (and `.claude/commands/`) *are* loaded. The `permissions.additionalDirectories`
  setting does not load skills.
- `--bare` (recommended for scripted calls, future default for `-p`) skips most auto-discovery but
  "Skills still resolve via /skill-name"; from `--add-dir` dirs it loads `.claude/skills/` but not
  `.claude/commands/`.

## 3. Machine-readable output

`--output-format` (only with `-p`): `text` (default), `json`, `stream-json`.

### `json`
One JSON object on stdout. Observed shape (v2.1.231), key fields:

```json
{
  "type": "result",
  "subtype": "success",            // error subtypes exist, e.g. errors during execution
  "is_error": false,
  "result": "<final assistant text>",
  "session_id": "6fca4763-…",
  "num_turns": 1,
  "stop_reason": "end_turn",
  "terminal_reason": "completed",
  "duration_ms": 2283, "duration_api_ms": 2230,
  "total_cost_usd": 0.0176,
  "usage": { "input_tokens": …, "output_tokens": …, "cache_read_input_tokens": …, … },
  "modelUsage": { "claude-haiku-4-5-20251001": { "costUSD": …, … } },
  "permission_denials": [],
  "uuid": "…"
}
```

With `--json-schema '<schema>'`, a `structured_output` field carries schema-conforming output.
Exit code is 0 on success, non-zero on failure; in-run failures are printed as the result.

### `stream-json`
Newline-delimited JSON events (pass `--verbose`; add `--include-partial-messages` for token
deltas). Observed sequence: optional `rate_limit_event` → `system/init` (session_id, model, full
`tools` list, `slash_commands`, `skills`, `mcp_servers`, `plugins`, `permissionMode`,
`capabilities`) → `system/thinking_tokens` progress events → `assistant` / `user` messages (tool
uses and results as content blocks; subagent messages tagged via `parent_tool_use_id`;
`--forward-subagent-text` adds subagent text) → final `result` line identical in shape to the
`json` output. Also documented: `system/api_retry`, `system/plugin_install`, hook events with
`--include-hook-events`.

### "Ended asking questions" vs "done" — the crucial finding

**In plain `-p` mode there is no built-in "waiting for user input" terminal state.**
Verified locally: the `system/init` tools list in a `-p` run contains **no `AskUserQuestion`
tool** — when prompted to use it, the model reported the tool doesn't exist, asked its question as
plain text, and the run ended with `subtype: "success"`. The docs corroborate: `AskUserQuestion`
is a "tool that requires user interaction" that headless-oriented modes deny (`dontAsk` denies it
even when allow rules match; a `--dangerously-skip-permissions` `-p` run *denies* the few calls
that would still prompt instead of prompting).

So an orchestrator must detect "asked questions" by **contract**, not by exit state. The robust
option is `--json-schema` forcing a status envelope:

```bash
claude -p "…do the thing; if you need answers, stop and list questions…" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"status":{"enum":["done","needs_input"]},"questions":{"type":"array","items":{"type":"string"}},"summary":{"type":"string"}},"required":["status"]}' \
  | jq '.structured_output'
```

Alternatives: a prompt-level marker convention parsed out of `.result`, or the full Agent SDK
(TS/Python), whose streaming-input mode supports interactive tool handling programmatically.

Also useful for a monitoring site: `permission_denials` (what got blocked), `total_cost_usd` and
`modelUsage` (client-side cost estimates, per docs), `usage` tokens, `num_turns`, durations, and
the live `stream-json` event feed itself.

## 4. Transcripts on disk

Docs (sessions § Where transcripts are stored), confirmed locally:

- Path: `~/.claude/projects/<project>/<session-id>.jsonl`, where `<project>` is the session's
  **working directory path with every non-alphanumeric character replaced by `-`**
  (e.g. `/Users/ggs/workspace/auto` → `-Users-ggs-workspace-auto`). Names over 200 chars are
  truncated + hashed. `CLAUDE_CONFIG_DIR` moves the root; `CLAUDE_CODE_PROJECT_DIR_NAME`
  (v2.1.234+, requires `CLAUDE_CONFIG_DIR`) pins the `<project>` name — designed for hosts that
  embed Claude Code, and handy for an orchestrator wanting stable per-agent transcript paths.
- Format: JSONL, one JSON object per line. Observed entry types: `queue-operation`, `user`,
  `assistant`, `attachment`, plus metadata. Message entries carry `uuid`/`parentUuid` (a linked
  chain, which is how forks/branches share history), `sessionId`, `timestamp`, `cwd`, `version`,
  `gitBranch`, `isSidechain`, `isMeta`, and an Anthropic-API-shaped `message`
  (`role`/`content` blocks including `thinking`, `tool_use`, `tool_result`).
- **Caveat the docs state explicitly**: "The entry format is internal to Claude Code and changes
  between versions, so scripts that parse these files directly can break on any release." For the
  website, prefer the supported script interfaces: capture `stream-json` at run time, read the
  `transcript_path` field passed to hooks (a `SessionEnd` hook can archive it), or query a session
  after the fact with `claude -p --resume <id> --output-format json "summarize …"`. If we do parse
  the JSONL (it is the only way to get the full conversation for free), pin the Claude Code version
  and treat parsing defensively.
- Retention: 30 days by default (`cleanupPeriodDays` in settings). `--no-session-persistence`
  suppresses writing for a `-p` run (which also makes it unresumable).

## 5. Unattended runs: permissions, tools, cwd/worktrees

- **Starting mode**: for `-p`, the built-in starting permission mode is Manual (`default`) on
  every plan — an unattended run that hits a permission prompt cannot answer it, so always pass an
  explicit mode or allowlist.
- **Options, least → most permissive** (docs: permission-modes § Common setups):
  - `--allowedTools "Bash(git diff *),Read,Edit"` — pre-approve specific tools; permission-rule
    syntax, trailing ` *` for prefix match. `--disallowedTools` denies/removes; `--tools` restricts
    the available set.
  - `--permission-mode dontAsk` — auto-**denies** anything not in `permissions.allow` rules or the
    read-only command set; never waits for input. Best for locked-down CI. Pair with
    `--allowedTools`.
  - `--permission-mode acceptEdits` — file edits + common fs commands auto-approved; other shell
    commands still need allow rules.
  - `--permission-mode auto` — a classifier model reviews actions instead of a human.
  - `--dangerously-skip-permissions` (≡ `--permission-mode bypassPermissions`) — skips everything,
    including protected-path writes. Deny rules still apply; the handful of calls that would still
    prompt are *denied* in a `-p` run. Refuses to start as root/sudo on macOS/Linux (check skipped
    inside a recognized sandbox). Docs recommend it only inside a container/VM/sandbox runtime.
    Admins can disable it via `permissions.disableBypassPermissionsMode`.
- **Other unattended flags**: `--max-budget-usd <amt>` (spend cap, `-p` only), `--fallback-model`
  (`-p` only), `--bare` (skip host hooks/plugins/CLAUDE.md/MCP for reproducible CI runs; API-key
  auth only), `--no-session-persistence`, `--settings`, `--setting-sources`. Note a non-`--bare`
  `-p` run **skips the workspace trust dialog** and will run a project's `.claude/settings.json`
  hooks and `.mcp.json` servers — only run in trusted directories.
- **cwd / worktree interplay**:
  - The cwd at launch determines the project: which `.claude/` config loads, and which
    `~/.claude/projects/<project>/` directory the transcript lands in. Each git worktree has its
    own path, hence its own transcript directory — but resume-by-id searches worktrees of the same
    repo and (v2.1.223+) all projects, so the orchestrator can resume from anywhere.
  - Project skills resolve from cwd *upward* to the repo root, so launching in a worktree of this
    repo still finds the repo's skills. (Nested subdirectory skills below cwd load lazily.)
  - `--add-dir <dirs...>` grants file read/edit access to extra directories and, exceptionally,
    loads their `.claude/skills/`, `.claude/commands/`, and `.claude/agents/`. Their CLAUDE.md is
    *not* loaded unless `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`. Not restored on resume —
    re-pass it.
  - `claude -w/--worktree [name]` can create a worktree itself, but an orchestrator managing its
    own worktrees should just launch each agent with cwd set inside the target worktree.

## Recommended orchestrator recipe

```bash
# launch an agent step in a worktree, unattended, with a known session id
sid=$(uuidgen | tr 'A-Z' 'a-z')
cd "$WORKTREE" && claude -p "/to-spec $ARGS" \
  --session-id "$sid" \
  --permission-mode acceptEdits --allowedTools "Bash(git *)" \
  --output-format stream-json --verbose > "$LOG/$sid.jsonl"
# next step, same conversation:
cd "$WORKTREE" && claude -p "/to-tickets" --resume "$sid" --output-format json
```

Feed the website from the captured `stream-json` logs (stable, documented interface); fall back to
version-pinned parsing of `~/.claude/projects/<project>/<sid>.jsonl` for full history.
