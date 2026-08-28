// PROTOTYPE — fake "live-ish" behaviour. In the real website this is a file tail / websocket;
// here it's a timer that appends canned events to running sessions. No persistence.
import { useEffect, useMemo, useState } from "react";
import { activeRun } from "./data";
import type { Gate, GateResponse, RunDetail, TranscriptEvent } from "./schema";

const FEED: Record<string, string[]> = {
  "s-05-signup": [
    "Trimmed trailing separators in slugify — rerunning lib/workspace.",
    "3 passed (3) · lib/workspace",
    "Probing uniqueness against the workspaces table with a numeric suffix fallback.",
    "Wrote lib/workspace/slug.ts",
    "acme-inc → taken → acme-inc-2 → free. Behaviour matches the ticket.",
    "pnpm vitest run app/(auth) lib/workspace",
    "9 passed (9)",
    "Updating .scratch/onboarding-revamp/issues/0005-implement-signup.md — Status: Done",
  ],
  "s-06-verify": [
    "Wrote app/(auth)/verify/[token]/page.tsx",
    "Expired-token path renders a resend affordance rather than a dead end.",
    "pnpm vitest run app/(auth)/verify",
    "4 passed (4)",
    "Console transport prints the verification URL — usable until Postmark lands.",
  ],
  "s-09-invite-api": [
    "(idle — session held alive inside gate #2)",
  ],
  "s-07-postmark": [
    "(idle — session held alive inside gate #3)",
  ],
};

export interface LiveRun extends RunDetail {
  /** simulated wall clock, ticks with the feed */
  now: Date;
  respond: (seq: number, response: GateResponse) => void;
}

export function useLiveRun(): LiveRun {
  const [tick, setTick] = useState(0);
  const [responses, setResponses] = useState<Record<number, GateResponse>>({});

  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 2200);
    return () => clearInterval(id);
  }, []);

  return useMemo(() => {
    const transcripts: Record<string, TranscriptEvent[]> = {};
    const base = new Date("2026-08-28T11:34:00Z").getTime();
    const now = new Date(base + tick * 90_000);

    for (const [sid, events] of Object.entries(activeRun.transcripts)) {
      const feed = FEED[sid];
      if (!feed) {
        transcripts[sid] = events;
        continue;
      }
      const extra: TranscriptEvent[] = [];
      for (let i = 0; i < Math.min(tick, feed.length); i++) {
        const text = feed[i];
        extra.push({
          t: new Date(base + i * 90_000).toISOString(),
          kind: text.startsWith("pnpm") ? "tool_use" : text.match(/^\d+ passed/) ? "tool_result" : "assistant",
          ...(text.startsWith("pnpm") ? { tool: "Bash" } : {}),
          text,
        });
      }
      transcripts[sid] = [...events, ...extra];
    }

    const gates: Gate[] = activeRun.gates.map((g) =>
      responses[g.seq] ? { ...g, response: responses[g.seq] } : g,
    );

    return {
      ...activeRun,
      transcripts,
      gates,
      now,
      respond: (seq, response) => setResponses((r) => ({ ...r, [seq]: response })),
    };
  }, [tick, responses]);
}
