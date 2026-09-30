"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

interface PollingState<T> {
  data: T | undefined;
  error: ApiError | Error | undefined;
  loading: boolean;
  refreshing: boolean;
  refresh: () => Promise<void>;
}

/**
 * Fetch now and then every `intervalMs` while the tab is visible. On refetch the previous
 * data stays on screen (no skeleton flash); `refreshing` lets views dim it slightly.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number | null,
  deps: unknown[] = [],
): PollingState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | Error>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setRefreshing(true);
    try {
      const result = await fetcherRef.current(controller.signal);
      if (!controller.signal.aborted) {
        setData(result);
        setError(undefined);
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError") && !controller.signal.aborted) {
        setError(err as Error);
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, deps);

  useEffect(() => {
    setLoading(true);
    void run();
    if (intervalMs === null) return () => controllerRef.current?.abort();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void run();
    }, intervalMs);
    return () => {
      window.clearInterval(timer);
      controllerRef.current?.abort();
    };
  }, [run, intervalMs]);

  return { data, error, loading, refreshing, refresh: run };
}

/** Re-render periodically so relative timestamps ("2 min ago") stay current. */
export function useNow(intervalMs = 15000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
