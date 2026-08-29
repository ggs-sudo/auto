// A real layout engine behind a small seam: measured boxes and dependency
// edges in, top-left positions and routed edge polylines out. Dagre does
// the ranking and crossing minimisation; nothing outside this file knows.

import dagre from "@dagrejs/dagre";

export interface LayoutBox {
  id: string;
  width: number;
  height: number;
  /** Ids of the boxes this one is blocked by — drawn as edges into it. */
  deps: string[];
}

export interface Point {
  x: number;
  y: number;
}

export interface LaidOut {
  width: number;
  height: number;
  /** Top-left corner of each box. */
  nodes: Map<string, Point>;
  edges: { from: string; to: string; points: Point[] }[];
}

const NODE_SEP = 14;
const RANK_SEP = 46;
const MARGIN = 4;

export function layoutGraph(boxes: LayoutBox[]): LaidOut {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({
    rankdir: "LR",
    nodesep: NODE_SEP,
    ranksep: RANK_SEP,
    marginx: MARGIN,
    marginy: MARGIN,
  });
  graph.setDefaultEdgeLabel(() => ({}));

  const known = new Set(boxes.map((box) => box.id));
  for (const box of boxes) {
    graph.setNode(box.id, { width: box.width, height: box.height });
  }
  for (const box of boxes) {
    for (const dep of box.deps) {
      if (known.has(dep)) graph.setEdge(dep, box.id);
    }
  }

  dagre.layout(graph);

  const nodes = new Map<string, Point>();
  for (const box of boxes) {
    const laid = graph.node(box.id);
    nodes.set(box.id, { x: laid.x - box.width / 2, y: laid.y - box.height / 2 });
  }
  const edges = graph.edges().map((edge) => ({
    from: edge.v,
    to: edge.w,
    points: graph.edge(edge).points ?? [],
  }));

  const laidGraph = graph.graph();
  return {
    width: laidGraph.width ?? 0,
    height: laidGraph.height ?? 0,
    nodes,
    edges,
  };
}

/** An SVG path through an edge's routed points: straight when two, a smooth
 * curve through dagre's control points otherwise. */
export function edgePath(points: Point[]): string {
  if (points.length === 0) return "";
  const [head, ...rest] = points;
  if (rest.length === 0) return `M${head.x},${head.y}`;
  if (rest.length === 1) return `M${head.x},${head.y} L${rest[0].x},${rest[0].y}`;
  let path = `M${head.x},${head.y}`;
  for (let i = 0; i < rest.length - 1; i++) {
    const mid = {
      x: (rest[i].x + rest[i + 1].x) / 2,
      y: (rest[i].y + rest[i + 1].y) / 2,
    };
    path += ` Q${rest[i].x},${rest[i].y} ${mid.x},${mid.y}`;
  }
  const last = rest.at(-1)!;
  path += ` L${last.x},${last.y}`;
  return path;
}
