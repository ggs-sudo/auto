// Derivations over run state. Shared on purpose: this is data, not layout.
// Readiness and blockedness are derived, never stored — the same stance the
// harness takes (schemas carry status and edges, nothing else).

import type {
  Gate,
  Graph,
  GraphNode,
  ReconciliationRecord,
  RunDetail,
  SessionRecord,
  StreamEvent,
} from "./types";

export const nodeKey = (graphId: string, nodeId: string) => `${graphId}/${nodeId}`;
export const nodeIdOf = (key: string) => key.slice(key.indexOf("/") + 1);
export const ROOT_KEY = "root";

/** A ticket stem, read as a title: `0004-prototype-payment-form` → "prototype payment form". */
export const titleOf = (stem: string) => stem.replace(/^\d+-/, "").replace(/-/g, " ");

/** The graph node a run-wide key names, or null for `root` and unknown keys. */
export function graphNodeAt(
  run: RunDetail,
  key: string,
): { graph: Graph; node: GraphNode } | null {
  const slash = key.indexOf("/");
  if (slash < 0) return null;
  const graph = run.graphs.find((g) => g.graph_id === key.slice(0, slash));
  const node = graph?.nodes.find((n) => n.node_id === key.slice(slash + 1));
  return graph != null && node != null ? { graph, node } : null;
}

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

/** Fixed-point spread: ids of nodes in `graph` depending, directly or
 * transitively, on any of `seeds`. The seeds themselves are not included. */
function downstreamWithin(seeds: ReadonlySet<string>, graph: Graph): Set<string> {
  const held = new Set<string>();
  let grew = true;
  while (grew) {
    grew = false;
    for (const node of graph.nodes) {
      if (held.has(node.node_id)) continue;
      if (node.blocked_by.some((id) => seeds.has(id) || held.has(id))) {
        held.add(node.node_id);
        grew = true;
      }
    }
  }
  return held;
}

/** Node keys a node parked at `parkedKey` holds up: its dependents within its
 * own graph, then upward — a parked node keeps its graph's whole subtree
 * incomplete, and a dependency is satisfied only when the subtree is
 * (ADR-0006), so the spawning node's dependents in the parent graph wait too. */
function heldDownstreamOf(parkedKey: string, graphs: Graph[]): Set<string> {
  const out = new Set<string>();
  const climbed = new Set<Graph>();
  let key = parkedKey;
  for (;;) {
    const slash = key.indexOf("/");
    const graph =
      slash < 0 ? undefined : graphs.find((g) => g.graph_id === key.slice(0, slash));
    if (graph == null || climbed.has(graph)) return out;
    climbed.add(graph);
    for (const id of downstreamWithin(new Set([nodeIdOf(key)]), graph)) {
      out.add(nodeKey(graph.graph_id, id));
    }
    key = graph.spawned_by; // "root", or the parent node whose dependents wait
  }
}

/** Node keys held up, directly or transitively, by a node parked at a gate. */
export function gateBlockedKeys(run: RunDetail): Set<string> {
  const out = new Set<string>();
  for (const graph of run.graphs) {
    for (const node of graph.nodes) {
      if (node.gate == null) continue;
      const parked = nodeKey(graph.graph_id, node.node_id);
      for (const key of heldDownstreamOf(parked, run.graphs)) out.add(key);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// The history, cleaned the way Claude Code renders a session: the reader
// gets prose and intent — assistant text, messages sent into the session,
// tool calls as one-liners — and is spared the mechanics: raw tool results
// (unless they errored), system events, the skill texts a Skill invocation
// injects, and the initial prompt the ticket already carries.

export type HistoryItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; summary: string }
  | { kind: "error"; text: string }
  | { kind: "turn"; text: string };

/** A Skill invocation injects the skill's whole instruction file as a user
 * event; the tool one-liner already says it happened. */
const SKILL_TEXT_PREFIX = "Base directory for this skill:";

export function cleanConversation(events: StreamEvent[]): HistoryItem[] {
  const items: HistoryItem[] = [];
  let sawSpeech = false; // once true, a user text is a real mid-run message
  for (const event of events) {
    if (event.type === "result") {
      const cost =
        typeof event.total_cost_usd === "number"
          ? ` · ${money(event.total_cost_usd)}`
          : "";
      items.push({ kind: "turn", text: `turn ended${cost}` });
      continue;
    }
    if (event.type !== "user" && event.type !== "assistant") continue;
    for (const block of event.message?.content ?? []) {
      if (block.type === "text" && block.text) {
        if (event.type === "user" && block.text.startsWith(SKILL_TEXT_PREFIX)) continue;
        if (event.type === "user" && !sawSpeech) {
          // The initial prompt: the first voice heard, and it is the user's.
          sawSpeech = true;
          continue;
        }
        sawSpeech = true;
        items.push({
          kind: event.type === "user" ? "user" : "assistant",
          text: block.text,
        });
      } else if (block.type === "tool_use" && event.type === "assistant") {
        items.push({
          kind: "tool",
          name: block.name ?? "tool",
          summary: truncate(primaryArgument(block.input), 110),
        });
      } else if (block.type === "tool_result") {
        // Results are noise unless they broke something.
        if ((block as { is_error?: unknown }).is_error === true) {
          items.push({ kind: "error", text: truncate(flattenToolResult(block.content), 300) });
        }
      }
    }
  }
  return items;
}

/** The primary argument is the summary — Read(file), Bash(command) — the
 * rest is noise at reading distance. */
function primaryArgument(input: Record<string, unknown> | undefined): string {
  if (input == null) return "";
  const preferred = ["file_path", "command", "pattern", "url", "path", "query", "skill"];
  for (const key of preferred) {
    if (typeof input[key] === "string") return input[key];
  }
  const first = Object.values(input).find((value) => typeof value === "string");
  return typeof first === "string" ? first : "";
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

// ---------------------------------------------------------------------------
// Takeover consultations as addressable nodes. They are not graph nodes —
// nothing depends on them and they block nothing — so they live in their own
// key namespace, one per consultation, since one effort can be taken over
// many times.

const TAKEOVER_PREFIX = "takeover/";

export const takeoverKey = (reconciliationId: string) =>
  `${TAKEOVER_PREFIX}${reconciliationId}`;

export const isTakeoverKey = (key: string) => key.startsWith(TAKEOVER_PREFIX);

/** The consultation a takeover key names, or null for unknown ids — the
 * record may simply not be written yet. */
export function reconciliationAt(
  run: RunDetail,
  key: string,
): ReconciliationRecord | null {
  if (!isTakeoverKey(key)) return null;
  const id = key.slice(TAKEOVER_PREFIX.length);
  return run.reconciliations.find((r) => r.reconciliation_id === id) ?? null;
}

/** Node keys downstream of one gate's parked node — what answering it frees.
 * A run-level ping parks nothing and holds nothing. */
export function keysHeldBy(run: RunDetail, gate: Gate): string[] {
  return gate.node == null ? [] : [...heldDownstreamOf(gate.node, run.graphs)];
}
