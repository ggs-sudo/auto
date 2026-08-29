// The layout seam: measured boxes and dependency edges in, positions and
// edge polylines out. The engine (dagre) is behind this interface; the
// tests state what any engine must guarantee for the boards to read.

import { describe, expect, it } from "vitest";
import { layoutGraph, type LayoutBox } from "./layout";

const box = (id: string, deps: string[] = []): LayoutBox => ({
  id,
  width: 220,
  height: 72,
  deps,
});

describe("layoutGraph", () => {
  it("positions every node inside the reported canvas", () => {
    const laid = layoutGraph([box("a"), box("b", ["a"]), box("c", ["a"])]);
    expect(laid.nodes.size).toBe(3);
    for (const pos of laid.nodes.values()) {
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeGreaterThanOrEqual(0);
      expect(pos.x + 220).toBeLessThanOrEqual(laid.width);
      expect(pos.y + 72).toBeLessThanOrEqual(laid.height);
    }
  });

  it("flows dependencies left to right", () => {
    const laid = layoutGraph([box("a"), box("b", ["a"]), box("c", ["b"])]);
    const a = laid.nodes.get("a")!;
    const b = laid.nodes.get("b")!;
    const c = laid.nodes.get("c")!;
    expect(a.x + 220).toBeLessThanOrEqual(b.x);
    expect(b.x + 220).toBeLessThanOrEqual(c.x);
  });

  it("keeps parallel nodes from overlapping", () => {
    const laid = layoutGraph([box("a"), box("b", ["a"]), box("c", ["a"]), box("d", ["a"])]);
    const rows = ["b", "c", "d"]
      .map((id) => laid.nodes.get(id)!)
      .sort((p, q) => p.y - q.y);
    expect(rows[0].y + 72).toBeLessThanOrEqual(rows[1].y);
    expect(rows[1].y + 72).toBeLessThanOrEqual(rows[2].y);
  });

  it("emits one edge per dependency, routed between the two boxes", () => {
    const laid = layoutGraph([box("a"), box("b", ["a"])]);
    expect(laid.edges).toHaveLength(1);
    const edge = laid.edges[0];
    expect(edge.from).toBe("a");
    expect(edge.to).toBe("b");
    expect(edge.points.length).toBeGreaterThanOrEqual(2);
    const a = laid.nodes.get("a")!;
    const b = laid.nodes.get("b")!;
    expect(edge.points[0].x).toBeGreaterThanOrEqual(a.x);
    expect(edge.points.at(-1)!.x).toBeLessThanOrEqual(b.x + 220);
  });

  it("ignores a dependency on an id it was not given", () => {
    const laid = layoutGraph([box("a", ["ghost"])]);
    expect(laid.nodes.size).toBe(1);
    expect(laid.edges).toHaveLength(0);
  });

  it("survives a dependency cycle rather than hanging", () => {
    const laid = layoutGraph([box("a", ["b"]), box("b", ["a"])]);
    expect(laid.nodes.size).toBe(2);
  });

  it("lays out an empty graph as an empty canvas", () => {
    const laid = layoutGraph([]);
    expect(laid.nodes.size).toBe(0);
    expect(laid.edges).toHaveLength(0);
  });
});

import { edgePath } from "./layout";

describe("edgePath", () => {
  it("draws a straight line for two points", () => {
    expect(edgePath([{ x: 0, y: 0 }, { x: 10, y: 10 }])).toBe("M0,0 L10,10");
  });

  it("curves through intermediate control points", () => {
    const path = edgePath([{ x: 0, y: 0 }, { x: 10, y: 5 }, { x: 20, y: 10 }]);
    expect(path.startsWith("M0,0")).toBe(true);
    expect(path).toContain("Q10,5");
    expect(path.endsWith("L20,10")).toBe(true);
  });
});
