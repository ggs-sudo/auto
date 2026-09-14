// PROTOTYPE — throwaway. Shared plumbing for the ?variant= layout round
// (corrections: no prompt block, toggleable runs rail, conversation below
// the graph). Dies with the variants; nothing here is production code.

import { ROOT_KEY, elapsed, graphNodeAt, money, openGates } from "../derive";
import type { RunDetail, RunSummary, SessionRecord, StreamEvent } from "../types";

export interface VariantProps {
  runs: RunSummary[] | null;
  runsError: string | null;
  detail: RunDetail | null;
  detailError: string | null;
  missingSession: string | null;
  runId: string | null;
  selectedNode: string;
  session: SessionRecord | undefined;
  events: StreamEvent[];
  transcriptError: string | null;
  now: number;
  live: boolean;
  everLive: boolean;
  onSelectRun: (id: string) => void;
  onSelectNode: (key: string) => void;
  onAnswered: () => void;
}

/** A takeover consultation record, as `run_detail` now serves them. Typed
 * here rather than in types.ts so the prototype stays self-contained. */
export interface ReconciliationCorrection {
  node: string;
  prior_status: string;
  new_status: string;
  evidence: string;
}

export interface ReconciliationRecord {
  reconciliation_id: string;
  sequence: number;
  effort: string;
  examined: string[];
  verdict: string;
  corrections: ReconciliationCorrection[];
  ticket_corrections?: unknown[];
  model: string;
  session_id: string;
  started_at: string;
  ended_at: string | null;
  prose: string | null;
  tool_calls: { tool: string; arguments: Record<string, unknown>; refused?: string | null }[];
}

/** The takeover trail, present only on runs a takeover has touched. */
export function reconciliationsOf(run: RunDetail): ReconciliationRecord[] {
  const extra = run as RunDetail & { reconciliations?: ReconciliationRecord[] };
  return extra.reconciliations ?? [];
}

export interface InspectedNode {
  type: string;
  title: string;
  sub: string;
  gate: string | null;
}

// Copy of Inspector's private findNode — duplicated on purpose, the
// variants must not force edits on production files beyond one export.
export function inspectNode(run: RunDetail, key: string): InspectedNode | null {
  if (key === ROOT_KEY) {
    const root = run.manifest.root_node;
    return {
      type: root.type ?? "reconciliation",
      title: `root — ${root.type != null ? "the pasted prompt" : "the effort path"}`,
      sub: "lives on the manifest; every graph descends from it",
      gate: root.gate,
    };
  }
  const at = graphNodeAt(run, key);
  if (at == null) return null;
  return {
    type: at.node.ticket_type,
    title: at.node.node_id.replace(/^\d+-/, "").replace(/-/g, " "),
    sub: at.node.ticket,
    gate: at.node.gate,
  };
}

/** Correction 1: the initial user message (the pasted prompt / ticket) is
 * dropped from the chat. It is the first user-typed event when nothing
 * conversational precedes it; later user events are orchestrator messages
 * and stay. */
export function withoutInitialPrompt(events: StreamEvent[]): StreamEvent[] {
  const first = events.findIndex((e) => e.type === "user" || e.type === "assistant");
  if (first >= 0 && events[first].type === "user") {
    return [...events.slice(0, first), ...events.slice(first + 1)];
  }
  return events;
}

/** The run's headline numbers — RunHeader's <dl> without the prompt block,
 * so every variant can place it where its layout wants it. */
export function RunStats({ run, now }: { run: RunDetail; now: number }) {
  const manifest = run.manifest;
  const gates = openGates(run);
  return (
    <dl className="mct-stats">
      <div>
        <dt>status</dt>
        <dd className={`mct-status mct-status--${manifest.status}`}>{manifest.status}</dd>
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
          <span className="mct-sub">· {money(manifest.orchestrator_spend_usd)} orch</span>
        </dd>
      </div>
    </dl>
  );
}

/** Session facts as one inline row — status, turns, spend, elapsed. */
export function SessionFacts({
  session,
  now,
}: {
  session: SessionRecord;
  now: number;
}) {
  return (
    <>
      <span className={`mct-status mct-status--${session.status}`}>{session.status}</span>
      <span>{session.telemetry.num_turns ?? 0} turns</span>
      <span>{money(session.telemetry.cost_usd ?? 0)}</span>
      <span>{elapsed(session.started_at, session.ended_at, now)}</span>
    </>
  );
}
