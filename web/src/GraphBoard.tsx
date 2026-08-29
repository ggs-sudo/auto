// The execution graph, centre-stage. Every graph in the run renders as a
// board — the root node first, then each graph with its nodes layered by
// dependency depth and its edges drawn — and a subgraph names the node that
// spawned it, so the inter-graph linkage reads straight off the page.

import { useLayoutEffect, useRef, useState } from "react";
import {
  ROOT_KEY,
  gateBlockedKeys,
  isReady,
  layersOf,
  nodeKey,
  titleOf,
} from "./derive";
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
            <span className={`mct-type mct-type--${root.type}`}>{root.type}</span>
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
  const wrap = useRef<HTMLDivElement>(null);
  const cells = useRef<Record<string, HTMLElement | null>>({});
  const [edges, setEdges] = useState<{ d: string; done: boolean }[]>([]);

  useLayoutEffect(() => {
    const measure = () => {
      const box = wrap.current?.getBoundingClientRect();
      if (!box) return;
      const next: { d: string; done: boolean }[] = [];
      for (const node of graph.nodes) {
        for (const dep of node.blocked_by) {
          const from = cells.current[dep]?.getBoundingClientRect();
          const to = cells.current[node.node_id]?.getBoundingClientRect();
          if (!from || !to) continue;
          const x1 = from.right - box.left;
          const y1 = from.top + from.height / 2 - box.top;
          const x2 = to.left - box.left;
          const y2 = to.top + to.height / 2 - box.top;
          const mid = (x1 + x2) / 2;
          next.push({
            d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`,
            done:
              graph.nodes.find((x) => x.node_id === dep)?.status === "done",
          });
        }
      }
      setEdges(next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (wrap.current) observer.observe(wrap.current);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [graph]);

  return (
    <section className="mct-board">
      <h3 className="mct-board__title">
        .scratch/{graph.graph_id}/
        {graph.spawned_by !== "root" && <em>subgraph of {graph.spawned_by}</em>}
      </h3>
      <div className="mct-board__canvas" ref={wrap}>
        <svg className="mct-edges">
          {edges.map((edge, i) => (
            <path key={i} d={edge.d} className={edge.done ? "is-done" : ""} />
          ))}
        </svg>
        {layersOf(graph.nodes).map((layer, i) => (
          <div className="mct-layer" key={i}>
            {layer.map((node) => (
              <NodeCell
                key={node.node_id}
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
            ))}
          </div>
        ))}
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
