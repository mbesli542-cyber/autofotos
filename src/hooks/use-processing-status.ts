"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchProcessingStatus } from "@/lib/processing/processing-client";
import type { ProcessingStatus } from "@/lib/processing/types";

export type ProcessingStatusState =
  | { phase: "loading"; status: null }
  | { phase: "ready"; status: ProcessingStatus }
  | { phase: "error"; status: null };

/**
 * Live processor status (GET /api/processing-status). Re-checked whenever the
 * page becomes visible again (routes stay mounted, effects re-run), when the
 * connection comes back or is lost, and on refresh().
 */
export function useProcessingStatus() {
  const [state, setState] = useState<ProcessingStatusState>({ phase: "loading", status: null });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetchProcessingStatus(controller.signal).then(
      (status) => setState({ phase: "ready", status }),
      () => {
        if (!controller.signal.aborted) setState({ phase: "error", status: null });
      },
    );
    return () => controller.abort();
  }, [version]);

  // Connection back (or lost): ask again, so the page never keeps an outdated state.
  useEffect(() => {
    const onChange = () => setVersion((v) => v + 1);
    window.addEventListener("online", onChange);
    window.addEventListener("offline", onChange);
    return () => {
      window.removeEventListener("online", onChange);
      window.removeEventListener("offline", onChange);
    };
  }, []);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  return { state, refresh };
}
