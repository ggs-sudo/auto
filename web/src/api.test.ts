// The change stream must outlive the server: EventSource handles transient
// drops itself, but when it gives up (readyState CLOSED — the server is
// down), the hook reopens it with backoff so reconnection needs no reload.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChangeStream } from "./api";
import { FakeEventSource } from "./test-doubles";

describe("useChangeStream", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("goes live on the versions baseline and resyncs", () => {
    const onResync = vi.fn();
    const { result } = renderHook(() => useChangeStream(vi.fn(), onResync));
    expect(result.current).toBe(false);
    act(() => FakeEventSource.instances[0].emit("versions", {}));
    expect(result.current).toBe(true);
    expect(onResync).toHaveBeenCalledTimes(1);
  });

  it("relays change notifications by run id", () => {
    const onChange = vi.fn();
    renderHook(() => useChangeStream(onChange, vi.fn()));
    act(() => FakeEventSource.instances[0].emit("change", { run_id: "r-1" }));
    expect(onChange).toHaveBeenCalledWith("r-1");
  });

  it("reopens the stream after the browser gives up, and resyncs on return", () => {
    const onResync = vi.fn();
    const { result } = renderHook(() => useChangeStream(vi.fn(), onResync));
    act(() => FakeEventSource.instances[0].emit("versions", {}));

    act(() => FakeEventSource.instances[0].die());
    expect(result.current).toBe(false);

    act(() => vi.advanceTimersByTime(20_000));
    expect(FakeEventSource.instances.length).toBe(2);

    act(() => FakeEventSource.instances[1].emit("versions", {}));
    expect(result.current).toBe(true);
    expect(onResync).toHaveBeenCalledTimes(2);
  });

  it("keeps retrying while the server stays down", () => {
    renderHook(() => useChangeStream(vi.fn(), vi.fn()));
    for (let round = 0; round < 4; round++) {
      act(() => FakeEventSource.instances.at(-1)!.die());
      act(() => vi.advanceTimersByTime(30_000));
    }
    expect(FakeEventSource.instances.length).toBe(5);
  });

  it("stops retrying once unmounted", () => {
    const { unmount } = renderHook(() => useChangeStream(vi.fn(), vi.fn()));
    act(() => FakeEventSource.instances[0].die());
    unmount();
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeEventSource.instances.length).toBe(1);
  });
});
