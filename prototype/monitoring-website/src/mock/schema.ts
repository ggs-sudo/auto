// PROTOTYPE — types mirror the run-state schema settled in issue #3 (+ the #4 amendment).
// Deliberately hand-written rather than generated from `schemas/`, which doesn't exist yet.

export type RunStatus = "running" | "gated" | "done" | "failed" | "aborted";
export type Route = "wayfinder" | "grill-with-docs";

export interface RunManifest {
  schema_version: 1;
  run_id: string;
  route: Route;
  prompt: string;
  target_repo: string;
  worktree: string;
  branch: string;
  created_at: string;
  ended_at: string | null;
  status: RunStatus;
  phase: string;
}

export type NodeType =
  | "wayfinder"
  | "grill-with-docs"
  | "research"
  | "prototype"
  | "implement";
export type NodeStatus = "pending" | "in-progress" | "done" | "failed";
export type ResolutionMode = "agent" | "user" | "undefined";

export interface GraphNode {
  id: string;
  title: string;
  type: NodeType;
  deps: string[];
  status: NodeStatus;
  session_id: string | null;
  /** graph_id of the subgraph this node spawned, if any */
  spawned_graph?: string;
  /** only on nodes derived from wayfinder `task` tickets */
  resolution_mode?: ResolutionMode;
}

export interface Graph {
  /** graph id == `.scratch/<effort>/` directory name */
  graph_id: string;
  /** the node (in another graph) whose session emitted this graph */
  spawned_by_node: string | null;
  nodes: GraphNode[];
}

export type SessionStatus = "running" | "succeeded" | "failed";

export interface SessionRecord {
  session_id: string;
  role: NodeType;
  ticket: string;
  graph_id: string;
  status: SessionStatus;
  started_at: string;
  ended_at: string | null;
  summary: string;
  /** headline facts the orchestrator transcribed — drives at-a-glance views */
  highlights: string[];
  transcript: string;
  native_transcript: string;
  cost_usd: number;
  num_turns: number;
  /** true for grilling/wayfinder sessions the orchestrator reads in full */
  monitored: boolean;
}

export type GateKind =
  | "prototype-review"
  | "ping"
  | "escalated-question"
  | "task-completion";

export interface Gate {
  seq: number;
  kind: GateKind;
  opened_at: string;
  opened_by_session: string | null;
  on_node: string | null;
  graph_id: string | null;
  question: string;
  artifact?: string;
  /** derived downstream set — the nodes this gate is holding up */
  blocking: string[];
  response?: GateResponse;
}

export interface GateResponse {
  responded_at: string;
  decision: "approve" | "revise" | "done" | "cannot" | "answered";
  text: string;
}

export type EventKind =
  | "user"
  | "assistant"
  | "tool_use"
  | "tool_result"
  | "orchestrator"
  | "result";

export interface TranscriptEvent {
  t: string;
  kind: EventKind;
  /** tool name, for tool_use / tool_result */
  tool?: string;
  text: string;
}

export interface RunDetail {
  manifest: RunManifest;
  graphs: Graph[];
  sessions: SessionRecord[];
  gates: Gate[];
  transcripts: Record<string, TranscriptEvent[]>;
}
