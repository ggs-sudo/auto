// The gate card's contract: a verb that needs words stays unclickable
// without them, and a server rejection surfaces instead of vanishing.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GateCard } from "./GateCard";
import { gateFixture, runDetail } from "./fixtures";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GateCard", () => {
  it("keeps a words-required verb disabled until words are given", async () => {
    render(
      <GateCard
        run={runDetail("run-1")}
        gate={gateFixture()}
        now={Date.now()}
        onAnswered={() => {}}
      />,
    );
    const answer = screen.getByRole("button", { name: "answer" });
    expect(answer).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "Use Stripe.");
    expect(answer).toBeEnabled();
  });

  it("surfaces the server's rejection under the form", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ error: "gate already answered" }),
    } as Response);
    render(
      <GateCard
        run={runDetail("run-1")}
        gate={gateFixture()}
        now={Date.now()}
        onAnswered={() => {}}
      />,
    );
    await userEvent.type(screen.getByRole("textbox"), "Use Stripe.");
    await userEvent.click(screen.getByRole("button", { name: "answer" }));
    await screen.findByText(/gate already answered/);
  });

  it("shows the recorded response once answered", () => {
    render(
      <GateCard
        run={runDetail("run-1")}
        gate={gateFixture({
          response: { decision: "answer", text: "Use Stripe." },
        })}
        now={Date.now()}
        onAnswered={() => {}}
      />,
    );
    expect(screen.getByText(/“Use Stripe.”/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
