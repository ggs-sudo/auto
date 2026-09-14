# A turn boundary is not a stale point while background work is outstanding

The loop reads a session's stream to its `result` event, calls that a **stale
point**, and hands the moment to an ephemeral agent. An agent that calls no
tool ends the node, because — the loop reasoned — nothing wakes an idle
headless session, so no next stale point is coming.

That reasoning was wrong, and run `20260914-142308-thread-scoped-tool-part-ids`
is what it cost. Nodes 03 and 04 both ended a turn having launched two review
subagents with the `Agent` tool and not waited for them. The CLI answered each
launch with *"the agent is working in the background. You will be notified
automatically when it completes."* Each session then said so and ended its
turn. The harness read the `result`, invoked an agent, got an honest "the
session is still working, nothing needed from me", and failed the node — nine
seconds after the boundary for 03, nineteen for 04. Ticket 04's feature was
already committed; ticket 03's was built and tested. Both nodes died owing
nothing but a `Status:` line.

`result` is the boundary of a **turn**, which is not the boundary of a
**session**. Driven sessions launch with `--input-format stream-json` precisely
so the process outlives its first result, and a background task completing
re-invokes the model on that same open stream with no input from the harness.
Reproduced against a live CLI: an `Agent` launched and not waited for, a turn
ended, nothing sent — and the session woke itself twice, 21s and 35s later,
finishing with the subagent's answer. The CLI's state reporting was never the
problem; the harness's inference from it was.

So staleness is a turn boundary **with nothing left running**. The session says
what is running, in `background_tasks_changed`, which carries the whole live
set every time it fires and empties out when the set drains. A `result` that
arrives with that set non-empty is held rather than judged, and the loop keeps
reading. Nothing else is needed: the wake-up is the CLI's to deliver and the
harness's only job is to still be listening. The evidence for the rule is the
run that motivated it — the two nodes it saves both had two `local_agent` tasks
outstanding at the boundary that killed them, and every boundary the same run
judged correctly had none.

Two waits bound it, because a rule the harness cannot verify needs a floor:

- **A held boundary is conceded after `BACKGROUND_GRACE_SECONDS` of total
  silence.** A task that reports to nobody would otherwise strand its node for
  the whole run. Generous on purpose — a live subagent keeps the stream warm
  with `task_progress` every few seconds, so what this really bounds is a
  backgrounded command whose wall time is the test suite's.
- **A no-op intervention gets `IDLE_GRACE_SECONDS` before it fails the node.**
  With the rule above in place the session really is idle when an agent is
  invoked, so this is not expected to be reached. It is the insurance on the
  loop's model of wake-ups being *incomplete* rather than merely wrong: a
  mechanism the harness cannot see costs a minute of waiting, where being wrong
  about it costs the node.

## Consequences

- **The orchestrator agent is never invoked about a session that has work
  running**, so "it is still working" stopped being a reading its prompt has to
  allow for. The prompt says so, and says what a silent intervention now costs.
- **A held turn is not absorbed**, so its telemetry does not land on the
  session record and its reported cost is not added to the run's driven spend.
  The record ends up carrying the turn the node was actually judged on.
- **The harness waits in wall-clock time, on purpose.** Neither grace is a
  quiet-period heuristic standing in for a signal: every decision is still made
  on events the CLI sent, and the clock only decides how long to keep listening
  for one.
- **Reading must be interruptible without being cancellable.** The loop has to
  stop waiting to notice an abort or count silence, while still meaning to read
  the same event afterwards — the codec accumulates a long line across several
  awaits, so a cancelled read can drop what it had already taken off the pipe.
  `_StreamReader` starts the read once, shields it, and keeps it; a wait that
  expires abandons the wait and never the read.
- **The replay launcher models the wake-up too.** A recorded turn that ends
  with background work outstanding is followed by the next turn with nothing
  sent, because a replay that needed a message to continue would hide the very
  bug this decision is about.
