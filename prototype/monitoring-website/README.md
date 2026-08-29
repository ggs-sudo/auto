# PROTOTYPE — monitoring website UI

Throwaway. Answers one question, from [issue #5](https://github.com/ggs-sudo/auto/issues/5):

> **What should the monitoring website look and feel like?**

Five UIs over the same mocked run state, on one route, switchable via
`?variant=` and the floating bar at the bottom (`←`/`→` also work).

```bash
npm install
npm run dev      # http://localhost:5178/?variant=A
```

| | Variant | The bet it makes |
|---|---|---|
| **A** | Mission control | The **execution graph** is the run. Three panes: runs rail, DAG with drawn dependency edges, session inspector. Everything visible at once. |
| **B** | Terminal log | Design borrowed from `~/workspace/agent-benchmarks/view/results.html`: one 880px monospace column, near-black, every row expands. Runs → nodes → conversation, nested. Tabs for runs / gates / sessions. |
| **C** | Timeline | The run as a **time axis**. One swimlane per node, bars for sessions, gates as vertical markers where the run stopped for a human, conversation in a bottom drawer. Shows parallelism and dead time. |
| **D** | Gate inbox | An unattended harness only needs you when it's stuck, so **what needs you is the page**. Light, reading-first triage queue; the run is browsable behind it. |
| **E** | Mission control · terminal skin | A's three-pane layout wearing B's skin — the agent-benchmarks palette and monospace type, flat rows instead of cards. Added after the first review round. |

## What's mocked

`src/mock/` holds one fixture run shaped like the run-state schema settled in
[#3](https://github.com/ggs-sudo/auto/issues/3) (+ the #4 amendment) — `run.json` manifest, two
graphs (`onboarding-revamp` and the `invite-seats` subgraph it spawned), nine session records,
four gates (prototype-review, escalated-question, task-completion, an answered ping), and per-session
transcripts including orchestrator interventions.

- **No persistence.** Gate responses live in React state and vanish on reload — the point is what
  responding *looks like*, not that it works.
- **Live-ish** is a 2.2s timer in `src/mock/live.ts` appending canned events to the running sessions
  and advancing a simulated clock. The real thing tails transcripts; the transport is still open (#1).
- **Readiness is derived** (`pending` + all deps `done`), never stored, matching the schema.

`src/mock/` and `src/PrototypeSwitcher.tsx` are shared; every variant owns its own layout and CSS
outright, so none of them are constrained by the others.

## Verdict

**E wins** — A's three-pane layout (runs rail → execution graph → session inspector), wearing B's
terminal skin (agent-benchmarks palette, monospace, flat rows). A's information architecture beat the
alternatives because the graph *is* the run; B's skin beat A's because this is a tool you stare at
while something else works.

## After a variant wins

Fold the winner into the real website properly (this code has no tests, no error handling, no
routing). The full variant set is the primary source: it goes to a throwaway branch, not to main.
