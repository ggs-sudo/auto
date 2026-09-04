# Takeover consultations are served on effort-scoped URLs, from the same endpoint

ADR-0003 scopes an orchestrator intervention to one node by addressing: the inline MCP config's URL carries the run and the node, no tool takes a node argument, and only an address with an open window answers. Takeover extends the same mechanism one level up rather than inventing a second one. A takeover consultation's tools are served at `/runs/<run-id>/efforts/<effort>/mcp` — the effort under reconciliation in the address, a window opened for exactly one ephemeral agent, and no `effort` argument on any tool. An agent cannot judge an effort it was not invoked about, because the address that would let it does not exist.

What differs is the roster behind the address. A window now carries its own tool roster: node addresses serve the node tools, effort addresses serve the takeover tools (today just `report_effort_clean`), and `tools/list` answers per window. The verdict that lets a takeover resume execution is therefore a recorded tool call over the same plumbing as every orchestrator judgment — validated arguments, refusals recorded, prose inert — which holds the state-repair agent to at least the standard of the orchestrator agent.

## Consequences

- **One endpoint, two scopes.** The hand-written server from ADR-0003 gained a path scheme and a roster field, not a sibling. Window discipline is shared: outside the block the URL 404s, and a second consultation on an open effort is refused.
- **The clean verdict is state, not prose.** Takeover resumes only after `report_effort_clean` lands; an agent that describes drift in prose and calls nothing stops the takeover, and its account survives in the numbered reconciliation record.
- **The consultation is read-only outside its verdict.** Same allowlist shape as an intervention: the takeover tools plus `Read`, `Glob`, `Grep`. The repo is ground truth and the agent must be able to read it; it cannot correct anything yet — corrections are future tools on this same address.
- **Future drift-correction tools have a home.** Resetting a lying node, annotating a ticket, aborting an orphan run: each lands as another entry in the takeover roster, scoped by the same URL, with nothing new to build in the transport.
