// The address bar as state: `#/run/<id>`, `#/run/<id>/node/<graph>/<node>`,
// `#/run/<id>/session/<session>`. Hash routing on purpose — the packaged
// server serves one static page, and a hash deep-link needs nothing from it.
// A node key contains a slash, so the node route greedily takes the rest.

import { ROOT_KEY } from "./derive";

export interface Route {
  runId: string | null;
  /** A run-wide node key — ROOT_KEY, or `<graph_id>/<node_id>`. */
  node: string;
  /** Set only by the session route; resolved to its node once the run loads. */
  session: string | null;
}

export const HOME: Route = { runId: null, node: ROOT_KEY, session: null };

export function parseHash(hash: string): Route {
  const parts = hash
    .replace(/^#\/?/, "")
    .split("/")
    .filter((part) => part !== "")
    .map(decodeURIComponent);
  if (parts[0] !== "run" || parts.length < 2) return { ...HOME };
  const runId = parts[1];
  if (parts[2] === "node" && parts.length > 3) {
    return { runId, node: parts.slice(3).join("/"), session: null };
  }
  if (parts[2] === "session" && parts.length === 4) {
    return { runId, node: ROOT_KEY, session: parts[3] };
  }
  return { runId, node: ROOT_KEY, session: null };
}

export function formatHash(route: Route): string {
  if (route.runId == null) return "#/";
  const run = `#/run/${encodeURIComponent(route.runId)}`;
  if (route.session != null) return `${run}/session/${encodeURIComponent(route.session)}`;
  if (route.node === ROOT_KEY) return run;
  return `${run}/node/${route.node.split("/").map(encodeURIComponent).join("/")}`;
}
