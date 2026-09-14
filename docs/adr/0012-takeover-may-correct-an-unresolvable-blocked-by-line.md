# Takeover may correct an unresolvable `Blocked by:` line — the second amendment to the tracker-file rule

ADR-0010 amended the tracker-file rule for one role and one line, and said in as many words that a ticket's `Blocked by:` has no write path at all. This ADR amends that rule a second time, for the same role and one more line, because the first-sight derivation can import a dependency map that no session can ever finish.

The graph's edges are parsed out of each ticket's `Blocked by:` line, and a reference the parser cannot reduce to a stem is deliberately *kept* rather than dropped — a ticket blocked by something the graph cannot find is blocked, not free, because the named ticket may simply not have been written yet. That rule is right for a ticket still to come and wrong for a reference that will never resolve. A session that writes `Blocked by: 01 (metadata helper)` where the stem is `01-metadata-helper` has named a ticket that is right there, but the derivation resolves it to nothing, an unresolved blocker is never satisfied, and the node can never dispatch. The whole effort renders as disconnected tasks and stalls the moment its unblocked nodes finish. Nothing in the run can recover from this: every tick re-derives the same unresolvable line from the same ticket.

Loosening the parser was the obvious alternative and is the wrong place for the repair. Whatever shape it learns to accept, the next one is a session's free prose away, and each loosening silently reinterprets edges in efforts nobody is looking at. Worse, it cannot tell the two cases apart — a reference the parser should have resolved and a reference to a ticket that genuinely does not exist yet look identical to a regular expression. That is a judgment, and judgment is what the takeover role exists for.

So the takeover agent gets `correct_ticket_blockers`, an effort-scoped tool (ADR-0009) that rewrites a ticket's `Blocked by:` line to the stems the agent names, appends the same one-line reconciliation note ADR-0010 established, and re-derives the graph snapshot so the persisted graph draws the corrected edges. The harness gathers the drift deterministically — every blocker resolving to no node is named in the reconciliation evidence — and the prompt charges the agent with checking that the map between tickets and graph nodes is one-to-one. The scope is enforced in the tool layer, not asked for in the prompt:

- Every blocker named must be a node of the *same* graph. Dependencies never cross graph boundaries, and a correction cannot be the thing that invents one.
- A node blocking on itself is refused, and so is any correction that would close a dependency cycle: the corrected graph must stay a DAG, or the correction has traded a stalled node for a stalled cycle.
- A ticket with no `Blocked by:` line at all is refused — the derivation read it as unblocked, so there is no reference to correct.
- An empty list is allowed and writes `None (can start immediately)`: the right repair when the reference named work that was never ticketed and never will be.
- The correction lands in the reconciliation record with the prior line verbatim, the new stems and the evidence, under a `corrected` verdict, exactly as graph and `Status:` corrections do.

## Consequences

- **The tracker-file rule now reads:** tracker files are what sessions wrote, except that the takeover role may correct a ticket's `Status:` line and its `Blocked by:` line and append a reconciliation note — nothing else, and no other role. The orchestrator's own prohibition (ADR-0002) is untouched.
- **The parser stays strict and stays honest.** Keeping an unresolvable reference blocked is still right, because the case it was wrong for now has a designated remedy.
- **An unfinishable graph is caught before anything resumes.** Reconciliation is the only moment in a run where the dependency map is judged rather than derived, so this is the only place the check can live.
- **The correction is a repair, not a redesign.** The tool cannot tell an agent rewiring an effort to its own taste from one fixing a typo, so the prompt draws that line and the record makes every correction reviewable after the fact.
