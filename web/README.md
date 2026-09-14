# auto · monitor

The monitoring website, over the `auto serve` API: a toggleable runs rail
beside one scrolling column — run header, open gates, the execution graph
centred (LED status lights; takeover consultations as disconnected nodes),
and the selected node's history panel underneath. The panel carries a node's
two histories behind a tab pair — the skill session's transcript, rendered
Claude Code-style, and the orchestrator's interventions on the node. Layout
settled by the prototype rounds in `../prototype/monitoring-website*/`.

## Production

The site ships prebuilt inside the Python package, so `auto serve` needs no
node toolchain at runtime:

```bash
npm install
npm run build     # writes into ../src/auto/web/static/, which is checked in
```

Rebuild and commit `src/auto/web/static/` whenever the site changes.

## Development

Two workflows, same endpoints either way:

```bash
# terminal 1 — the API (generate a fixture first if you have no runs)
uv run python -m auto.fixture /tmp/auto-fixture
uv run auto serve --state-dir /tmp/auto-fixture/state

# terminal 2 — the site, with HMR
npm run dev       # Vite on :5173, /api proxied to auto serve on :2886
```

Or the other direction: `auto serve --dev` proxies pages to Vite (default
`http://localhost:5173`) so everything is on one origin; HMR's websocket goes
straight to Vite's port.

If filesystem watching misbehaves, `auto serve --poll` polls instead — the
documented fallback, behind identical endpoints.

## Tests

```bash
npm test          # vitest: pure derivations, routing, layout, components
```

The suite runs under jsdom. Pure seams (`derive.ts`, `router.ts`,
`layout.ts`) are tested directly; the shell's states — routing, errors,
reconnection — through rendered components with a stubbed server.

## Contracts

`src/types.ts` mirrors `../schemas/*.json`, which are generated from the
harness's Pydantic models (`uv run python -m auto.model.export`). When a
schema changes, update `types.ts` to match.
