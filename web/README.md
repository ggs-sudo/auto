# auto · monitor

The monitoring website: variant E from the prototype review (issue #5), built
for real against the generated schemas. Three panes — runs rail, execution
graph centre-stage, session inspector — over the `auto serve` API.

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

## Contracts

`src/types.ts` mirrors `../schemas/*.json`, which are generated from the
harness's Pydantic models (`uv run python -m auto.model.export`). When a
schema changes, update `types.ts` to match.
