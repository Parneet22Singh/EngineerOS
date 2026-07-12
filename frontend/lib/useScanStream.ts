"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "./api";

export interface ProgressFrame {
  stage: string;
  progress: number | null;
  detail?: string;
  status?: string;
}

/**
 * Subscribes to a scan's WebSocket progress stream. Returns the latest frame and a
 * `terminal` flag once the scan completes or fails. Reconnection is intentionally
 * simple — the parent refetches the full scan on terminal.
 */
export function useScanStream(scanId: string, enabled: boolean) {
  const [frame, setFrame] = useState<ProgressFrame | null>(null);
  const [terminal, setTerminal] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!enabled || !scanId) return;
    setTerminal(false);
    const ws = new WebSocket(api.streamUrl(scanId));
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as ProgressFrame;
        if (data.stage === "heartbeat") return;
        setFrame(data);
        if (data.stage === "completed" || data.stage === "failed") {
          setTerminal(true);
          ws.close();
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onerror = () => {
      /* surfaced via terminal + parent refetch */
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [scanId, enabled]);

  return { frame, terminal };
}
