// The runs rail: every run in the state directory, newest first. Finished
// and crashed runs list alongside live ones — the server outlives them all.
//
// A run can also be deleted from here, which is the only destructive thing
// the site does: the ✕ arms an inline confirmation on that card rather than
// a modal, so a mis-click costs a second click and nothing else, and a
// refusal — a run a live orchestrator still holds — is shown on the card it
// belongs to instead of vanishing.

import { useState } from "react";
import { elapsed, money } from "./derive";
import type { RunSummary } from "./types";

export function RunsRail({
  runs,
  selected,
  now,
  live,
  stale,
  onSelect,
  onDelete,
}: {
  runs: RunSummary[];
  selected: string | null;
  now: number;
  live: boolean;
  /** The listing failed to refresh while the stream stayed up: what is
   * shown still stands, but it is old news. */
  stale: boolean;
  onSelect: (runId: string) => void;
  /** Erase a run. Rejects with the server's reason when it is refused. */
  onDelete: (runId: string) => Promise<void>;
}) {
  // Which card is asking, which is mid-delete, and what the server last said
  // about one — all about a single card at a time, so one slot each.
  const [arming, setArming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ runId: string; reason: string } | null>(null);

  const confirm = (runId: string) => {
    setDeleting(runId);
    setFailure(null);
    onDelete(runId)
      .then(() => setArming(null))
      .catch((err: unknown) => {
        setArming(null);
        setFailure({
          runId,
          reason: err instanceof Error ? err.message : String(err),
        });
      })
      .finally(() => setDeleting(null));
  };

  return (
    <aside className="mct-rail">
      <div className="mct-rail__brand">
        auto ▸ <span>monitor</span>
        {!live && <span className="mct-rail__offline"> · reconnecting…</span>}
        {live && stale && <span className="mct-rail__offline"> · listing stale</span>}
      </div>
      {runs.map((run) => {
        const on = run.run_id === selected;
        const short = run.run_id.slice(9);
        return (
          <div className="mct-runrow" key={run.run_id}>
            <button
              className={`mct-runcard ${on ? "is-on" : ""}`}
              onClick={() => onSelect(run.run_id)}
            >
              <div className="mct-runcard__top">
                <span className={`mct-dot mct-dot--${run.status}`} />
                <span className="mct-runcard__id">{short}</span>
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
            {arming !== run.run_id && (
              <button
                className="mct-runrow__x"
                aria-label={`delete run ${run.run_id}`}
                title={`delete run ${run.run_id}`}
                onClick={() => {
                  setArming(run.run_id);
                  setFailure(null);
                }}
              >
                ✕
              </button>
            )}
            {arming === run.run_id && (
              <div className="mct-runrow__confirm">
                <span>delete this run?</span>
                <button
                  className="mct-runrow__go"
                  disabled={deleting === run.run_id}
                  onClick={() => confirm(run.run_id)}
                >
                  {deleting === run.run_id ? "deleting…" : "delete"}
                </button>
                <button
                  className="mct-runrow__no"
                  disabled={deleting === run.run_id}
                  onClick={() => setArming(null)}
                >
                  cancel
                </button>
              </div>
            )}
            {failure?.runId === run.run_id && (
              <div className="mct-runrow__failed" role="alert">
                ⚠ {failure.reason}
              </div>
            )}
          </div>
        );
      })}
    </aside>
  );
}
