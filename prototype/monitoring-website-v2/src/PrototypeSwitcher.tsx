/// <reference types="vite/client" />
// PROTOTYPE — throwaway. Floating variant bar: ← / → (buttons or arrow
// keys) cycle the ?variant= layouts; the URL is shareable and reload-stable.
// Never rendered in production builds or under vitest.

import { useEffect } from "react";
import "./switcher.css";

export const VARIANT_KEYS = ["current", "A", "B", "C"] as const;
export type VariantKey = (typeof VARIANT_KEYS)[number];

const VARIANT_NAMES: Record<VariantKey, string> = {
  current: "today's layout",
  A: "flow — one scrolling page",
  B: "console — chat drawer under the graph",
  C: "tabs — session tabs under the graph",
};

export const prototypeEnabled =
  !import.meta.env.PROD && import.meta.env.MODE !== "test";

export function variantFromLocation(): VariantKey {
  const raw = new URLSearchParams(window.location.search).get("variant") ?? "";
  return (VARIANT_KEYS as readonly string[]).includes(raw) ? (raw as VariantKey) : "current";
}

export function writeVariant(variant: VariantKey): void {
  const url = new URL(window.location.href);
  if (variant === "current") url.searchParams.delete("variant");
  else url.searchParams.set("variant", variant);
  window.history.replaceState(null, "", url);
}

const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement &&
  (target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.isContentEditable);

export function PrototypeSwitcher({
  current,
  onChange,
}: {
  current: VariantKey;
  onChange: (next: VariantKey) => void;
}) {
  const step = (direction: number) => {
    const n = VARIANT_KEYS.length;
    const i = VARIANT_KEYS.indexOf(current);
    onChange(VARIANT_KEYS[(i + direction + n) % n]);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target)) return;
      if (event.key === "ArrowLeft") step(-1);
      if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="proto-bar">
      <button onClick={() => step(-1)} aria-label="previous variant">←</button>
      <span className="proto-bar__label">
        {current}
        <em> · {VARIANT_NAMES[current]}</em>
      </span>
      <button onClick={() => step(1)} aria-label="next variant">→</button>
    </div>
  );
}
