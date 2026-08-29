// The derivations the boards and gate cards stand on. Fixtures are minimal
// literals, not schema dumps: each test names only the fields it reads.

import { describe, expect, it } from "vitest";
import {
  conversation,
  elapsed,
  gateBlockedKeys,
  isReady,
  keysHeldBy,
  nodeIdOf,
  nodeKey,
  titleOf,
} from "./derive";
import type { Graph, GraphNode, InterventionRecord, RunDetail, StreamEvent } from "./types";

function node(id: string, over: Partial<GraphNode> = {}): GraphNode {
  return {
    node_id: id,
    ticket: `${id}.md`,
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

function graph(id: string, nodes: GraphNode[], spawnedBy = "root"): Graph {
  return { graph_id: id, spawned_by: spawnedBy, nodes };
}

describe("titleOf", () => {
  it("drops the numeric prefix and reads dashes as spaces", () => {
    expect(titleOf("0004-prototype-payment-form")).toBe("prototype payment form");
  });
});

describe("node keys", () => {
  it("round-trips through nodeKey and nodeIdOf", () => {
    expect(nodeIdOf(nodeKey("effort", "0001-a"))).toBe("0001-a");
  });
});

describe("elapsed", () => {
  it("prefers the coarsest sensible unit", () => {
    const start = "2026-08-29T10:00:00Z";
    const at = (iso: string) => Date.parse(iso);
    expect(elapsed(start, null, at("2026-08-29T10:00:42Z"))).toBe("42s");
    expect(elapsed(start, null, at("2026-08-29T10:07:00Z"))).toBe("7m");
    expect(elapsed(start, "2026-08-29T13:05:00Z", 0)).toBe("3h05");
  });
});

describe("isReady", () => {
  it("holds a dependent until the blocker's spawned subtree is terminal", () => {
    const parent = graph("root-effort", [
      node("0001-a", { status: "done", graph: "sub-effort" }),
      node("0002-b", { blocked_by: ["0001-a"] }),
    ]);
    const openBelow = graph("sub-effort", [node("0009-x")], "root-effort/0001-a");
    expect(isReady(parent.nodes[1], parent, [parent, openBelow])).toBe(false);

    const doneBelow = graph(
      "sub-effort",
      [node("0009-x", { status: "done" })],
      "root-effort/0001-a",
    );
    expect(isReady(parent.nodes[1], parent, [parent, doneBelow])).toBe(true);
  });

  it("never readies a user task; the gate does that", () => {
    const g = graph("e", [node("0001-t", { ticket_type: "task", task_mode: "user" })]);
    expect(isReady(g.nodes[0], g, [g])).toBe(false);
  });
});

function detailWith(graphs: Graph[], over: Partial<RunDetail> = {}): RunDetail {
  return {
    manifest: {} as RunDetail["manifest"],
    graphs,
    sessions: [],
    interventions: [],
    gates: [],
    nodes: { total: 0, done: 0, running: 0, review_pending: 0, pending: 0, failed: 0 },
    open_gates: 0,
    version: 1,
    ...over,
  };
}

describe("gateBlockedKeys", () => {
  it("holds dependents in the parked node's graph and climbs to the parent's dependents", () => {
    const sub = graph(
      "sub",
      [
        node("0001-parked", { gate: "g-1", status: "in-progress" }),
        node("0002-below", { blocked_by: ["0001-parked"] }),
      ],
      "top/0005-spawner",
    );
    const top = graph("top", [
      node("0005-spawner", { status: "done", graph: "sub" }),
      node("0006-after", { blocked_by: ["0005-spawner"] }),
      node("0007-free"),
    ]);
    const held = gateBlockedKeys(detailWith([top, sub]));
    expect(held).toEqual(new Set(["sub/0002-below", "top/0006-after"]));
  });
});

describe("keysHeldBy", () => {
  it("holds nothing for a run-level ping", () => {
    const run = detailWith([graph("e", [node("0001-a")])], {
      gates: [
        {
          gate_id: "g-1",
          sequence: 1,
          kind: "user-ping",
          node: null,
          question: "fyi",
          artifact: null,
          raised_at: "2026-08-29T10:00:00Z",
          answered_at: null,
          response: null,
        },
      ],
    });
    expect(keysHeldBy(run, run.gates[0])).toEqual([]);
  });
});

describe("conversation", () => {
  const assistant = (text: string): StreamEvent => ({
    type: "assistant",
    message: { content: [{ type: "text", text }] },
  });
  const result = (text: string): StreamEvent => ({ type: "result", result: text });
  const intervention = (
    id: string,
    tools: { tool: string; refused: string | null }[],
  ): InterventionRecord => ({
    intervention_id: id,
    node: "root",
    trigger: "stale",
    model: "m",
    started_at: "2026-08-29T10:00:00Z",
    ended_at: null,
    prose: null,
    tool_calls: tools.map(({ tool, refused }) => ({ tool, arguments: {}, refused })),
    telemetry: {
      cost_usd: null,
      num_turns: null,
      duration_ms: null,
      stop_reason: null,
      terminal_reason: null,
      is_error: null,
    },
  });

  it("places one intervention batch after each result, advancing past a send", () => {
    const items = conversation(
      [assistant("hi"), result("turn 1"), assistant("more"), result("turn 2")],
      [
        intervention("iv-1", [{ tool: "send_to_session", refused: null }]),
        intervention("iv-2", []),
      ],
    );
    expect(
      items.map((item) =>
        item.kind === "event" ? item.event.type : item.intervention.intervention_id,
      ),
    ).toEqual(["assistant", "result", "iv-1", "assistant", "result", "iv-2"]);
  });

  it("appends leftover interventions after the captured turns", () => {
    const items = conversation([assistant("hi")], [intervention("iv-1", [])]);
    expect(items.at(-1)).toMatchObject({ kind: "intervention" });
  });
});
