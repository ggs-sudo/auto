# PROTOTYPE — monitor layout corrections (round 2)

Throwaway. The second UI prototype round, iterating on the shipped variant E
layout (see `../monitoring-website/`). Ran as `?variant=` layouts inside the
real `web/` app against live `auto serve` data; the files here are the
variants lifted out of `web/src/prototype/` after the fold-in, kept as the
primary source. They do not compile standalone — they imported the real
app's components.

## The question

Three corrections to the shipped layout, and how the corrected layout should
be structured:

1. Don't render the pasted prompt in the run view.
2. The runs rail should toggle.
3. The session chat belongs below the graph, not in a side pane.

| Variant | The bet it makes |
|---|---|
| **A — flow** | One scrolling document: header → gates → graph → session, full width. Rail toggles fully away. |
| **B — console** | Fixed split panes; the chat is a drawer pinned under the graph with height presets; rail collapses to a dot strip. |
| **C — tabs** | Graph on top; the chat wears a session tab strip; rail behind a hamburger overlay. |

## Verdict

**A wins**, then took three further iterations before the fold-in:

- Session panel carries a **session | orchestrator tab pair** — a node's two
  histories (its own transcript; the orchestrator's interventions on it).
- The transcript renders **Claude Code-style** (assistant prose bulleted,
  user messages quoted, tool calls as one-liners, tool results only when
  errored, turn ends as hairlines) and drops injected skill texts and the
  initial prompt.
- Graph centred; **LED status lights** on node cards (red failed / orange
  running / green done / blue pending).
- **Takeover consultations render as disconnected nodes** — one per
  reconciliation record, since one effort can be taken over many times —
  gated on the run actually having been taken over. This forced the one API
  change of the round: `run_detail` now serves `reconciliations`.

Folded into `web/src/` (App, SessionPanel, GraphBoard, RunHeader, derive,
types, app.css) with tests.
