# Errors

## 1. Agent Spawns in auto/ repo not in working repo !!

Ticket 03 is resolved, but this agent committed its findings branch to the wrong repo (auto, where its worktree lived) instead of ai-new. I'll port the doc onto a proper research/headless-claude-and-backends branch in ai-new, then clean up.

This is a harness failure point, not a one-off: nothing guarantees a driven session's working directory is the target repo, so its file writes and commits can land wherever its process happened to start. The harness must ensure (and verify) that every dispatched session executes in the target repo. Until fixed, "repo is ground truth" checks (takeover) can miss work that landed elsewhere.

## 2. Website mislabels injected skill text as "orchestrator"

`web/src/derive.ts:222-223` labels any user-role text block "orchestrator", assuming user-role text in a driven session can only come from the orchestrator. But the Skill tool expands a skill by injecting its SKILL.md instructions into the conversation as a user-role text message, so skill instruction bodies (e.g. research, grilling) render in the transcript view as if the orchestrator wrote them. Fix: distinguish injected skill/command text from real orchestrator messages and label it "skill" (or similar).

## 3. Website renders every `system` event as "session started"

`web/src/derive.ts:196-204` maps every `type: "system"` stream-json event to a "session / started" line, but only `subtype: "init"` actually marks a session start. A real transcript (session 80e39a5c) had 1 `init` against 93 `thinking_tokens`, 15 `task_progress`, 4 `task_started`, and 4 `background_tasks_changed` — ~116 spurious "session started" lines. Fix: filter on `subtype === "init"` and hide or distinctly label the other system subtypes.
---

# Backlog

Ideas and tasks deferred until after the first working implementation of the harness. One entry per item; promote an entry to a Wayfinder map or feature run when its time comes.

## 1. Self-validation loop (Ralph-style)

After the harness finishes implementing a feature, add a validation phase controlled by the harness:

1. The user writes a **validation rubric**: a list of concrete points the implementation must satisfy (behaviors to exercise, commands to run, outcomes to check).
2. The harness executes the rubric against the target repo and collects results.
3. Any failing point is reported as an error **in the target repo's tracker** and handed to a fixer agent that attempts to resolve it.
4. The harness re-runs the rubric after the fix.
5. Loop (run rubric → dispatch fixer → re-run) until the rubric passes clean.

This is the "Ralph" pattern (a circulating verify-fix loop); we don't need any particular existing tool for it, just the loop mechanism, driven by `claude -p` like everything else. Needs an iteration cap / escalation path so a stuck loop pings the user via the monitoring website instead of spinning forever.

## 2. Resumable runs (crash recovery)

The orchestrator is a long-running process for a run's whole lifetime, and sessions blocked on a gate stay alive inside a blocked `ask` tool call (see ADR-0001 and the issue #3 schema). If the machine or the orchestrator dies mid-run, the run dies with it. Add recovery: on restart, rebuild in-flight state from the run dir (manifest, session records, graph, open gates) and re-enter or re-dispatch interrupted sessions via `--resume`. Deliberately not built into the MVP — the schema (statuses, one-writer files, derived readiness) was designed so this can be layered on later.

## 3. Implement-only entry point

Today the harness always enters through the question phase (Wayfinder route or grill route). Add a third route for when the user has already done the grilling / wayfinding themselves and tickets already exist in the target repo's tracker (`.scratch/<feature>/issues/`): invoke `auto` in implement-only mode, point it at the feature's tickets, and the harness skips straight to execution — parse the execution graph, dispatch implementation sessions respecting `Blocked by:` edges, monitor, and start the next ticket as soon as one finishes. No question-answering at all; the orchestrator is purely dispatcher + monitor.

## 4. Harness observability: an event log for orchestrator state transitions

The harness runs as a black box: it's hard to tell what state the orchestrator is in at any moment, and its own actions leave no trace. For example, when the orchestrator jumps in on a running session (an intervention, a poll picking up a stale turn, a dispatch), no event is recorded — it just happens, and afterwards there's no way to see *when* it happened or *why*. Add an orchestrator event log to the run dir (e.g. `events.jsonl`): one timestamped entry per state transition and per orchestrator action (session dispatched, turn went stale, intervention triggered and its reason, gate opened/answered, session ended), so both the monitoring website and `auto show` can surface a live timeline of what the harness is doing and why.


