// PROTOTYPE — throwaway. Variant A, "flow": the run is ONE SCROLLING
// DOCUMENT. Header → gates → graph → session, top to bottom, full width;
// the conversation is the last section of the page, not a sibling pane.
// The runs rail toggles fully away via a button in the top strip.

import { useState } from "react";
import { GateCard } from "../GateCard";
import { RunBoards } from "../GraphBoard";
import { RunsRail } from "../RunsRail";
import { gateById, openGates } from "../derive";
import { CleanConversation, OrchestratorView, TakeoverView } from "./CleanConversation";
import {
  RunStats,
  SessionFacts,
  inspectNode,
  reconciliationsOf,
  withoutInitialPrompt,
  type VariantProps,
} from "./shared";
import "./variant-a.css";

export function VariantA(p: VariantProps) {
  const [railOpen, setRailOpen] = useState(true);
  return (
    <div className={`mct va ${railOpen ? "" : "va--norail"}`}>
      {railOpen && (
        <RunsRail
          runs={p.runs ?? []}
          selected={p.runId}
          now={p.now}
          live={p.live}
          stale={p.runsError != null && p.runs != null}
          onSelect={p.onSelectRun}
        />
      )}
      <main className="va-main">
        <div className="va-top">
          <button className="va-toggle" onClick={() => setRailOpen((open) => !open)}>
            {railOpen ? "◧ hide runs" : "◨ runs"}
          </button>
          {p.everLive && !p.live && (
            <span className="va-offline">⚠ connection lost — reconnecting</span>
          )}
        </div>
        <Body {...p} />
      </main>
    </div>
  );
}

function Body(p: VariantProps) {
  // The takeover node lives outside the hash router on purpose: it is not a
  // graph node, just a prototype selection. A real graph click clears it.
  const [takeoverOpen, setTakeoverOpen] = useState(false);
  if (p.missingSession != null && p.detail != null) {
    return (
      <div className="mct-empty">
        <p>⚠ This run has no session {p.missingSession}.</p>
      </div>
    );
  }
  if (p.detail == null) {
    return (
      <div className="mct-empty">
        <p>{p.detailError ?? p.runsError ?? "Loading…"}</p>
      </div>
    );
  }
  const run = p.detail;
  const gates = openGates(run);
  const reconciliations = reconciliationsOf(run);
  const selectNode = (key: string) => {
    setTakeoverOpen(false);
    p.onSelectNode(key);
  };
  return (
    <>
      <header className="va-head">
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
      <RunBoards
        run={run}
        selected={takeoverOpen ? "" : p.selectedNode}
        onSelect={selectNode}
      />
      {reconciliations.length > 0 && (
        <section className="mct-board va-takeover">
          <h3 className="mct-board__title">takeover</h3>
          <div className="mct-board__canvas">
            <button
              className={`mct-node va-tknode ${takeoverOpen ? "is-on" : ""}`}
              onClick={() => setTakeoverOpen(true)}
            >
              <span className="mct-type va-type-takeover">takeover</span>
              <span className="mct-node__title">
                reconciliation — {reconciliations[reconciliations.length - 1].effort}
              </span>
              <span className="mct-node__foot">
                <span>{reconciliations.length} consultation{reconciliations.length > 1 ? "s" : ""}</span>
                <span className="mct-tag">
                  latest: {reconciliations[reconciliations.length - 1].verdict}
                </span>
              </span>
            </button>
          </div>
        </section>
      )}
      {takeoverOpen ? (
        <TakeoverSection reconciliations={reconciliations} />
      ) : (
        <SessionSection {...p} />
      )}
    </>
  );
}

function TakeoverSection({
  reconciliations,
}: {
  reconciliations: ReturnType<typeof reconciliationsOf>;
}) {
  const latest = reconciliations[reconciliations.length - 1];
  return (
    <section className="va-session">
      <h3 className="mct-board__title">takeover</h3>
      <div className="va-session__panel">
        <div className="va-session__head">
          <span className="mct-type va-type-takeover">takeover</span>
          <h2>reconciliation — {latest.effort}</h2>
          <span>{reconciliations.length} consultation{reconciliations.length > 1 ? "s" : ""}</span>
          <span>latest: {latest.model}</span>
        </div>
        <TakeoverView reconciliations={reconciliations} />
      </div>
    </section>
  );
}

// The two histories every node has: the skill session's own transcript,
// and the orchestrator's interventions on it. One panel, two tabs.
type HistoryTab = "session" | "orchestrator";

function SessionSection(p: VariantProps) {
  const [tab, setTab] = useState<HistoryTab>("session");
  const run = p.detail!;
  const node = inspectNode(run, p.selectedNode);
  if (node == null) {
    return (
      <section className="va-session">
        <h3 className="mct-board__title">session</h3>
        <div className="mct-empty">
          <p>Select a node.</p>
        </div>
      </section>
    );
  }
  const gate = gateById(run, node.gate);
  const interventions = run.interventions.filter((r) => r.node === p.selectedNode);
  const session = p.session;
  return (
    <section className="va-session">
      <h3 className="mct-board__title">session</h3>
      <div className="va-session__panel">
        <div className="va-session__head">
          <span className={`mct-type mct-type--${node.type}`}>{node.type}</span>
          <h2>{node.title}</h2>
          {session != null && <SessionFacts session={session} now={p.now} />}
          <div className="va-tabs">
            <button
              className={`va-tab ${tab === "session" ? "is-on" : ""}`}
              onClick={() => setTab("session")}
            >
              session
            </button>
            <button
              className={`va-tab va-tab--orch ${tab === "orchestrator" ? "is-on" : ""}`}
              onClick={() => setTab("orchestrator")}
            >
              orchestrator{interventions.length > 0 && ` · ${interventions.length}`}
            </button>
          </div>
        </div>
        {gate != null && (
          <GateCard run={run} gate={gate} now={p.now} onAnswered={p.onAnswered} />
        )}
        {tab === "session" ? (
          <>
            {session?.summary != null && (
              <p className="va-session__summary">{session.summary}</p>
            )}
            {p.transcriptError != null && (
              <div className="mct-transcript-err">
                ⚠ transcript can’t be loaded — {p.transcriptError}
              </div>
            )}
            {session != null ? (
              <CleanConversation
                key={session.session_id}
                events={withoutInitialPrompt(p.events)}
                running={session.status === "running"}
              />
            ) : (
              <div className="mct-empty">
                <p>No session yet.</p>
              </div>
            )}
          </>
        ) : (
          <OrchestratorView interventions={interventions} />
        )}
      </div>
    </section>
  );
}
