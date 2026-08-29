// Test fixtures: one small run, shaped like the fixture generator's output.
// Builders over literals so each test overrides only what it reads.

import type {
  Gate,
  Graph,
  GraphNode,
  Manifest,
  RunDetail,
  RunSummary,
  SessionRecord,
  StreamEvent,
} from "./types";

export function graphNode(id: string, over: Partial<GraphNode> = {}): GraphNode {
  return {
    node_id: id,
    ticket: `.scratch/effort/${id}.md`,
    ticket_type: "implement",
    task_mode: null,
    blocked_by: [],
    status: "pending",
    session_id: null,
    nudge_count: 0,
    missing_artifacts: [],
    graph: null,
    gate: null,
    ...over,
  };
}

export function sessionRecord(id: string, node: string, over: Partial<SessionRecord> = {}): SessionRecord {
  return {
    session_id: id,
    node,
    role: "implement",
    ticket: null,
    status: "running",
    started_at: "2026-08-29T10:00:00Z",
    ended_at: null,
    summary: null,
    highlights: [],
    telemetry: {
      cost_usd: 0.5,
      num_turns: 3,
      duration_ms: null,
      stop_reason: null,
      terminal_reason: null,
      is_error: null,
    },
    ...over,
  };
}

export function manifest(runId: string, over: Partial<Manifest> = {}): Manifest {
  return {
    run_id: runId,
    route: "wayfinder",
    prompt: "build the thing",
    target_repo: "/repo",
    branch: null,
    head: null,
    dirty: false,
    created_at: "2026-08-29T09:00:00Z",
    ended_at: null,
    status: "running",
    phase: null,
    root_node: {
      node_id: "root",
      type: "wayfinder",
      prompt: "build the thing",
      status: "in-progress",
      session_id: "sess-root",
      nudge_count: 0,
      missing_artifacts: [],
      graph: "effort",
      gate: null,
    },
    driven_spend_usd: 1.25,
    orchestrator_spend_usd: 0.25,
    ...over,
  };
}

export function runDetail(runId: string, over: Partial<RunDetail> = {}): RunDetail {
  const graphs: Graph[] = [
    {
      graph_id: "effort",
      spawned_by: "root",
      nodes: [
        graphNode("0001-first", { status: "done", session_id: "sess-1" }),
        graphNode("0002-second", { blocked_by: ["0001-first"], status: "in-progress", session_id: "sess-2" }),
      ],
    },
  ];
  return {
    manifest: manifest(runId),
    graphs,
    sessions: [
      sessionRecord("sess-root", "root", { role: "wayfinder" }),
      sessionRecord("sess-1", "effort/0001-first", { status: "succeeded" }),
      sessionRecord("sess-2", "effort/0002-second"),
    ],
    interventions: [],
    gates: [],
    nodes: { total: 3, done: 1, running: 2, review_pending: 0, pending: 0, failed: 0 },
    open_gates: 0,
    version: 1,
    ...over,
  };
}

export function runSummary(runId: string, over: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: runId,
    status: "running",
    route: "wayfinder",
    prompt: "build the thing",
    target_repo: "/repo",
    created_at: "2026-08-29T09:00:00Z",
    ended_at: null,
    spend_usd: 1.5,
    nodes: { total: 3, done: 1, running: 2, review_pending: 0, pending: 0, failed: 0 },
    open_gates: 0,
    ...over,
  };
}

export const gateFixture = (over: Partial<Gate> = {}): Gate => ({
  gate_id: "gate-1",
  sequence: 1,
  kind: "escalated-question",
  node: "effort/0002-second",
  question: "Which provider?",
  artifact: null,
  raised_at: "2026-08-29T10:00:00Z",
  answered_at: null,
  response: null,
  ...over,
});

export const textEvent = (text: string): StreamEvent => ({
  type: "assistant",
  message: { content: [{ type: "text", text }] },
});
