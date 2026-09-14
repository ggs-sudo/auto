# Takeover may correct a lying ticket `Status:` line — the one amendment to the tracker-file rule

ADR-0002 settled that the orchestrator never writes tracker files, and ADR-0005 leaned on the strong form of that rule: verification-after-the-fact only works if the repo's tracker files are, without exception, what sessions wrote. This ADR amends that rule for exactly one role and one line.

The reason is the first-sight import rule, which stands unchanged: a ticket already closed out when the harness first sees it imports as `done` — that is what lets legitimately pre-done work count. When such a line lies (the reference corruption: five nodes imported as `done` with no session behind them), resetting the graph node is not enough. Graph files are harness state and takeover corrects them freely, but the graph is a *derived* record: any from-scratch re-derivation reads the tickets at first sight again and re-imports the same lie. The lie lives in the ticket, so the correction must too.

So the takeover agent gets `correct_ticket_status`, an effort-scoped tool (ADR-0009) that rewrites a ticket's closed-out `Status:` line to an open value the agent names and appends a one-line reconciliation note saying what changed and on what evidence. The scope is enforced in the tool layer, not asked for in the prompt:

- Only the `Status:` line is rewritten — every closed-out one, since first-sight import matches anywhere in the file — and its formatting (bare or bolded) is preserved. The ticket's content and `Type:` have no write path at all. (`Blocked by:` had none either when this was written; ADR-0012 later gave it one, for the same role and on the same terms.)
- A value that would close a ticket out is refused: takeover reopens lying tickets; only a session doing the work closes one. Done-ness is never minted by the harness — a reset node re-dispatches, and the session that finishes the work closes the ticket itself.
- A ticket with no closed-out `Status:` line is refused: an open ticket tells no lie a first-sight derivation could import, so there is nothing to correct.
- The appended note is a single line with its evidence collapsed to spaces, so a correction cannot smuggle a fresh line-anchored `Status:` (or anything else) into the ticket.
- The correction lands in the numbered reconciliation record with the prior value, the new value and the evidence, under a `corrected` verdict, exactly as graph corrections do.

## Consequences

- **The tracker-file rule now reads:** tracker files are what sessions wrote, except that the takeover role may correct a ticket's `Status:` line and append a reconciliation note — nothing else, and no other role. (ADR-0012 adds the `Blocked by:` line to that exception.) The orchestrator's own prohibition (ADR-0002) is untouched.
- **First-sight import stays trustworthy without being changed.** The import rule keeps its simplicity because takeover is the designated remedy for the case where it lies.
- **A reader of the ticket sees the correction happened.** The appended note is the in-ticket account; the reconciliation record is the audit trail with the full evidence.
- **A session redoing the work overwrites the note** when it rewrites the ticket to close it out again — which is fine: by then the line is true, and the record still holds the history.
