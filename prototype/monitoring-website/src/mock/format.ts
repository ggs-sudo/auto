// PROTOTYPE — shared formatting + derivations. Shared on purpose: this is data, not layout.
import type { Gate, Graph, GraphNode, RunDetail } from "./schema";

export const clock = (iso: string) => iso.slice(11, 16);
export const day = (iso: string) => iso.slice(5, 10).replace("-", "/");
export const stamp = (iso: string) => `${day(iso)} ${clock(iso)}`;

export function elapsed(fromIso: string, to: Date | string | null): string {
  const end = to == null ? Date.now() : typeof to === "string" ? Date.parse(to) : to.getTime();
  const s = Math.max(0, (end - Date.parse(fromIso)) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${(m % 60).toString().padStart(2, "0")}`;
}

export const money = (n: number) => `$${n.toFixed(2)}`;

export const GATE_LABEL: Record<Gate["kind"], string> = {
  "prototype-review": "prototype review",
  ping: "ping",
  "escalated-question": "escalated question",
  "task-completion": "task completion",
};

export const openGates = (run: RunDetail) => run.gates.filter((g) => !g.response);

export const allNodes = (run: RunDetail): (GraphNode & { graph_id: string })[] =>
  run.graphs.flatMap((g) => g.nodes.map((n) => ({ ...n, graph_id: g.graph_id })));

export const nodeKey = (graphId: string, nodeId: string) => `${graphId}/${nodeId}`;

/** Readiness is always derived, never stored (issue #3). */
export function isReady(graph: Graph, node: GraphNode): boolean {
  if (node.status !== "pending") return false;
  return node.deps.every((d) => graph.nodes.find((n) => n.id === d)?.status === "done");
}

export function gateFor(run: RunDetail, graphId: string, nodeId: string): Gate | undefined {
  return run.gates.find((g) => !g.response && g.graph_id === graphId && g.on_node === nodeId);
}

export function sessionFor(run: RunDetail, node: GraphNode) {
  return node.session_id ? run.sessions.find((s) => s.session_id === node.session_id) : undefined;
}

/** Nodes held up by an open gate, as node keys. */
export function blockedKeys(run: RunDetail): Set<string> {
  const out = new Set<string>();
  for (const g of openGates(run)) {
    for (const b of g.blocking) out.add(nodeKey(g.graph_id ?? "", b));
  }
  return out;
}

export const PROGRESS = (run: RunDetail) => {
  const nodes = allNodes(run);
  return {
    total: nodes.length,
    done: nodes.filter((n) => n.status === "done").length,
    running: nodes.filter((n) => n.status === "in-progress").length,
    pending: nodes.filter((n) => n.status === "pending").length,
    failed: nodes.filter((n) => n.status === "failed").length,
    cost: run.sessions.reduce((a, s) => a + s.cost_usd, 0),
    turns: run.sessions.reduce((a, s) => a + s.num_turns, 0),
  };
};
