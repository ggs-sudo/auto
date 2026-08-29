// The runs rail: every run in the state directory, newest first. Finished
// and crashed runs list alongside live ones — the server outlives them all.

import { elapsed, money } from "./derive";
import type { RunSummary } from "./types";

export function RunsRail({
  runs,
  selected,
  now,
  live,
  stale,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string | null;
  now: number;
  live: boolean;
  /** The listing failed to refresh while the stream stayed up: what is
   * shown still stands, but it is old news. */
  stale: boolean;
  onSelect: (runId: string) => void;
}) {
  return (
    <aside className="mct-rail">
      <div className="mct-rail__brand">
        auto ▸ <span>monitor</span>
        {!live && <span className="mct-rail__offline"> · reconnecting…</span>}
        {live && stale && <span className="mct-rail__offline"> · listing stale</span>}
      </div>
      {runs.map((run) => {
        const on = run.run_id === selected;
        return (
          <button
            key={run.run_id}
            className={`mct-runcard ${on ? "is-on" : ""}`}
            onClick={() => onSelect(run.run_id)}
          >
            <div className="mct-runcard__top">
              <span className={`mct-dot mct-dot--${run.status}`} />
              <span className="mct-runcard__id">{run.run_id.slice(9)}</span>
              {run.open_gates > 0 && <span className="mct-badge">{run.open_gates}</span>}
            </div>
            <div className="mct-runcard__prompt">{run.prompt}</div>
            <div className="mct-bar">
              <i
                style={{
                  width: `${run.nodes.total ? (run.nodes.done / run.nodes.total) * 100 : 0}%`,
                }}
              />
            </div>
            <div className="mct-runcard__meta">
              <span>{run.route}</span>
              <span>
                {run.nodes.done}/{run.nodes.total}
              </span>
              <span>{money(run.spend_usd)}</span>
              <span>{elapsed(run.created_at, run.ended_at, now)}</span>
            </div>
          </button>
        );
      })}
    </aside>
  );
}
