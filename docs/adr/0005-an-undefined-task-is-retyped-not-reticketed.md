# An undefined task re-types its node in place; no grilling ticket is written

#12 says an undefined `task` ticket "resolves immediately by emitting a
grilling ticket in its place". Read literally, that has the harness writing a
new tracker file — and ADR-0002 already settled that the orchestrator never
writes tracker files, not even to rescue a node. The two cannot both hold, and
ADR-0002 is the more load-bearing rule: verification-after-the-fact only works
if the repo's tracker files are, without exception, what sessions wrote.

So "in its place" is implemented as re-typing rather than re-ticketing: when
the orchestrator agent classifies a task as `undefined` at emit time, the
node's entry skill becomes `grill-with-docs` and its dispatch points the
grilling session at the *same* ticket. The grilling ticket the spec speaks of
is this node, re-typed — the milestone's own file is the material the grilling
session interviews over, and the spec and tickets that session writes land in
a fresh effort directory as its subgraph, exactly as they would have from a
separate ticket.

What is given up: the tracker never records that a substitution happened — a
reader of the effort directory sees a `task` ticket that a grilling session
answered. The graph file records the classification (`task_mode: undefined`),
which is where the harness's view of the substitution lives.

Amends the **Task resolution mode** glossary entry, which previously used the
spec's "emitting a grilling ticket" phrasing.
