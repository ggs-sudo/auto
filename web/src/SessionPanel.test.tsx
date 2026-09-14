// The history panel under a long unattended run: the conversation renders a
// bounded tail with the rest behind "show earlier"; a node's two histories
// sit behind the tab pair; a takeover key renders the consultation record.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SessionPanel } from "./SessionPanel";
import { reconciliationRecord, runDetail, sessionRecord, textEvent } from "./fixtures";
import { takeoverKey } from "./derive";
import type { InterventionRecord } from "./types";

const noop = () => {};

const intervention = (id: string, node: string): InterventionRecord => ({
  intervention_id: id,
  node,
  trigger: "stale",
  model: "claude-opus-5",
  started_at: "2026-08-29T10:30:00Z",
  ended_at: null,
  prose: "The session stalled on a question it can answer itself.",
  tool_calls: [
    { tool: "send_to_session", arguments: { message: "carry on" }, refused: null },
  ],
  telemetry: {
    cost_usd: null,
    num_turns: null,
    duration_ms: null,
    stop_reason: null,
    terminal_reason: null,
    is_error: null,
  },
});

describe("SessionPanel conversation windowing", () => {
  const run = runDetail("run-1");
  const session = sessionRecord("sess-2", "effort/0002-second");

  it("renders every event of a short transcript", () => {
    render(
      <SessionPanel
        run={run}
        nodeKey="effort/0002-second"
        session={session}
        events={[textEvent("alpha"), textEvent("beta")]}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /earlier/ })).not.toBeInTheDocument();
  });

  it("bounds a long transcript to a recent tail, the rest behind a control", async () => {
    const events = Array.from({ length: 1000 }, (_, i) => textEvent(`event ${i}`));
    render(
      <SessionPanel
        run={run}
        nodeKey="effort/0002-second"
        session={session}
        events={events}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    expect(screen.getByText("event 999")).toBeInTheDocument();
    expect(screen.queryByText("event 0")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".cc-row").length).toBeLessThanOrEqual(260);

    await userEvent.click(screen.getByRole("button", { name: /earlier/ }));
    expect(document.querySelectorAll(".cc-row").length).toBeGreaterThan(260);
  });
});

describe("SessionPanel summary fold", () => {
  const run = runDetail("run-1");
  const session = sessionRecord("sess-2", "effort/0002-second", {
    summary: "A very long recap of everything the session did.",
    highlights: ["settled the seam sketch", "sent /to-spec"],
  });

  it("starts folded and reveals the summary on toggle", async () => {
    render(
      <SessionPanel
        run={run}
        nodeKey="effort/0002-second"
        session={session}
        events={[textEvent("alpha")]}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    expect(screen.queryByText(/very long recap/)).not.toBeInTheDocument();
    expect(screen.queryByText("settled the seam sketch")).not.toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /summary/ });
    await userEvent.click(toggle);
    expect(screen.getByText(/very long recap/)).toBeInTheDocument();
    expect(screen.getByText("settled the seam sketch")).toBeInTheDocument();

    await userEvent.click(toggle);
    expect(screen.queryByText(/very long recap/)).not.toBeInTheDocument();
  });

  it("offers no toggle when there is nothing to fold", () => {
    render(
      <SessionPanel
        run={run}
        nodeKey="effort/0002-second"
        session={sessionRecord("sess-2", "effort/0002-second")}
        events={[textEvent("alpha")]}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    expect(screen.queryByRole("button", { name: /summary/ })).not.toBeInTheDocument();
  });
});

describe("SessionPanel histories", () => {
  it("shows the orchestrator's record for the node behind its tab", async () => {
    const run = runDetail("run-1", {
      interventions: [
        intervention("iv-1", "effort/0002-second"),
        intervention("iv-2", "effort/0001-first"),
      ],
    });
    render(
      <SessionPanel
        run={run}
        nodeKey="effort/0002-second"
        session={sessionRecord("sess-2", "effort/0002-second")}
        events={[textEvent("alpha")]}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    // Only this node's interventions count toward the tab.
    await userEvent.click(screen.getByRole("button", { name: "orchestrator · 1" }));
    expect(
      screen.getByText(/stalled on a question it can answer itself/),
    ).toBeInTheDocument();
    expect(screen.getByText("carry on")).toBeInTheDocument();
    expect(screen.queryByText("alpha")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "session" }));
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });

  it("renders a takeover key as the consultation's record", () => {
    const record = reconciliationRecord("0001-effort");
    const run = runDetail("run-1", { reconciliations: [record] });
    render(
      <SessionPanel
        run={run}
        nodeKey={takeoverKey("0001-effort")}
        session={undefined}
        events={[]}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    expect(screen.getByText("corrected")).toBeInTheDocument();
    expect(screen.getByText(/record and the repo disagreed/)).toBeInTheDocument();
    expect(screen.getByText("effort/0001-first")).toBeInTheDocument();
    expect(screen.getByText(/no committed artifact/)).toBeInTheDocument();
  });

  it("says so for a takeover key whose record is not written yet", () => {
    render(
      <SessionPanel
        run={runDetail("run-1")}
        nodeKey={takeoverKey("0009-ghost")}
        session={undefined}
        events={[]}
        now={Date.now()}
        onAnswered={noop}
      />,
    );
    expect(screen.getByText(/no such consultation/)).toBeInTheDocument();
  });
});
