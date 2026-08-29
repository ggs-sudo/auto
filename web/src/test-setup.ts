import "@testing-library/jest-dom/vitest";

// jsdom gaps the components lean on: layout observation and scrolling are
// no-ops under test, present so mounting doesn't throw.
class QuietResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= QuietResizeObserver as unknown as typeof ResizeObserver;
Element.prototype.scrollTo ??= () => {};

// No vitest globals in this setup, so register RTL's cleanup by hand.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
afterEach(cleanup);
