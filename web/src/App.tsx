// The shell: a toggleable runs rail beside one scrolling column — header,
// open gates, the execution graph centre-stage, and the selected node's
// history panel underneath. The graph is the run: parallelism, blockage and
// readiness are legible without navigating; reading a node means scrolling
// to the panel below, not switching context to a side pane.
//
// The URL hash is the selection's source of truth (see router.ts): clicks
// navigate, navigation selects, and a pasted link lands on the same run and
// node — takeover consultations included, which live in their own key
// namespace. Fetch failures render as states, never as a silently blank
// pane.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchRun, fetchRuns, fetchTranscript, useChangeStream } from "./api";
import { ROOT_KEY, graphNodeAt, isTakeoverKey, sessionById } from "./derive";
import { SessionPanel } from "./SessionPanel";
import { RunBoards } from "./GraphBoard";
import { RunHeader } from "./RunHeader";
import { RunsRail } from "./RunsRail";
import { formatHash, parseHash, type Route } from "./router";
import type { RunDetail, RunSummary, StreamEvent } from "./types";
import "./app.css";

interface Transcript {
  sessionId: string;
  events: StreamEvent[];
  offset: number;
}

const reasonOf = (err: unknown) => (err instanceof Error ? err.message : String(err));

export function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(true);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  // The hash drives selection; back and forward just work.
  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = useCallback((next: Route, replace = false) => {
    const hash = formatHash(next);
    if (hash === window.location.hash) return;
    if (replace) {
      // replaceState fires no hashchange, so mirror the state by hand —
      // used for defaults and canonicalising, which deserve no history entry.
      window.history.replaceState(null, "", hash);
      setRoute(next);
    } else {
      window.location.hash = hash;
    }
  }, []);

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await fetchRuns());
      setRunsError(null);
    } catch (err) {
      setRunsError(reasonOf(err));
    }
  }, []);

  const refreshDetail = useCallback(async (runId: string) => {
    try {
      setDetail(await fetchRun(runId));
      setDetailError(null);
    } catch (err) {
      setDetailError(reasonOf(err));
    }
  }, []);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  // No run in the URL: land on the newest one, without a history entry.
  useEffect(() => {
    if (route.runId == null && runs != null && runs.length > 0) {
      navigate({ runId: runs[0].run_id, node: ROOT_KEY, session: null }, true);
    }
  }, [route.runId, runs, navigate]);

  const runId = route.runId;
  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    setTranscript(null);
    setTranscriptError(null);
    if (runId != null) void refreshDetail(runId);
  }, [runId, refreshDetail]);

  // A session URL: resolve it to the node it ran for, then canonicalise.
  // An id the run doesn't know stays in the URL and renders as an error
  // state — the record may simply not be written yet, and a refetch retries.
  useEffect(() => {
    if (route.session == null || detail == null || route.runId == null) return;
    if (detail.manifest.run_id !== route.runId) return;
    const record = sessionById(detail, route.session);
    if (record == null) return;
    navigate({ runId: route.runId, node: record.node, session: null }, true);
  }, [route, detail, navigate]);
  const missingSession =
    route.session != null &&
    detail != null &&
    detail.manifest.run_id === route.runId &&
    sessionById(detail, route.session) == null
      ? route.session
      : null;

  const refreshAll = useCallback(() => {
    void refreshRuns();
    if (runId != null) void refreshDetail(runId);
  }, [refreshRuns, refreshDetail, runId]);

  const live = useChangeStream(
    useCallback(
      (movedRun: string) => {
        void refreshRuns();
        if (movedRun === runId) void refreshDetail(movedRun);
      },
      [refreshRuns, refreshDetail, runId],
    ),
    refreshAll,
  );

  // "Reconnecting" is only meaningful once a connection has existed;
  // before that the stream is simply still opening.
  const [everLive, setEverLive] = useState(false);
  useEffect(() => {
    if (live) setEverLive(true);
  }, [live]);

  const selectedNode = route.node;
  const selectNode = useCallback(
    (key: string) => {
      if (runId != null) navigate({ runId, node: key, session: null });
    },
    [runId, navigate],
  );
  const selectRun = useCallback(
    (id: string) => navigate({ runId: id, node: ROOT_KEY, session: null }),
    [navigate],
  );

  // Tail the selected node's transcript: from scratch when the session
  // changes, incrementally whenever the run moves. A takeover key names a
  // consultation, not a session — its record carries its own content.
  const session = useMemo(() => {
    if (detail == null || isTakeoverKey(selectedNode)) return undefined;
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
    if (runId == null || sessionId == null) return;
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
      .then(() => {
        if (!cancelled) setTranscriptError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setTranscriptError(reasonOf(err));
      });
    return () => {
      cancelled = true;
    };
  }, [runId, sessionId, version]);

  return (
    <div className={`mct ${railOpen ? "" : "mct--norail"}`}>
      {railOpen && (
        <RunsRail
          runs={runs ?? []}
          selected={runId}
          now={now}
          live={live}
          stale={runsError != null && runs != null}
          onSelect={selectRun}
        />
      )}
      <main className="mct-main">
        <div className="mct-topbar">
          <button
            className="mct-railtoggle"
            onClick={() => setRailOpen((open) => !open)}
          >
            {railOpen ? "◧ hide runs" : "◨ runs"}
          </button>
          {everLive && !live && (
            <span className="mct-offline" role="status">
              ⚠ connection lost — reconnecting automatically; what you see may be stale
            </span>
          )}
        </div>
        <MainPane
          runs={runs}
          runsError={runsError}
          detail={detail}
          detailError={detailError}
          missingSession={missingSession}
          runId={runId}
          now={now}
          session={session}
          events={
            transcript?.sessionId === session?.session_id
              ? (transcript?.events ?? [])
              : []
          }
          transcriptError={transcriptError}
          onRetryRuns={refreshRuns}
          onRetryDetail={() => runId != null && void refreshDetail(runId)}
          onSelectNode={selectNode}
          onAnswered={refreshAll}
          selectedNode={selectedNode}
        />
      </main>
    </div>
  );
}

function MainPane({
  runs,
  runsError,
  detail,
  detailError,
  missingSession,
  runId,
  now,
  session,
  events,
  transcriptError,
  onRetryRuns,
  onRetryDetail,
  onSelectNode,
  onAnswered,
  selectedNode,
}: {
  runs: RunSummary[] | null;
  runsError: string | null;
  detail: RunDetail | null;
  detailError: string | null;
  missingSession: string | null;
  runId: string | null;
  now: number;
  session: ReturnType<typeof sessionById>;
  events: StreamEvent[];
  transcriptError: string | null;
  onRetryRuns: () => void;
  onRetryDetail: () => void;
  onSelectNode: (key: string) => void;
  onAnswered: () => void;
  selectedNode: string;
}) {
  if (missingSession != null && detail != null) {
    return (
      <ErrorPane
        note={`This run has no session ${missingSession} — its record may not be written yet`}
        onRetry={onRetryDetail}
      />
    );
  }
  if (detail != null) {
    return (
      <>
        <RunHeader run={detail} now={now} onSelectNode={onSelectNode} onAnswered={onAnswered} />
        <RunBoards run={detail} selected={selectedNode} onSelect={onSelectNode} />
        <SessionPanel
          run={detail}
          nodeKey={selectedNode}
          session={session}
          events={events}
          transcriptError={transcriptError}
          now={now}
          onAnswered={onAnswered}
        />
      </>
    );
  }
  if (detailError != null) {
    return (
      <ErrorPane note={`This run can't be loaded — ${detailError}`} onRetry={onRetryDetail} />
    );
  }
  if (runs == null && runsError != null) {
    return (
      <ErrorPane note={`The server can't be reached — ${runsError}`} onRetry={onRetryRuns} />
    );
  }
  return (
    <div className="mct-empty">
      {runs != null && runs.length === 0 ? (
        <>
          <p>No runs yet.</p>
          <p className="mct-sub">Start one with `auto run`, then watch it here.</p>
        </>
      ) : (
        <p>{runId == null ? "Loading runs…" : "Loading run…"}</p>
      )}
    </div>
  );
}

function ErrorPane({ note, onRetry }: { note: string; onRetry: () => void }) {
  return (
    <div className="mct-empty mct-errpane">
      <p>⚠ {note}</p>
      <button className="mct-retry" onClick={onRetry}>
        retry
      </button>
    </div>
  );
}
