import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Poll an async loader every `intervalMs`. Keeps the last good value when a
 * request fails, and reports the error separately, so a network blip shows a
 * warning instead of blanking the dashboard. Pauses while the tab is hidden.
 */
export function usePolling(loader, intervalMs, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true, updatedAt: null });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const run = useCallback(async () => {
    try {
      const data = await loaderRef.current();
      setState({ data, error: null, loading: false, updatedAt: new Date() });
    } catch (error) {
      setState((prev) => ({ ...prev, error, loading: false }));
    }
  }, []);

  useEffect(() => {
    let timer = null;
    let cancelled = false;
    const tick = async () => {
      if (!document.hidden) await run();
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, run, ...deps]);

  return { ...state, refresh: run };
}

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Load once (and on demand). Keeps the previous data while reloading. */
export function useApi(loader, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const counter = useRef(0);

  const reload = useCallback(async () => {
    const ticket = ++counter.current;
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await loaderRef.current();
      if (ticket === counter.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (ticket === counter.current) setState((s) => ({ ...s, error, loading: false }));
    }
  }, []);

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { ...state, reload };
}

/** Debounce a fast-changing value (search boxes). */
export function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}
