// Talking to the auto serve API: fetches plus the SSE change stream.
// The stream carries only "run X moved" — the client refetches what it is
// showing. EventSource reconnects on its own; every (re)connection opens
// with a `versions` baseline, which doubles as a resync signal.

import { useEffect, useRef, useState } from "react";
import type { GateDecision, RunDetail, RunSummary, TranscriptTail } from "./types";

export async function fetchRuns(): Promise<RunSummary[]> {
  const response = await fetch("/api/runs");
  if (!response.ok) throw new Error(`runs: ${response.status}`);
  return response.json();
}

export async function fetchRun(runId: string): Promise<RunDetail> {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  if (!response.ok) throw new Error(`run ${runId}: ${response.status}`);
  return response.json();
}

export async function fetchTranscript(
  runId: string,
  sessionId: string,
  after: number,
): Promise<TranscriptTail> {
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/transcripts/${encodeURIComponent(sessionId)}?after=${after}`,
  );
  if (!response.ok) throw new Error(`transcript ${sessionId}: ${response.status}`);
  return response.json();
}

/** Subscribe to change notifications. `onChange(runId)` fires per moved run;
 * `onResync()` fires on every (re)connection baseline. */
export function useChangeStream(
  onChange: (runId: string) => void,
  onResync: () => void,
): boolean {
  const [live, setLive] = useState(false);
  const handlers = useRef({ onChange, onResync });
  handlers.current = { onChange, onResync };

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.addEventListener("versions", () => {
      setLive(true);
      handlers.current.onResync();
    });
    source.addEventListener("change", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as { run_id: string };
      handlers.current.onChange(data.run_id);
    });
    source.onerror = () => setLive(false);
    return () => source.close();
  }, []);

  return live;
}

/** The one write the site ever makes: a gate's response file, via the server.
 * Non-2xx becomes an Error carrying the server's reason. */
export async function submitGateResponse(
  runId: string,
  gateId: string,
  decision: GateDecision,
  text: string,
): Promise<void> {
  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/gates/${encodeURIComponent(gateId)}/response`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, text }),
    },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: string;
    } | null;
    throw new Error(body?.error ?? `gate ${gateId}: ${response.status}`);
  }
}
