// The inbox's contract: gates waiting on an answer list before pings, the
// reader opens the first by default and switches on a click, and the
// question renders as markdown rather than raw asterisks.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { GateInbox } from "./GateInbox";
import { gateFixture, runDetail } from "./fixtures";

const ping = gateFixture({
  gate_id: "gate-1",
  sequence: 1,
  kind: "user-ping",
  node: null,
  question: "Ticket 02 is closed on the code side. Dispatch the **emailer** deploy.",
});
const question = gateFixture({
  gate_id: "gate-2",
  sequence: 2,
  question: "Which provider?\n\n**1. Stripe or Adyen?**",
});

const renderInbox = () =>
  render(
    <GateInbox
      run={runDetail("run-1")}
      gates={[ping, question]}
      now={Date.now()}
      onAnswered={() => {}}
      onSelectNode={() => {}}
    />,
  );

describe("GateInbox", () => {
  it("lists answers-wanted before pings and opens the first", () => {
    renderInbox();
    const rows = within(screen.getByRole("navigation")).getAllByRole("button");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Which provider?"),
      expect.stringContaining("Ticket 02 is closed on the code side"),
    ]);
    expect(rows[0]).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: "answer" })).toBeInTheDocument();
  });

  it("switches the reader on a click and renders the markdown", async () => {
    renderInbox();
    await userEvent.click(screen.getByText(/Ticket 02 is closed/, { selector: "nav *" }));
    expect(screen.getByRole("button", { name: "dismiss" })).toBeInTheDocument();
    expect(screen.getByText("emailer", { selector: "strong" })).toBeInTheDocument();
    expect(screen.queryByText(/\*\*emailer\*\*/)).not.toBeInTheDocument();
  });
});

describe("GateInbox large reader", () => {
  it("expands into a dialog, keeps the draft, and closes on Escape", async () => {
    renderInbox();
    await userEvent.type(screen.getByRole("textbox"), "Use Stripe.");
    await userEvent.click(screen.getByRole("button", { name: /expand/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("Use Stripe.");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("Use Stripe.");
  });
});
