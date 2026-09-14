// PROTOTYPE — throwaway. Variant B, "console": FIXED SPLIT PANES. The graph
// pane scrolls on top; the session is a chat drawer pinned under it with
// height presets (min / ⅓ / ½), like a terminal drawer in an IDE — the tail
// of the conversation is always on screen while you read the graph. Inside
// the drawer, facts + gate sit in a left meta column beside the chat. The
// runs rail collapses to a mini strip of status dots instead of vanishing.

import { useState } from "react";
import { GateCard } from "../GateCard";
import { RunBoards } from "../GraphBoard";
import { Conversation } from "../Inspector";
import { RunsRail } from "../RunsRail";
import { gateById, openGates } from "../derive";
import type { RunSummary } from "../types";
import {
  RunStats,
  SessionFacts,
  inspectNode,
  withoutInitialPrompt,
  type VariantProps,
} from "./shared";
import "./variant-b.css";

type DrawerSize = "min" | "third" | "half";

export function VariantB(p: VariantProps) {
  const [railOpen, setRailOpen] = useState(true);
  const [drawer, setDrawer] = useState<DrawerSize>("third");
  return (
    <div className={`mct vb ${railOpen ? "" : "vb--mini"}`}>
      {railOpen ? (
        <div className="vb-railwrap">
          <button className="vb-collapse" onClick={() => setRailOpen(false)}>
            « collapse
          </button>
          <RunsRail
            runs={p.runs ?? []}
            selected={p.runId}
            now={p.now}
            live={p.live}
            stale={p.runsError != null && p.runs != null}
            onSelect={p.onSelectRun}
          />
        </div>
      ) : (
        <MiniRail
          runs={p.runs ?? []}
          selected={p.runId}
          onSelect={p.onSelectRun}
          onExpand={() => setRailOpen(true)}
        />
      )}
      <div className="vb-right">
        <div className="vb-graphpane">
          {p.everLive && !p.live && (
            <div className="mct-offline" role="status">
              ⚠ connection lost — reconnecting automatically
            </div>
          )}
          <GraphPane {...p} />
        </div>
        <Drawer p={p} size={drawer} onSize={setDrawer} />
      </div>
    </div>
  );
}

function MiniRail({
  runs,
  selected,
  onSelect,
  onExpand,
}: {
  runs: RunSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  onExpand: () => void;
}) {
  return (
    <aside className="vb-minirail">
      <button className="vb-minirail__expand" title="expand runs" onClick={onExpand}>
        »
      </button>
      {runs.map((run) => (
        <button
          key={run.run_id}
          title={run.run_id}
          className={`vb-minirail__run ${run.run_id === selected ? "is-on" : ""}`}
          onClick={() => onSelect(run.run_id)}
        >
          <span className={`mct-dot mct-dot--${run.status}`} />
        </button>
      ))}
    </aside>
  );
}

function GraphPane(p: VariantProps) {
  if (p.detail == null) {
    return (
      <div className="mct-empty">
        <p>{p.detailError ?? p.runsError ?? "Loading…"}</p>
      </div>
    );
  }
  const run = p.detail;
  const gates = openGates(run);
  return (
    <>
      <header className="vb-head">
        <h1>{run.manifest.run_id}</h1>
        <RunStats run={run} now={p.now} />
      </header>
      {gates.length > 0 && (
        <section className="mct-gatestrip">
          <span className="mct-gatestrip__label">needs you</span>
          <div className="mct-gatestrip__cards">
            {gates.map((gate) => (
              <GateCard
                key={gate.gate_id}
                run={run}
                gate={gate}
                now={p.now}
                onAnswered={p.onAnswered}
                onSelectNode={p.onSelectNode}
              />
            ))}
          </div>
        </section>
      )}
      <RunBoards run={run} selected={p.selectedNode} onSelect={p.onSelectNode} />
    </>
  );
}

function Drawer({
  p,
  size,
  onSize,
}: {
  p: VariantProps;
  size: DrawerSize;
  onSize: (size: DrawerSize) => void;
}) {
  const run = p.detail;
  const node = run != null ? inspectNode(run, p.selectedNode) : null;
  const session = p.session;
  return (
    <div className={`vb-drawer vb-drawer--${size}`}>
      <div className="vb-bar">
        {node != null ? (
          <>
            <span className={`mct-type mct-type--${node.type}`}>{node.type}</span>
            <h2>{node.title}</h2>
            {session != null && <SessionFacts session={session} now={p.now} />}
          </>
        ) : (
          <h2>session</h2>
        )}
        <div className="vb-size">
          {(["min", "third", "half"] as const).map((preset) => (
            <button
              key={preset}
              className={size === preset ? "is-on" : ""}
              onClick={() => onSize(preset)}
            >
              {preset === "min" ? "▁" : preset === "third" ? "▄" : "█"}
            </button>
          ))}
        </div>
      </div>
      {size !== "min" && run != null && node != null && (
        <div className="vb-body">
          <div className="vb-meta">
            {(() => {
              const gate = gateById(run, node.gate);
              return gate != null ? (
                <GateCard run={run} gate={gate} now={p.now} onAnswered={p.onAnswered} />
              ) : null;
            })()}
            {session?.summary != null && <p className="vb-meta__summary">{session.summary}</p>}
            {session != null && session.highlights.length > 0 && (
              <ul className="mct-glance__list">
                {session.highlights.map((highlight) => (
                  <li key={highlight}>{highlight}</li>
                ))}
              </ul>
            )}
            {p.transcriptError != null && (
              <div className="mct-transcript-err">⚠ {p.transcriptError}</div>
            )}
          </div>
          {session != null ? (
            <Conversation
              key={session.session_id}
              events={withoutInitialPrompt(p.events)}
              interventions={run.interventions.filter((r) => r.node === p.selectedNode)}
              running={session.status === "running"}
            />
          ) : (
            <div className="mct-empty">
              <p>No session yet.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
