// PROTOTYPE — throwaway variant switcher. Hidden in production builds.
/// <reference types="vite/client" />
import { useEffect, type ReactElement } from "react";

export interface VariantDef {
  key: string;
  name: string;
  Component: () => ReactElement;
}

export function useVariant(variants: VariantDef[]): [VariantDef, (key: string) => void] {
  const param = new URLSearchParams(window.location.search).get("variant");
  const current = variants.find((v) => v.key === param) ?? variants[0];

  const set = (key: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("variant", key);
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  return [current, set];
}

export function PrototypeSwitcher({
  variants,
  current,
  onChange,
}: {
  variants: VariantDef[];
  current: VariantDef;
  onChange: (key: string) => void;
}) {
  const i = variants.findIndex((v) => v.key === current.key);
  const step = (d: number) => onChange(variants[(i + d + variants.length) % variants.length].key);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (el instanceof HTMLElement && el.isContentEditable) return;
      if (e.key === "ArrowLeft") step(-1);
      if (e.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (import.meta.env.PROD) return null;

  return (
    <div className="proto-switcher">
      <button onClick={() => step(-1)} aria-label="previous variant">
        ←
      </button>
      <span className="proto-switcher__label">
        <b>{current.key}</b> {current.name}
      </span>
      <button onClick={() => step(1)} aria-label="next variant">
        →
      </button>
    </div>
  );
}
