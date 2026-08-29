// The run header: identity and prompt on the left; status, node counts and
// spend on the right — and the gate strip, because a partially blocked run is
// still running and the gate badge is the only thing that says a human is
// the bottleneck. Counts and gates are visible at the same time, always.

import { GATE_LABEL, elapsed, money, openGates } from "./derive";
import type { RunDetail } from "./types";

export function RunHeader({
  run,
  now,
  onSelectNode,
}: {
  run: RunDetail;
  now: number;
  onSelectNode: (key: string) => void;
}) {
  const manifest = run.manifest;
  const gates = openGates(run);
  return (
    <>
      <header className="mct-head">
        <div>
          <h1>{manifest.run_id}</h1>
          <p className="mct-head__prompt">{manifest.prompt}</p>
        </div>
        <dl className="mct-stats">
          <div>
            <dt>status</dt>
            <dd className={`mct-status mct-status--${manifest.status}`}>
              {manifest.status}
            </dd>
          </div>
          {manifest.phase != null && (
            <div>
              <dt>phase</dt>
              <dd>{manifest.phase}</dd>
            </div>
          )}
          <div>
            <dt>elapsed</dt>
            <dd>{elapsed(manifest.created_at, manifest.ended_at, now)}</dd>
          </div>
          <div>
            <dt>nodes</dt>
            <dd>
              {run.nodes.done}/{run.nodes.total}{" "}
              <span className="mct-sub">
                · {run.nodes.running} live
                {run.nodes.failed > 0 && ` · ${run.nodes.failed} failed`}
              </span>
            </dd>
          </div>
          <div>
            <dt>gates</dt>
            <dd className={run.open_gates > 0 ? "mct-gatecount" : undefined}>
              {run.open_gates > 0 ? `${run.open_gates} open` : "none"}
            </dd>
          </div>
          <div>
            <dt>spend</dt>
            <dd>
              {money(manifest.driven_spend_usd + manifest.orchestrator_spend_usd)}{" "}
              <span className="mct-sub">
                · {money(manifest.orchestrator_spend_usd)} orch
              </span>
            </dd>
          </div>
        </dl>
      </header>

      {gates.length > 0 && (
        <div className="mct-gatestrip">
          <span className="mct-gatestrip__label">needs you</span>
          {gates.map((gate) => (
            <button
              key={gate.gate_id}
              className={`mct-gatechip mct-gatechip--${gate.kind}`}
              onClick={() => gate.node != null && onSelectNode(gate.node)}
            >
              #{gate.sequence} {GATE_LABEL[gate.kind]}
              <span>· waiting {elapsed(gate.raised_at, null, now)}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
