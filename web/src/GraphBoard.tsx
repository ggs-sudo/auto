// The execution graph, centre-stage. Every graph in the run renders as a
// board — the root node first, then each graph laid out by a real engine
// (dagre, behind layout.ts): nodes are rendered, measured, then positioned,
// and the edges follow the engine's routing. A subgraph names the node that
// spawned it, so the inter-graph linkage reads straight off the page.

import { useLayoutEffect, useReducer, useRef, useState } from "react";
import { ROOT_KEY, gateBlockedKeys, isReady, nodeKey, titleOf } from "./derive";
import { edgePath, layoutGraph, type LaidOut } from "./layout";
import type { Graph, GraphNode, RunDetail } from "./types";

export function RunBoards({
  run,
  selected,
  onSelect,
}: {
  run: RunDetail;
  selected: string;
  onSelect: (key: string) => void;
}) {
  const blocked = gateBlockedKeys(run);
  return (
    <div className="mct-graphs">
      <RootBoard run={run} selected={selected} onSelect={onSelect} />
      {run.graphs.map((graph) => (
        <GraphBoard
          key={graph.graph_id}
          graph={graph}
          graphs={run.graphs}
          selected={selected}
          blocked={blocked}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function RootBoard({
  run,
  selected,
  onSelect,
}: {
  run: RunDetail;
  selected: string;
  onSelect: (key: string) => void;
}) {
  const root = run.manifest.root_node;
  return (
    <section className="mct-board">
      <h3 className="mct-board__title">run</h3>
      <div className="mct-board__canvas">
        <div className="mct-layer mct-layer--root">
          <button
            className={`mct-node mct-node--${root.status} ${selected === ROOT_KEY ? "is-on" : ""}`}
            onClick={() => onSelect(ROOT_KEY)}
          >
            <span className={`mct-type mct-type--${root.type ?? "reconciliation"}`}>
              {root.type ?? "reconciliation"}
            </span>
            <span className="mct-node__title">root — the pasted prompt</span>
            <span className="mct-node__foot">
              <span>{ROOT_KEY}</span>
              {root.graph != null && <span className="mct-tag">↳ {root.graph}</span>}
              <NudgeTag node={root} />
            </span>
          </button>
        </div>
      </div>
    </section>
  );
}

function NudgeTag({
  node,
}: {
  node: { nudge_count: number; missing_artifacts: string[] };
}) {
  if (node.missing_artifacts.length === 0) return null;
  return (
    <span className="mct-tag mct-tag--owes">
      owes {node.missing_artifacts.length}
      {node.nudge_count > 0 && ` · nudged ×${node.nudge_count}`}
    </span>
  );
}

function GraphBoard({
  graph,
  graphs,
  selected,
  blocked,
  onSelect,
}: {
  graph: Graph;
  graphs: Graph[];
  selected: string;
  blocked: Set<string>;
  onSelect: (key: string) => void;
}) {
  const cells = useRef<Record<string, HTMLElement | null>>({});
  const [laid, setLaid] = useState<LaidOut | null>(null);
  const lastSignature = useRef("");
  const [measureTick, remeasure] = useReducer((tick: number) => tick + 1, 0);

  // Render, measure, lay out: the cells commit unpositioned (and unpainted —
  // this runs before paint), their real sizes feed the engine, and the
  // positions land in state. The signature keeps a same-shape refetch from
  // looping through setState.
  useLayoutEffect(() => {
    const boxes = graph.nodes.map((node) => {
      const cell = cells.current[node.node_id];
      return {
        id: node.node_id,
        width: cell?.offsetWidth ?? 220,
        height: cell?.offsetHeight ?? 72,
        deps: node.blocked_by,
      };
    });
    const signature = JSON.stringify(boxes);
    if (signature === lastSignature.current) return;
    lastSignature.current = signature;
    setLaid(layoutGraph(boxes));
  });

  // A cell can change size without a re-render — zoom, font load — and the
  // layout must follow. The observer only bumps a counter; the layout effect
  // above re-measures and decides whether anything actually moved.
  useLayoutEffect(() => {
    const observer = new ResizeObserver(() => remeasure());
    for (const cell of Object.values(cells.current)) {
      if (cell != null) observer.observe(cell);
    }
    return () => observer.disconnect();
  }, [graph]);
  void measureTick;

  const statuses = new Map(graph.nodes.map((node) => [node.node_id, node.status]));

  return (
    <section className="mct-board">
      <h3 className="mct-board__title">
        .scratch/{graph.graph_id}/
        {graph.spawned_by !== "root" && <em>subgraph of {graph.spawned_by}</em>}
      </h3>
      <div className="mct-board__canvas mct-board__canvas--laid">
        <div
          className="mct-board__field"
          style={laid != null ? { width: laid.width, height: laid.height } : undefined}
        >
          <svg
            className="mct-edges"
            width={laid?.width ?? 0}
            height={laid?.height ?? 0}
          >
            {laid?.edges.map((edge) => (
              <path
                key={`${edge.from}→${edge.to}`}
                d={edgePath(edge.points)}
                className={statuses.get(edge.from) === "done" ? "is-done" : ""}
              />
            ))}
          </svg>
          {graph.nodes.map((node) => {
            const pos = laid?.nodes.get(node.node_id);
            return (
              <div
                key={node.node_id}
                className="mct-cell"
                style={
                  pos != null
                    ? { left: pos.x, top: pos.y }
                    : { left: 0, top: 0, visibility: "hidden" }
                }
              >
                <NodeCell
                  node={node}
                  graph={graph}
                  graphs={graphs}
                  selected={selected}
                  blocked={blocked}
                  onSelect={onSelect}
                  cellRef={(el) => {
                    cells.current[node.node_id] = el;
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function NodeCell({
  node,
  graph,
  graphs,
  selected,
  blocked,
  onSelect,
  cellRef,
}: {
  node: GraphNode;
  graph: Graph;
  graphs: Graph[];
  selected: string;
  blocked: Set<string>;
  onSelect: (key: string) => void;
  cellRef: (el: HTMLButtonElement | null) => void;
}) {
  const key = nodeKey(graph.graph_id, node.node_id);
  const held = blocked.has(key);
  return (
    <button
      ref={cellRef}
      className={`mct-node mct-node--${node.status} ${selected === key ? "is-on" : ""} ${
        held ? "is-blocked" : ""
      }`}
      onClick={() => onSelect(key)}
    >
      <span className={`mct-type mct-type--${node.ticket_type}`}>
        {node.ticket_type}
      </span>
      <span className="mct-node__title">{titleOf(node.node_id)}</span>
      <span className="mct-node__foot">
        <span>{node.node_id.split("-", 1)[0]}</span>
        {node.task_mode === "user" && <span className="mct-tag">user</span>}
        {node.graph != null && <span className="mct-tag">↳ {node.graph}</span>}
        {isReady(node, graph, graphs) && (
          <span className="mct-tag mct-tag--ready">ready</span>
        )}
        {held && <span className="mct-tag mct-tag--blocked">held by gate</span>}
        <NudgeTag node={node} />
      </span>
      {node.gate != null && (
        <span className="mct-node__gate">needs you · {node.gate.slice(0, 4)}</span>
      )}
    </button>
  );
}
