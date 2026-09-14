// The API's shapes, mirroring the generated JSON Schemas in ../schemas/ —
// which are themselves generated from the harness's Pydantic models, the one
// source of truth. Field names and enums here must match those files exactly.

export type RunStatus = "running" | "gated" | "done" | "failed" | "aborted";
export type NodeStatus =
  | "pending"
  | "in-progress"
  | "review-pending"
  | "done"
  | "failed";
export type SessionStatus = "running" | "succeeded" | "failed";
export type NodeType =
  | "grill-with-docs"
  | "wayfinder"
  | "research"
  | "prototype"
  | "implement";
export type TicketType = "research" | "prototype" | "grilling" | "task" | "implement";
export type GateKind =
  | "prototype-review"
  | "task-completion"
  | "escalated-question"
  | "user-ping";
export type GateDecision =
  | "approve"
  | "revise"
  | "done"
  | "cannot"
  | "answer"
  | "dismiss";

export interface Telemetry {
  cost_usd: number | null;
  num_turns: number | null;
  duration_ms: number | null;
  stop_reason: string | null;
  terminal_reason: string | null;
  is_error: boolean | null;
}

export interface RootNode {
  node_id: string;
  /** The entry skill, or null on the takeover route — its root session is
   * the reconciliation rather than a skill. */
  type: NodeType | null;
  prompt: string;
  status: NodeStatus;
  session_id: string | null;
  nudge_count: number;
  missing_artifacts: string[];
  graph: string | null;
  gate: string | null;
}

export interface Manifest {
  run_id: string;
  route: "grill" | "wayfinder" | "takeover";
  prompt: string;
  target_repo: string;
  branch: string | null;
  head: string | null;
  dirty: boolean;
  created_at: string;
  ended_at: string | null;
  status: RunStatus;
  phase: string | null;
  root_node: RootNode;
  driven_spend_usd: number;
  orchestrator_spend_usd: number;
}

export interface GraphNode {
  node_id: string;
  ticket: string;
  ticket_type: TicketType;
  task_mode: "agent" | "user" | "undefined" | null;
  blocked_by: string[];
  status: NodeStatus;
  session_id: string | null;
  nudge_count: number;
  missing_artifacts: string[];
  graph: string | null;
  gate: string | null;
}

export interface Graph {
  graph_id: string;
  spawned_by: string;
  nodes: GraphNode[];
}

export interface SessionRecord {
  session_id: string;
  node: string;
  role: NodeType;
  ticket: string | null;
  status: SessionStatus;
  started_at: string;
  ended_at: string | null;
  summary: string | null;
  highlights: string[];
  telemetry: Telemetry;
}

export interface ToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  refused: string | null;
}

export interface InterventionRecord {
  intervention_id: string;
  node: string;
  trigger: "stale" | "gate-response";
  model: string;
  started_at: string;
  ended_at: string | null;
  prose: string | null;
  tool_calls: ToolCall[];
  telemetry: Telemetry;
}

export interface GateResponse {
  decision: GateDecision;
  text: string;
}

export interface Gate {
  gate_id: string;
  sequence: number;
  kind: GateKind;
  node: string | null;
  question: string;
  artifact: string | null;
  raised_at: string;
  answered_at: string | null;
  response: GateResponse | null;
}

export interface NodeCounts {
  total: number;
  done: number;
  running: number;
  review_pending: number;
  pending: number;
  failed: number;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  route: "grill" | "wayfinder" | "takeover";
  prompt: string;
  target_repo: string;
  created_at: string;
  ended_at: string | null;
  spend_usd: number;
  nodes: NodeCounts;
  open_gates: number;
}

/** One graph-node status a takeover consultation corrected, and why. */
export interface Correction {
  node: string;
  prior_status: NodeStatus;
  new_status: NodeStatus;
  evidence: string;
}

/** One lying ticket `Status:` line a consultation corrected. */
export interface TicketCorrection {
  node: string;
  ticket: string;
  prior_status: string;
  new_status: string;
  evidence: string;
}

/** One takeover consultation. Written before the agent runs — a null
 * verdict with a null ended_at is a judgment still being made; a null
 * verdict with an ended_at means the takeover stopped instead of resuming. */
export interface ReconciliationRecord {
  reconciliation_id: string;
  sequence: number;
  effort: string;
  examined: string[];
  verdict: "clean" | "corrected" | null;
  corrections: Correction[];
  ticket_corrections: TicketCorrection[];
  model: string;
  session_id: string;
  started_at: string;
  ended_at: string | null;
  prose: string | null;
  tool_calls: ToolCall[];
  telemetry: Telemetry;
}

export interface RunDetail {
  manifest: Manifest;
  graphs: Graph[];
  sessions: SessionRecord[];
  interventions: InterventionRecord[];
  reconciliations: ReconciliationRecord[];
  gates: Gate[];
  nodes: NodeCounts;
  open_gates: number;
  version: number;
}

// One line of captured stream-json. The harness treats the stream as data it
// does not own, and so does the site: everything is optional beyond `type`.
export interface StreamEvent {
  type: string;
  subtype?: string;
  result?: string;
  is_error?: boolean;
  total_cost_usd?: number;
  num_turns?: number;
  message?: {
    role?: string;
    content?: Array<{
      type?: string;
      text?: string;
      name?: string;
      input?: Record<string, unknown>;
      content?: unknown;
    }>;
  };
  [key: string]: unknown;
}

export interface TranscriptTail {
  events: StreamEvent[];
  offset: number;
}
