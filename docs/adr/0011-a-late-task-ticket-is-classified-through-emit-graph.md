# A task ticket written after the emit is classified through emit_graph, without a completeness demand

#30: a grilling session wrote a new `task` ticket into an effort whose graph
was already emitted. The tick re-derives membership, so the ticket joined the
graph — but a tick derives only what the ticket files say, and a resolution
mode is judgment, not derivation. The orchestrator agent called `emit_graph`
with the classification and was refused for the graph having been emitted
already; the mode was discarded with the refusal, the task sat unclassified
with no entry skill, and the run drained to a failure with dispatchable-in-
principle work still pending.

So `emit_graph` on an effort whose graph the run already holds lands the
given classifications instead of refusing (`GraphStore.classify`). The same
tool in both cases, deliberately: classification has exactly one landing
place, whether the task was in the first derivation or written mid-run. The
node calling it is not linked to the graph — `node.graph` stays untouched, so
one node still spawns at most one graph and the spawned-graph tree stays a
tree. A refusal remains only when there is nothing to land: with no tasks
given, the old message's claim is actually true — membership joins on its
own.

Two asymmetries with `emit` are load-bearing:

- **`emit` demands completeness; `classify` demands none.** At emit the agent
  is reading the entire ticket set, so every `task` can and must be
  classified in one call. Mid-run, interventions on different nodes run side
  by side and each agent knows only the tickets its own session wrote —
  demanding graph-wide completeness would hold one agent's landing hostage to
  a task another agent has yet to judge. The tool's success message lists any
  tasks still unclassified, so nothing goes silently missing.
- **A landed mode is settled.** The same mode again is a no-op; a different
  one is refused, and a refused call changes nothing — not even the modes it
  named first. Re-judging a classification is takeover's business, not a
  later intervention's.

What is given up: a task ticket whose writing session's agent never calls the
tool again still strands, exactly as before — the fix gives the judgment a
landing place, it does not conjure the judgment. The run still ends `failed`
with the node pending, which is the loud version of that outcome.

Amends the **Execution graph** and **Task resolution mode** glossary entries,
which previously tied classification to emit time alone.
