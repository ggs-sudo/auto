// The history panel under a long unattended run: the conversation renders a
// bounded tail with the rest behind "show earlier"; a node's two histories
// sit behind the tab pair; a takeover key renders the consultation record.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionPanel } from "./SessionPanel";
import {
  graphNode,
  reconciliationRecord,
  runDetail,
  sessionRecord,
  textEvent,
} from "./fixtures";
import { takeoverKey } from "./derive";
import type { InterventionRecord, UserMessage } from "./types";

const noop = () => {};

const intervention = (id: string, node: string): InterventionRecord => ({
  intervention_id: id,
  node,
  trigger: "stale",
  model: "claude-opus-5",
  session_id: `agent-${id}`,
  message_id: null,
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

describe("SessionPanel chats take messages", () => {
  afterEach(() => vi.restoreAllMocks());

  const message = (over: Partial<UserMessage> = {}): UserMessage => ({
    message_id: "m-1",
    node: "effort/0002-second",
    session_id: "sess-2",
    target: "orchestrator",
    text: "Raise the review, the build is ready.",
    sent_at: "2026-08-29T10:20:00Z",
    delivery: null,
    ...over,
  });

  const panel = (run = runDetail("run-1"), events = [textEvent("alpha")]) =>
    render(
      <SessionPanel
        run={run}
        nodeKey="effort/0002-second"
        session={sessionRecord("sess-2", "effort/0002-second")}
        events={events}
        now={Date.now()}
        onAnswered={noop}
      />,
    );

  it("names the Claude session behind each chat", async () => {
    panel(runDetail("run-1", { interventions: [intervention("iv-1", "effort/0002-second")] }));
    expect(screen.getByText("sess-2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /orchestrator/ }));
    expect(screen.getByText("agent-iv-1")).toBeInTheDocument();
  });

  it("sends the session the user's words, addressed to the session they saw", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 201 }));
    const onAnswered = vi.fn();
    render(
      <SessionPanel
        run={runDetail("run-1")}
        nodeKey="effort/0002-second"
        session={sessionRecord("sess-2", "effort/0002-second")}
        events={[textEvent("alpha")]}
        now={Date.now()}
        onAnswered={onAnswered}
      />,
    );

    await userEvent.type(screen.getByLabelText("message the session"), "approved");
    await userEvent.click(screen.getByRole("button", { name: "send to session" }));

    await waitFor(() => expect(onAnswered).toHaveBeenCalled());
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/runs/run-1/messages");
    expect(JSON.parse(String(init?.body))).toEqual({
      target: "session",
      node: "effort/0002-second",
      session_id: "sess-2",
      text: "approved",
    });
    expect(screen.getByLabelText("message the session")).toHaveValue("");
  });

  it("shows the server's refusal and keeps the words", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: "run run-1 has no live orchestrator" }), {
        status: 409,
      }),
    );
    panel();
    await userEvent.click(screen.getByRole("button", { name: /orchestrator/ }));
    await userEvent.type(screen.getByLabelText("message the orchestrator"), "hello");
    await userEvent.click(screen.getByRole("button", { name: "send to orchestrator" }));
    expect(await screen.findByText(/no live orchestrator/)).toBeInTheDocument();
    expect(screen.getByLabelText("message the orchestrator")).toHaveValue("hello");
  });

  it("offers no box where nothing could deliver the message", () => {
    const gated = runDetail("run-1");
    gated.graphs[0].nodes[1] = graphNode("0002-second", {
      status: "review-pending",
      session_id: "sess-2",
      gate: "gate-1",
    });
    panel(gated);
    expect(screen.queryByLabelText("message the session")).not.toBeInTheDocument();
    expect(screen.getByText(/waiting at a gate/)).toBeInTheDocument();
  });

  it("interleaves the user's messages with the orchestrator's record", async () => {
    panel(
      runDetail("run-1", {
        interventions: [intervention("iv-1", "effort/0002-second")],
        messages: [
          message({
            message_id: "m-2",
            text: "Leave it alone for now.",
            delivery: {
              message_id: "m-2",
              delivered_at: "2026-08-29T10:21:00Z",
              refused: "node effort/0002-second is waiting at gate 0003: answer the gate instead",
              intervention_id: null,
            },
          }),
        ],
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: /orchestrator/ }));
    const entries = [...document.querySelectorAll(".cc-orch-entry")].map(
      (el) => el.textContent ?? "",
    );
    expect(entries[0]).toMatch(/Leave it alone for now\./);
    expect(entries[0]).toMatch(/not delivered: .*answer the gate instead/);
    expect(entries[1]).toMatch(/stalled on a question/);
  });

  it("renders a delivered session message in the transcript as the user's", () => {
    panel(runDetail("run-1"), [
      textEvent("Built it."),
      {
        type: "user",
        auto_user_message: "m-3",
        message: { content: [{ type: "text", text: "approved" }] },
      },
    ]);
    const row = screen.getByText("approved").closest(".cc-row");
    expect(row).toHaveClass("cc-user--direct");
    expect(row?.textContent).toMatch(/^❯?you/);
  });
});
