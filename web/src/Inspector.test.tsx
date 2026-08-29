// The inspector under a long unattended run: the conversation renders a
// bounded tail with the rest behind "show earlier", so thousands of events
// never become thousands of DOM rows at once.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Inspector } from "./Inspector";
import { runDetail, sessionRecord, textEvent } from "./fixtures";

const noop = () => {};

describe("Inspector conversation windowing", () => {
  const run = runDetail("run-1");
  const session = sessionRecord("sess-2", "effort/0002-second");

  it("renders every event of a short transcript", () => {
    render(
      <Inspector
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
      <Inspector
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
    expect(document.querySelectorAll(".mct-ev").length).toBeLessThanOrEqual(260);

    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(screen.getByRole("button", { name: /earlier/ }));
    expect(document.querySelectorAll(".mct-ev").length).toBeGreaterThan(260);
  });
});
