// The URL contract: runs, nodes and sessions addressable, the hash the one
// source of truth. Parse and format are inverses over every route shape.

import { describe, expect, it } from "vitest";
import { formatHash, parseHash } from "./router";

describe("parseHash", () => {
  it("reads an empty or bare hash as no selection", () => {
    expect(parseHash("")).toEqual({ runId: null, node: "root", session: null });
    expect(parseHash("#/")).toEqual({ runId: null, node: "root", session: null });
  });

  it("reads a run route, defaulting to the root node", () => {
    expect(parseHash("#/run/run-20260829-a1b2")).toEqual({
      runId: "run-20260829-a1b2",
      node: "root",
      session: null,
    });
  });

  it("reads a node route whose key spans graph and node", () => {
    expect(parseHash("#/run/r-1/node/my-effort/0002-build-the-thing")).toEqual({
      runId: "r-1",
      node: "my-effort/0002-build-the-thing",
      session: null,
    });
  });

  it("reads the root node route", () => {
    expect(parseHash("#/run/r-1/node/root")).toEqual({
      runId: "r-1",
      node: "root",
      session: null,
    });
  });

  it("reads a session route", () => {
    expect(parseHash("#/run/r-1/session/sess-42")).toEqual({
      runId: "r-1",
      node: "root",
      session: "sess-42",
    });
  });

  it("decodes percent-encoded segments", () => {
    expect(parseHash("#/run/r%201").runId).toBe("r 1");
  });

  it("treats an unrecognised shape as no selection", () => {
    expect(parseHash("#/what/ever")).toEqual({ runId: null, node: "root", session: null });
  });
});

describe("formatHash", () => {
  it("round-trips through parseHash", () => {
    for (const route of [
      { runId: null, node: "root", session: null },
      { runId: "r-1", node: "root", session: null },
      { runId: "r-1", node: "my-effort/0002-build", session: null },
      { runId: "r 1", node: "root", session: null },
      { runId: "r-1", node: "root", session: "sess-42" },
    ]) {
      expect(parseHash(formatHash(route))).toEqual(route);
    }
  });

  it("keeps the node key's slash readable rather than encoding it", () => {
    expect(formatHash({ runId: "r-1", node: "e/0001-a", session: null })).toBe(
      "#/run/r-1/node/e/0001-a",
    );
  });
});
