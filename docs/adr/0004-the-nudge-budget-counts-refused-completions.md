# The nudge budget counts refused completions, not every stale point

The harness verifies after the fact that a node left behind the tracker files
its type owes, nudges the session when it did not, and fails the node when
nudging stops working. #11 sizes that patience as **three consecutive stale
points at which the owed set did not shrink**.

Counting *every* stale point that way kills the thing the rule protects. A
monitored session goes stale once per question, and a grilling or wayfinder
conversation owes nothing on disk until its questions are over — so the owed
set cannot shrink for as many turns as the interview takes, and an interview
longer than three questions would fail mid-conversation for doing exactly what
it was dispatched to do.

So the count is scoped to the stale points where the harness had to **refuse a
completion**: the agent read the trace, judged the session finished, and the
repo could not back that up. Those are the moments the ticket calls *mid-nudge*
— the only ones where the harness and the session are known to disagree about
whether the work is done, and the only ones a nudge is aimed at. A session
still being interviewed is working, not stalling, and nothing counts against
it.

## Consequences

- **The first refusal is already the first no-shrink stale point.** The owed
  set is measured once at dispatch, so a session that returns having written
  nothing has not shrunk anything. Three consecutive refusals therefore fail
  the node, which is the acceptance criterion as written and two delivered
  nudges rather than ADR-0002's looser "up to three".
- **`research` and `prototype` cost exactly one nudge cycle**, which is the
  running cost ADR-0002 accepted: they arrive owing everything, are refused
  once, are told the convention, and complete on the next stale point.
- **A node whose agent never tries to complete it is never counted against.**
  Patience stays expressible, and that is the accepted hole: a session that
  neither finishes nor claims to is bounded by the run and session spend
  ceilings, not by this budget.
- **The budget is the loop's arithmetic, not the agent's judgment.** The agent
  is never told the count and has no tool for spending it; `fail_node` is for
  work it judges impossible, which is a different thing from work that keeps
  not getting written down.
- **The failing stale point still gets its intervention.** The loop only learns
  a stale point was a nudge point by running it, so the third refusal may
  arrive alongside a message the agent had already sent. That message is inert:
  the node is failed and the session comes down with it.
