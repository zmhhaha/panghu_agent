import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { Project } from './domain';
import { hasPendingParses } from './parse';

export const PARSE_POLL_INTERVAL = 5000;

/** Poll only a visible material panel with actual nonterminal parse states. */
export function useParsePolling(project: Project | null, enabled: boolean, onProject: (project: Project) => void) {
  const [error, setError] = useState<unknown>(null);
  const receiver = useRef(onProject);
  receiver.current = onProject;
  const projectId = project?.id;
  const pending = hasPendingParses(project?.assets ?? []);
  useEffect(() => {
    setError(null);
    if (!projectId || !enabled || !pending) return;
    let disposed = false;
    let terminal = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    const clear = () => { if (timer) clearTimeout(timer); timer = undefined; };
    const schedule = () => { clear(); if (!disposed && !terminal && document.visibilityState !== 'hidden') timer = setTimeout(tick, PARSE_POLL_INTERVAL); };
    const tick = async () => {
      clear();
      if (disposed || terminal || request || document.visibilityState === 'hidden') return;
      const controller = new AbortController();
      request = controller;
      try {
        const next = await api.project(projectId, controller.signal);
        if (disposed || controller.signal.aborted) return;
        terminal = !hasPendingParses(next.assets);
        setError(null);
        receiver.current(next);
      } catch (nextError) {
        if (disposed || controller.signal.aborted) return;
        setError(nextError);
        if (nextError instanceof ApiError && [401, 403, 404].includes(nextError.status)) terminal = true;
      } finally {
        if (request === controller) request = undefined;
        schedule();
      }
    };
    const visibility = () => {
      if (document.visibilityState === 'hidden') { clear(); request?.abort(); request = undefined; }
      else void tick();
    };
    document.addEventListener('visibilitychange', visibility);
    schedule();
    return () => { disposed = true; clear(); request?.abort(); document.removeEventListener('visibilitychange', visibility); };
  }, [projectId, enabled, pending]);
  return error;
}
