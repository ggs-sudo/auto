// PROTOTYPE — four radically different takes on the monitoring website, one route,
// switchable via ?variant=. Issue #5. Throwaway: no tests, no error handling, no persistence.
import { useEffect, useState } from "react";
import { PrototypeSwitcher, useVariant, type VariantDef } from "./PrototypeSwitcher";
import { MissionControl } from "./variants/MissionControl";
import { TerminalLog } from "./variants/TerminalLog";
import { Timeline } from "./variants/Timeline";
import { Inbox } from "./variants/Inbox";
import { MissionControlTerm } from "./variants/MissionControlTerm";

const VARIANTS: VariantDef[] = [
  { key: "A", name: "Mission control", Component: MissionControl },
  { key: "B", name: "Terminal log", Component: TerminalLog },
  { key: "C", name: "Timeline", Component: Timeline },
  { key: "D", name: "Gate inbox", Component: Inbox },
  { key: "E", name: "Mission control · terminal skin", Component: MissionControlTerm },
];

export function App() {
  const [, force] = useState(0);
  useEffect(() => {
    const onPop = () => force((n) => n + 1);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const [current, set] = useVariant(VARIANTS);
  const Variant = current.Component;

  return (
    <>
      <Variant key={current.key} />
      <PrototypeSwitcher variants={VARIANTS} current={current} onChange={set} />
    </>
  );
}
