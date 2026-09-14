// The shell's contract: the hash addresses what you see, and every way the
// data can be missing — server down, unknown run, no runs, connection lost —
// renders as a state a person can act on, never a blank pane.

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { reconciliationRecord, runDetail, runSummary } from "./fixtures";
import { FakeEventSource } from "./test-doubles";
import type { RunDetail, RunSummary } from "./types";

interface FakeServer {
  runs: RunSummary[] | null; // null: the listing endpoint fails
  details: Record<string, RunDetail>;
  transcriptsFail?: boolean;
}

let server: FakeServer;

function respond(url: string): { ok: boolean; status: number; json: () => Promise<unknown> } {
  const path = url.split("?")[0];
  if (/\/transcripts\//.test(path)) {
    if (server.transcriptsFail === true) {
      return { ok: false, status: 500, json: async () => ({ error: "boom" }) };
    }
    return { ok: true, status: 200, json: async () => ({ events: [], offset: 0 }) };
  }
  if (path === "/api/runs") {
    if (server.runs == null) throw new TypeError("Failed to fetch");
    const listed = server.runs;
    return { ok: true, status: 200, json: async () => listed };
  }
  const match = path.match(/^\/api\/runs\/([^/]+)$/);
  if (match != null) {
    const detail = server.details[decodeURIComponent(match[1])];
    if (detail == null) {
      return { ok: false, status: 404, json: async () => ({ error: "no such run" }) };
    }
    return { ok: true, status: 200, json: async () => detail };
  }
  throw new Error(`unexpected fetch: ${url}`);
}

beforeEach(() => {
  server = {
    runs: [runSummary("run-1")],
    details: { "run-1": runDetail("run-1") },
  };
  vi.stubGlobal("EventSource", FakeEventSource);
  FakeEventSource.instances = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => respond(url)),
  );
  window.history.replaceState(null, "", "#/");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App routing", () => {
  it("lands on the newest run and writes it into the URL", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "run-1" });
    expect(window.location.hash).toBe("#/run/run-1");
  });

  it("follows a node deep link into the inspector", async () => {
    window.history.replaceState(null, "", "#/run/run-1/node/effort/0002-second");
    render(<App />);
    await screen.findByText(".scratch/effort/0002-second.md");
  });

  it("resolves a session link to the node it ran for", async () => {
    window.history.replaceState(null, "", "#/run/run-1/session/sess-2");
    render(<App />);
    await waitFor(() =>
      expect(window.location.hash).toBe("#/run/run-1/node/effort/0002-second"),
    );
  });

  it("writes a node selection into the URL when clicked", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "run-1" });
    await userEvent.click(screen.getByRole("button", { name: /first/ }));
    expect(window.location.hash).toBe("#/run/run-1/node/effort/0001-first");
  });

  it("routes a takeover consultation like any node, when one exists", async () => {
    server.details["run-1"] = runDetail("run-1", {
      reconciliations: [reconciliationRecord("0001-effort")],
    });
    render(<App />);
    await screen.findByRole("heading", { name: "run-1" });
    await userEvent.click(screen.getByRole("button", { name: /0001-effort/ }));
    expect(window.location.hash).toBe("#/run/run-1/node/takeover/0001-effort");
    await screen.findByText(/record and the repo disagreed/);
  });

  it("renders no takeover board for a run never taken over", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "run-1" });
    expect(screen.queryByText("takeover")).not.toBeInTheDocument();
  });
});

describe("App states", () => {
  it("shows a retryable error when the server is unreachable", async () => {
    server.runs = null;
    render(<App />);
    await screen.findByText(/server can't be reached/);
    server.runs = [runSummary("run-1")];
    await userEvent.click(screen.getByRole("button", { name: "retry" }));
    await screen.findByRole("heading", { name: "run-1" });
  });

  it("shows a retryable error for a run that can't be loaded", async () => {
    window.history.replaceState(null, "", "#/run/run-gone");
    render(<App />);
    await screen.findByText(/run can't be loaded/);
    expect(screen.getByRole("button", { name: "retry" })).toBeInTheDocument();
  });

  it("shows an error state for a session the run does not know", async () => {
    window.history.replaceState(null, "", "#/run/run-1/session/sess-ghost");
    render(<App />);
    await screen.findByText(/no session sess-ghost/);
    expect(window.location.hash).toBe("#/run/run-1/session/sess-ghost");
  });

  it("surfaces a failing transcript instead of an empty conversation", async () => {
    server.transcriptsFail = true;
    window.history.replaceState(null, "", "#/run/run-1/node/effort/0002-second");
    render(<App />);
    await screen.findByText(/transcript can’t be loaded/);
  });

  it("says so when there are no runs yet", async () => {
    server.runs = [];
    render(<App />);
    await screen.findByText("No runs yet.");
  });

  it("announces a lost connection once it had one", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "run-1" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    act(() => FakeEventSource.instances[0].emit("versions", {}));
    act(() => FakeEventSource.instances[0].die());
    await screen.findByRole("status");
    expect(screen.getByRole("status")).toHaveTextContent(/reconnecting/);
  });
});
