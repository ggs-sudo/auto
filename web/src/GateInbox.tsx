// The open gates as an inbox: headlines on the left, split into the gates
// waiting on an answer and the pings that only hand you something, and one
// gate open in the reader on the right. Ten stacked walls of text read as
// none; one at a time, with the rest a headline away, reads. A long gate
// can be lifted into a large centred overlay — the same card, moved by
// class alone, so a half-typed answer survives the toggle.

import { useEffect, useState } from "react";
import { GateCard } from "./GateCard";
import { Markdown } from "./Markdown";
import { GATE_LABEL, awaitsAnswer, elapsed, headlineOf, nodeIdOf, titleOf } from "./derive";
import type { Gate, RunDetail } from "./types";

export function GateInbox({
  run,
  gates,
  now,
  onAnswered,
  onSelectNode,
}: {
  run: RunDetail;
  gates: Gate[];
  now: number;
  onAnswered: () => void;
  onSelectNode: (key: string) => void;
}) {
  const asks = gates.filter(awaitsAnswer);
  const pings = gates.filter((gate) => !awaitsAnswer(gate));
  const ordered = [...asks, ...pings];
  const [selected, setSelected] = useState<string | null>(null);
  // An answered gate drops out of `gates`; the reader falls to the first.
  const open = ordered.find((gate) => gate.gate_id === selected) ?? ordered[0];
  const [large, setLarge] = useState(false);

  useEffect(() => {
    if (!large) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setLarge(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [large]);

  const group = (label: string, list: Gate[]) =>
    list.length > 0 && (
      <>
        <div className="mct-inbox__group">
          {label} · {list.length}
        </div>
        {list.map((gate) => (
          <button
            key={gate.gate_id}
            className={`mct-inbox__row mct-inbox__row--${gate.kind}`}
            aria-current={gate.gate_id === open.gate_id}
            onClick={() => setSelected(gate.gate_id)}
          >
            <Markdown inline className="mct-inbox__headline" text={headlineOf(gate.question)} />
            <span className="mct-inbox__meta">
              <span className="mct-inbox__kind">{GATE_LABEL[gate.kind]}</span>
              {gate.node != null && <span>{titleOf(nodeIdOf(gate.node))}</span>}
              <span>{elapsed(gate.raised_at, null, now)}</span>
            </span>
          </button>
        ))}
      </>
    );

  return (
    <section className="mct-inbox">
      <nav className="mct-inbox__list" aria-label="open gates">
        {group("waiting on your answer", asks)}
        {group("for you to do", pings)}
      </nav>
      {large && <div className="mct-inbox__backdrop" onClick={() => setLarge(false)} />}
      <div
        className={`mct-inbox__reader ${large ? "is-large" : ""}`}
        role={large ? "dialog" : undefined}
        aria-modal={large || undefined}
      >
        <button
          className="mct-inbox__expand"
          onClick={() => setLarge(!large)}
          title={large ? "close (Esc)" : "read it large"}
        >
          {large ? "✕ close" : "⤢ expand"}
        </button>
        <GateCard
          key={open.gate_id}
          run={run}
          gate={open}
          now={now}
          onAnswered={onAnswered}
          onSelectNode={onSelectNode}
        />
      </div>
    </section>
  );
}
