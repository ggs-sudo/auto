// Derivations over run state. Shared on purpose: this is data, not layout.
// Readiness and blockedness are derived, never stored — the same stance the
// harness takes (schemas carry status and edges, nothing else).

import type {
  Gate,
  Graph,
  GraphNode,
  InterventionRecord,
  RunDetail,
  SessionRecord,
  StreamEvent,
} from "./types";

export const nodeKey = (graphId: string, nodeId: string) => `${graphId}/${nodeId}`;
export const ROOT_KEY = "root";

export const money = (n: number) => `$${n.toFixed(2)}`;

export const clock = (iso: string) => iso.slice(11, 16);

export function elapsed(fromIso: string, toIso: string | null, now: number): string {
  const end = toIso == null ? now : Date.parse(toIso);
  const s = Math.max(0, (end - Date.parse(fromIso)) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h${(m % 60).toString().padStart(2, "0")}`;
  return `${Math.floor(h / 24)}d`;
}

export const GATE_LABEL: Record<Gate["kind"], string> = {
  "prototype-review": "prototype review",
  "task-completion": "task completion",
  "escalated-question": "escalated question",
  "user-ping": "ping",
};

export const openGates = (run: RunDetail): Gate[] =>
  run.gates.filter((g) => g.response == null && g.answered_at == null);

export function gateById(run: RunDetail, gateId: string | null): Gate | undefined {
  return gateId == null ? undefined : run.gates.find((g) => g.gate_id === gateId);
}

export function sessionById(
  run: RunDetail,
  sessionId: string | null,
): SessionRecord | undefined {
  return sessionId == null
    ? undefined
    : run.sessions.find((s) => s.session_id === sessionId);
}

/** Layer nodes by dependency depth — the drawn left-to-right axis. */
export function layersOf(nodes: GraphNode[]): GraphNode[][] {
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const at = (n: GraphNode): number => {
    const known = depth.get(n.node_id);
    if (known !== undefined) return known;
    if (visiting.has(n.node_id)) return 0; // a cycle in the tickets; draw it flat
    visiting.add(n.node_id);
    const deps = n.blocked_by
      .map((id) => nodes.find((x) => x.node_id === id))
      .filter((x): x is GraphNode => x !== undefined);
    const d = deps.length ? 1 + Math.max(...deps.map(at)) : 0;
    visiting.delete(n.node_id);
    depth.set(n.node_id, d);
    return d;
  };
  nodes.forEach(at);
  const max = nodes.length ? Math.max(...nodes.map((n) => depth.get(n.node_id)!)) : 0;
  return Array.from({ length: max + 1 }, (_, i) =>
    nodes.filter((n) => depth.get(n.node_id) === i),
  );
}

/** Whether a node could dispatch right now. Mirrors the harness's own rule:
 * pending, and every blocker done with its whole subtree terminal. */
export function isReady(node: GraphNode, graph: Graph, graphs: Graph[]): boolean {
  if (node.status !== "pending") return false;
  if (node.ticket_type === "task" && (node.task_mode == null || node.task_mode === "user"))
    return false;
  return node.blocked_by.every((id) => {
    const dep = graph.nodes.find((n) => n.node_id === id);
    return dep !== undefined && satisfied(dep, graphs);
  });
}

function satisfied(node: GraphNode, graphs: Graph[]): boolean {
  if (node.status !== "done") return false;
  if (node.graph == null) return true;
  const below = graphs.find((g) => g.graph_id === node.graph);
  if (below === undefined) return false;
  return below.nodes.every((n) => satisfied(n, graphs));
}

/** Node keys held up, directly or transitively, by a node parked at a gate. */
export function gateBlockedKeys(run: RunDetail): Set<string> {
  const out = new Set<string>();
  for (const graph of run.graphs) {
    const gated = new Set(
      graph.nodes.filter((n) => n.gate != null).map((n) => n.node_id),
    );
    if (gated.size === 0) continue;
    let grew = true;
    const held = new Set<string>();
    while (grew) {
      grew = false;
      for (const node of graph.nodes) {
        if (held.has(node.node_id)) continue;
        if (node.blocked_by.some((id) => gated.has(id) || held.has(id))) {
          held.add(node.node_id);
          grew = true;
        }
      }
    }
    for (const id of held) out.add(nodeKey(graph.graph_id, id));
  }
  return out;
}

// ---------------------------------------------------------------------------
// The conversation: transcript events with the orchestrator's interventions
// rendered inline. Transcript events carry no timestamps, but an intervention
// happens strictly between the `result` event that triggered it and the turn
// its message wakes — so interventions are placed after result events, one
// batch per stale point, advancing past a batch when an intervention's
// accepted send_to_session started the next turn.

export type ConversationItem =
  | { kind: "event"; event: DisplayEvent }
  | { kind: "intervention"; intervention: InterventionRecord };

export interface DisplayEvent {
  type: "assistant" | "orchestrator" | "tool_use" | "tool_result" | "result" | "system";
  label: string;
  text: string;
  isError?: boolean;
}

const sentToSession = (record: InterventionRecord): boolean =>
  record.tool_calls.some((c) => c.tool === "send_to_session" && c.refused == null);

export function conversation(
  events: StreamEvent[],
  interventions: InterventionRecord[],
): ConversationItem[] {
  const items: ConversationItem[] = [];
  let next = 0;
  const placeBatch = () => {
    while (next < interventions.length) {
      const record = interventions[next];
      items.push({ kind: "intervention", intervention: record });
      next += 1;
      if (sentToSession(record)) return; // its message started the next turn
    }
  };
  for (const event of events) {
    for (const display of displayEvents(event)) {
      items.push({ kind: "event", event: display });
    }
    if (event.type === "result") placeBatch();
  }
  // Whatever remains happened after the last captured turn — a judgment in
  // flight, or one whose message has not produced output yet.
  while (next < interventions.length) {
    items.push({ kind: "intervention", intervention: interventions[next] });
    next += 1;
  }
  return items;
}

function displayEvents(event: StreamEvent): DisplayEvent[] {
  if (event.type === "system") {
    return [
      {
        type: "system",
        label: "session",
        text: `started${typeof event.model === "string" ? ` · ${event.model}` : ""}`,
      },
    ];
  }
  if (event.type === "result") {
    const cost =
      typeof event.total_cost_usd === "number" ? ` · ${money(event.total_cost_usd)}` : "";
    return [
      {
        type: "result",
        label: "stale",
        text: `${event.result ?? "(turn ended)"}${cost}`,
        isError: event.is_error === true,
      },
    ];
  }
  const content = event.message?.content ?? [];
  const out: DisplayEvent[] = [];
  for (const block of content) {
    if (block.type === "text" && block.text) {
      out.push({
        type: event.type === "user" ? "orchestrator" : "assistant",
        label: event.type === "user" ? "orchestrator" : "session",
        text: block.text,
      });
    } else if (block.type === "tool_use") {
      out.push({
        type: "tool_use",
        label: block.name ?? "tool",
        text: summarizeInput(block.input),
      });
    } else if (block.type === "tool_result") {
      out.push({
        type: "tool_result",
        label: "result",
        text: truncate(flattenToolResult(block.content), 400),
      });
    }
  }
  return out;
}

function summarizeInput(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  return truncate(
    Object.entries(input)
      .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join("  "),
    300,
  );
}

function flattenToolResult(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((c) => (typeof c === "object" && c !== null && "text" in c ? String(c.text) : ""))
      .join("\n");
  return JSON.stringify(content ?? "");
}

const truncate = (text: string, max: number) =>
  text.length > max ? `${text.slice(0, max)}…` : text;
