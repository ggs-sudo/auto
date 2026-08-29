// Variant E from the prototype review, built for real: three panes — runs
// rail, the execution graph centre-stage, session inspector — wearing the
// terminal skin. The graph is the run: parallelism, blockage and readiness
// are legible without navigating.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchRun, fetchRuns, fetchTranscript, useChangeStream } from "./api";
import { ROOT_KEY, graphNodeAt, sessionById } from "./derive";
import { Inspector } from "./Inspector";
import { RunBoards } from "./GraphBoard";
import { RunHeader } from "./RunHeader";
import { RunsRail } from "./RunsRail";
import type { RunDetail, RunSummary, StreamEvent } from "./types";
import "./app.css";

interface Transcript {
  sessionId: string;
  events: StreamEvent[];
  offset: number;
}

export function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [selectedNode, setSelectedNode] = useState<string>(ROOT_KEY);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const refreshRuns = useCallback(async () => {
    const listed = await fetchRuns();
    setRuns(listed);
    setSelectedRun((current) => current ?? listed[0]?.run_id ?? null);
  }, []);

  const refreshDetail = useCallback(async (runId: string) => {
    setDetail(await fetchRun(runId));
  }, []);

  useEffect(() => {
    refreshRuns().catch(() => {});
  }, [refreshRuns]);

  useEffect(() => {
    if (selectedRun == null) return;
    setDetail(null);
    setSelectedNode(ROOT_KEY);
    setTranscript(null);
    refreshDetail(selectedRun).catch(() => {});
  }, [selectedRun, refreshDetail]);

  const live = useChangeStream(
    useCallback(
      (runId: string) => {
        refreshRuns().catch(() => {});
        if (runId === selectedRun) refreshDetail(runId).catch(() => {});
      },
      [refreshRuns, refreshDetail, selectedRun],
    ),
    useCallback(() => {
      refreshRuns().catch(() => {});
      if (selectedRun != null) refreshDetail(selectedRun).catch(() => {});
    }, [refreshRuns, refreshDetail, selectedRun]),
  );

  // Tail the selected node's transcript: from scratch when the session
  // changes, incrementally whenever the run moves.
  const session = useMemo(() => {
    if (detail == null) return undefined;
    const sessionId =
      selectedNode === ROOT_KEY
        ? detail.manifest.root_node.session_id
        : (graphNodeAt(detail, selectedNode)?.node.session_id ?? null);
    return sessionById(detail, sessionId);
  }, [detail, selectedNode]);

  const transcriptRef = useRef<Transcript | null>(null);
  transcriptRef.current = transcript;
  const sessionId = session?.session_id;
  const version = detail?.version;
  useEffect(() => {
    if (selectedRun == null || sessionId == null) return;
    const runId = selectedRun;
    const known = transcriptRef.current;
    const after = known?.sessionId === sessionId ? known.offset : 0;
    let cancelled = false;
    fetchTranscript(runId, sessionId, after)
      .then((tail) => {
        if (cancelled) return;
        setTranscript((latest) => {
          const continuing = latest?.sessionId === sessionId;
          if (continuing && tail.events.length === 0) return latest;
          const base = continuing ? latest.events : [];
          return { sessionId, events: [...base, ...tail.events], offset: tail.offset };
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [selectedRun, sessionId, version]);

  return (
    <div className="mct">
      <RunsRail
        runs={runs}
        selected={selectedRun}
        now={now}
        live={live}
        onSelect={setSelectedRun}
      />
      <main className="mct-main">
        {detail == null ? (
          <div className="mct-empty">
            <p>{runs.length === 0 ? "No runs yet." : "Loading…"}</p>
            {runs.length === 0 && (
              <p className="mct-sub">Start one with `auto run`, then watch it here.</p>
            )}
          </div>
        ) : (
          <>
            <RunHeader run={detail} now={now} onSelectNode={setSelectedNode} />
            <RunBoards run={detail} selected={selectedNode} onSelect={setSelectedNode} />
          </>
        )}
      </main>
      <Inspector
        run={detail}
        nodeKey={selectedNode}
        session={session}
        events={transcript?.sessionId === session?.session_id ? (transcript?.events ?? []) : []}
        now={now}
      />
    </div>
  );
}
