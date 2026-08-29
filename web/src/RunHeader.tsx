// The run header: identity and prompt on the left; status, node counts and
// spend on the right — and the open gates, full cards, because a partially
// blocked run is still running and a human is the bottleneck. Answering
// happens right here: the card is the response box, whatever the kind.

import { GateCard } from "./GateCard";
import { elapsed, money, openGates } from "./derive";
import type { RunDetail } from "./types";

export function RunHeader({
  run,
  now,
  onSelectNode,
  onAnswered,
}: {
  run: RunDetail;
  now: number;
  onSelectNode: (key: string) => void;
  onAnswered: () => void;
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
            <dd className={gates.length > 0 ? "mct-gatecount" : undefined}>
              {gates.length > 0 ? `${gates.length} open` : "none"}
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
        <section className="mct-gatestrip">
          <span className="mct-gatestrip__label">needs you</span>
          <div className="mct-gatestrip__cards">
            {gates.map((gate) => (
              <GateCard
                key={gate.gate_id}
                run={run}
                gate={gate}
                now={now}
                onAnswered={onAnswered}
                onSelectNode={onSelectNode}
              />
            ))}
          </div>
        </section>
      )}
    </>
  );
}
