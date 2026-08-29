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
