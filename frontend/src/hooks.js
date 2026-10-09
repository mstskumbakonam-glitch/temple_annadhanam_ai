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
