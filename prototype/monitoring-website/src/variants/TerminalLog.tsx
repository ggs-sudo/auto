// PROTOTYPE variant B — "Terminal log". Design lifted from ~/workspace/agent-benchmarks/view/results.html:
// one narrow monospace column on near-black, everything is a collapsible row, no cards, no chrome.
// The whole website is one page; tabs swap what the column lists.
import { useEffect, useRef, useState } from "react";
import { useLiveRun } from "../mock/live";
import { runSummaries, runs } from "../mock/data";
import {
  GATE_LABEL,
  PROGRESS,
  clock,
  elapsed,
  gateFor,
  isReady,
  money,
  openGates,
  sessionFor,
  stamp,
} from "../mock/format";
import type { Gate, GraphNode, SessionRecord, TranscriptEvent } from "../mock/schema";
import "./terminal-log.css";

type Tab = "runs" | "gates" | "sessions";

const ST: Record<string, string> = {
  running: "tl-ok",
  "in-progress": "tl-ok",
  done: "tl-blue",
  succeeded: "tl-blue",
  gated: "tl-warn",
  pending: "tl-dim",
  failed: "tl-bad",
  aborted: "tl-dim",
};

export function TerminalLog() {
  const run = useLiveRun();
  const [tab, setTab] = useState<Tab>("runs");
  const [q, setQ] = useState("");
  const [openRun, setOpenRun] = useState<string | null>(run.manifest.run_id);
  const gates = openGates(run);

  return (
    <div className="tl">
      <div className="tl-col">
        <div className="tl-head">
          <span>auto ▸ monitor</span>
          <span className="tl-tabs">
            {(["runs", "gates", "sessions"] as Tab[]).map((t) => (
              <span key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                [{t}
                {t === "gates" && gates.length ? ` ${gates.length}` : ""}]
              </span>
            ))}
          </span>
        </div>

        {tab === "runs" && (
          <>
            <input
              className="tl-q"
              placeholder="filter by run id or prompt…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {(() => {
              const hit = (id: string, prompt: string) =>
                (id + " " + prompt).toLowerCase().includes(q.trim().toLowerCase());
              const live = runs.filter((r) => hit(r.run_id, r.prompt) && (r.status === "running" || r.status === "gated"));
              const past = runs.filter((r) => hit(r.run_id, r.prompt) && !(r.status === "running" || r.status === "gated"));
              return (
                <>
                  <div className="tl-secttl">live · {live.length}</div>
                  {live.map((r) => (
                    <RunRow key={r.run_id} r={r} open={openRun === r.run_id} onToggle={setOpenRun} run={run} />
                  ))}
                  <div className="tl-secttl" style={{ marginTop: 22 }}>
                    finished · {past.length}
                  </div>
                  {past.map((r) => (
                    <RunRow key={r.run_id} r={r} open={openRun === r.run_id} onToggle={setOpenRun} run={run} dimmed />
                  ))}
                </>
              );
            })()}
          </>
        )}

        {tab === "gates" && (
          <>
            <div className="tl-secttl">open · {gates.length}</div>
            {gates.map((g) => (
              <GateRow key={g.seq} gate={g} run={run} defaultOpen />
            ))}
            <div className="tl-secttl" style={{ marginTop: 22 }}>
              answered · {run.gates.length - gates.length}
            </div>
            {run.gates
              .filter((g) => g.response)
              .map((g) => (
                <GateRow key={g.seq} gate={g} run={run} />
              ))}
          </>
        )}

        {tab === "sessions" && (
          <>
            <div className="tl-secttl">sessions · {run.sessions.length}</div>
            {run.sessions.map((s) => (
              <SessionRow key={s.session_id} s={s} events={run.transcripts[s.session_id] ?? []} now={run.now} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function RunRow({
  r,
  open,
  onToggle,
  run,
  dimmed,
}: {
  r: (typeof runs)[number];
  open: boolean;
  onToggle: (id: string | null) => void;
  run: ReturnType<typeof useLiveRun>;
  dimmed?: boolean;
}) {
  const s = runSummaries[r.run_id];
  const isActive = r.run_id === run.manifest.run_id;
  return (
    <div className={`tl-row ${dimmed ? "tl-row--dim" : ""}`}>
      <div className="tl-rowhd" onClick={() => onToggle(open ? null : r.run_id)}>
        <span className={`tl-st ${ST[r.status]}`}>{r.status === "gated" ? "◆" : "●"}</span>
        <span className="tl-slug">{r.run_id}</span>
        {s.openGates > 0 && <span className="tl-m tl-warn">{s.openGates} gate{s.openGates > 1 ? "s" : ""}</span>}
        <span className="tl-m">
          {s.done}/{s.nodes}
        </span>
        <span className="tl-m">{money(s.cost)}</span>
        <span className="tl-m">{r.route}</span>
        <span className="tl-m">{elapsed(r.created_at, r.ended_at ?? run.now)}</span>
        <span className="tl-m">{stamp(r.created_at)}</span>
      </div>
      {open && (
        <div className="tl-body">
          <pre className="tl-doc">{r.prompt}</pre>
          <div className="tl-kv">
            <span>target</span>
            <span>{r.target_repo}</span>
            <span>worktree</span>
            <span>{r.worktree}</span>
            <span>branch</span>
            <span>{r.branch}</span>
            <span>phase</span>
            <span>{r.phase}</span>
            <span>state dir</span>
            <span>~/.auto/runs/{r.run_id}/</span>
          </div>
          {isActive ? <ActiveRunBody run={run} /> : <p className="tl-muted">no state captured for this run in the fixture.</p>}
        </div>
      )}
    </div>
  );
}

function ActiveRunBody({ run }: { run: ReturnType<typeof useLiveRun> }) {
  const p = PROGRESS(run);
  const gates = openGates(run);
  return (
    <>
      <h3>gates {gates.length ? `· ${gates.length} open` : ""}</h3>
      {run.gates.map((g) => (
        <GateRow key={g.seq} gate={g} run={run} compact defaultOpen={!g.response} />
      ))}
      {run.graphs.map((g) => (
        <div key={g.graph_id}>
          <h3>
            .scratch/{g.graph_id}/ · {g.nodes.filter((n) => n.status === "done").length}/{g.nodes.length}
            {g.spawned_by_node ? ` · subgraph of ${g.spawned_by_node}` : ""}
          </h3>
          {g.nodes.map((n) => (
            <NodeLine key={n.id} n={n} graphId={g.graph_id} run={run} ready={isReady(g, n)} />
          ))}
        </div>
      ))}
      <h3>totals</h3>
      <div className="tl-kv">
        <span>nodes</span>
        <span>
          {p.done} done · {p.running} in-progress · {p.pending} pending
        </span>
        <span>sessions</span>
        <span>
          {run.sessions.length} · {p.turns} turns · {money(p.cost)}
        </span>
      </div>
    </>
  );
}

function NodeLine({
  n,
  graphId,
  run,
  ready,
}: {
  n: GraphNode;
  graphId: string;
  run: ReturnType<typeof useLiveRun>;
  ready: boolean;
}) {
  const [open, setOpen] = useState(false);
  const session = sessionFor(run, n);
  const gate = gateFor(run, graphId, n.id);
  const mark =
    n.status === "done" ? "✓" : n.status === "in-progress" ? "▸" : n.status === "failed" ? "✗" : "·";
  return (
    <>
      <div className={`tl-node ${open ? "on" : ""}`} onClick={() => setOpen(!open)}>
        <span className={ST[n.status]}>{mark}</span>
        <span className="tl-node__id">{n.id}</span>
        <span className="tl-node__title">{n.title}</span>
        <span className="tl-m">{n.type}</span>
        {n.resolution_mode === "user" && <span className="tl-m tl-warn">user-task</span>}
        {n.spawned_graph && n.spawned_graph !== graphId && <span className="tl-m">↳{n.spawned_graph}</span>}
        {ready && <span className="tl-m tl-ok">ready</span>}
        {gate && <span className="tl-m tl-warn">gate #{gate.seq}</span>}
        {n.deps.length > 0 && <span className="tl-m tl-dim">← {n.deps.join(" ")}</span>}
      </div>
      {open && (
        <div className="tl-nodebody">
          {session ? (
            <SessionBody s={session} events={run.transcripts[session.session_id] ?? []} now={run.now} />
          ) : (
            <p className="tl-muted">
              not dispatched — {ready ? "ready, waiting for a slot" : `blocked by ${n.deps.join(", ")}`}
            </p>
          )}
        </div>
      )}
    </>
  );
}

function SessionRow({ s, events, now }: { s: SessionRecord; events: TranscriptEvent[]; now: Date }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="tl-row">
      <div className="tl-rowhd" onClick={() => setOpen(!open)}>
        <span className={`tl-st ${ST[s.status]}`}>●</span>
        <span className="tl-slug">{s.ticket}</span>
        <span className="tl-m">{s.role}</span>
        <span className="tl-m">{s.num_turns} turns</span>
        <span className="tl-m">{money(s.cost_usd)}</span>
        <span className="tl-m">{elapsed(s.started_at, s.ended_at ?? now)}</span>
      </div>
      {open && (
        <div className="tl-body">
          <SessionBody s={s} events={events} now={now} />
        </div>
      )}
    </div>
  );
}

function SessionBody({ s, events, now }: { s: SessionRecord; events: TranscriptEvent[]; now: Date }) {
  return (
    <>
      <div className="tl-kv">
        <span>session</span>
        <span>{s.session_id}</span>
        <span>role</span>
        <span>
          {s.role} {s.monitored ? "· monitored (full trace read)" : "· autonomous (tail read)"}
        </span>
        <span>status</span>
        <span className={ST[s.status]}>
          {s.status} · {elapsed(s.started_at, s.ended_at ?? now)} · {s.num_turns} turns · {money(s.cost_usd)}
        </span>
        <span>transcript</span>
        <span>{s.transcript}</span>
      </div>
      <h3>summary</h3>
      <pre className="tl-doc">{s.summary}</pre>
      <h3>at a glance</h3>
      {s.highlights.map((h) => (
        <div className="tl-hl" key={h}>
          <span className="tl-dim">·</span> {h}
        </div>
      ))}
      <h3>conversation{s.status === "running" ? " · live" : ""}</h3>
      <Term events={events} live={s.status === "running"} />
    </>
  );
}

function Term({ events, live }: { events: TranscriptEvent[]; live: boolean }) {
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (live) box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [events.length, live]);
  return (
    <pre className="tl-term" ref={box}>
      {events.map((e, i) => (
        <span key={i} className={`tl-ev tl-ev--${e.kind}`}>
          <span className="tl-dim">{clock(e.t)} </span>
          <span className="tl-evk">{(e.tool ?? e.kind).padEnd(12, " ").slice(0, 12)}</span>
          {e.text}
          {"\n"}
        </span>
      ))}
      {live && <span className="tl-blink">█</span>}
    </pre>
  );
}

function GateRow({
  gate,
  run,
  compact,
  defaultOpen,
}: {
  gate: Gate;
  run: ReturnType<typeof useLiveRun>;
  compact?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!defaultOpen);
  const [text, setText] = useState("");
  const answered = !!gate.response;
  const send = (decision: string) =>
    run.respond(gate.seq, { responded_at: new Date().toISOString(), decision: decision as any, text });

  return (
    <div className={`tl-row ${compact ? "tl-row--compact" : ""} ${answered ? "tl-row--dim" : ""}`}>
      <div className="tl-rowhd" onClick={() => setOpen(!open)}>
        <span className={`tl-st ${answered ? "tl-dim" : "tl-warn"}`}>{answered ? "✓" : "◆"}</span>
        <span className="tl-slug">
          #{gate.seq} {GATE_LABEL[gate.kind]}
        </span>
        <span className="tl-m">{gate.on_node ?? "run-level"}</span>
        {!answered && <span className="tl-m tl-warn">{elapsed(gate.opened_at, run.now)} waiting</span>}
        {gate.blocking.length > 0 && <span className="tl-m">holds {gate.blocking.length}</span>}
        <span className="tl-m">{stamp(gate.opened_at)}</span>
      </div>
      {open && (
        <div className="tl-body">
          <pre className="tl-doc">{gate.question}</pre>
          {gate.artifact && (
            <p style={{ marginTop: 8 }}>
              artifact:{" "}
              <a href={gate.artifact} target="_blank" rel="noreferrer">
                {gate.artifact} ↗
              </a>
            </p>
          )}
          {gate.blocking.length > 0 && (
            <p className="tl-muted" style={{ marginTop: 8 }}>
              blocking downstream: {gate.blocking.join(", ")} — everything else keeps running.
            </p>
          )}
          {answered ? (
            <>
              <h3>response · {stamp(gate.response!.responded_at)}</h3>
              <pre className="tl-doc">
                {gate.response!.decision}
                {gate.response!.text ? ` — ${gate.response!.text}` : ""}
              </pre>
            </>
          ) : (
            <>
              <h3>respond</h3>
              <textarea
                className="tl-ta"
                rows={3}
                value={text}
                placeholder={gate.kind === "prototype-review" ? "what to keep, what to change…" : "your answer…"}
                onChange={(e) => setText(e.target.value)}
              />
              <div className="tl-acts">
                {gate.kind === "prototype-review" && (
                  <>
                    <button className="tl-act" onClick={() => send("approve")}>
                      [ approve ]
                    </button>
                    <button className="tl-act" disabled={!text.trim()} onClick={() => send("revise")}>
                      [ request revision ]
                    </button>
                  </>
                )}
                {gate.kind === "task-completion" && (
                  <>
                    <button className="tl-act" disabled={!text.trim()} onClick={() => send("done")}>
                      [ done ]
                    </button>
                    <button className="tl-act" onClick={() => send("cannot")}>
                      [ can't do it ]
                    </button>
                  </>
                )}
                {(gate.kind === "escalated-question" || gate.kind === "ping") && (
                  <button className="tl-act" disabled={!text.trim()} onClick={() => send("answered")}>
                    [ send answer ]
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
