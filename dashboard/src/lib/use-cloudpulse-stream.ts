"use client";

import * as React from "react";
import type {
  Decision,
  ForecastFrame,
  RunMeta,
  TelemetryFrame,
} from "./telemetry";

export type StreamStatus = "off" | "connecting" | "connected" | "error";

interface WsSnapshotMessage {
  type: "snapshot";
  payload: {
    meta: RunMeta;
    telemetry: TelemetryFrame[];
    forecasts: ForecastFrame[];
    decisions: Decision[];
  };
}

interface WsTelemetryMessage {
  type: "telemetry";
  payload: TelemetryFrame;
}

interface WsForecastMessage {
  type: "forecast";
  payload: ForecastFrame;
}

interface WsDecisionMessage {
  type: "decision";
  payload: Decision;
}

type WsMessage =
  | WsSnapshotMessage
  | WsTelemetryMessage
  | WsForecastMessage
  | WsDecisionMessage;

export interface CloudPulseStreamState {
  status: StreamStatus;
  meta: RunMeta | null;
  telemetry: TelemetryFrame[];
  forecasts: ForecastFrame[];
  decisions: Decision[];
  errorMessage: string | null;
  wsUrl: string;
  connect: (url?: string) => void;
  disconnect: () => void;
}

export const DEFAULT_WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://127.0.0.1:8002/ws/stream";

export function useCloudPulseStream(
  initialAutoUrl?: string,
): CloudPulseStreamState {
  const [status, setStatus] = React.useState<StreamStatus>("off");
  const [wsUrl, setWsUrl] = React.useState<string>(
    initialAutoUrl || DEFAULT_WS_URL,
  );
  const [meta, setMeta] = React.useState<RunMeta | null>(null);
  const [telemetry, setTelemetry] = React.useState<TelemetryFrame[]>([]);
  const [forecasts, setForecasts] = React.useState<ForecastFrame[]>([]);
  const [decisions, setDecisions] = React.useState<Decision[]>([]);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  const socketRef = React.useRef<WebSocket | null>(null);

  const closeSocket = React.useCallback(() => {
    if (socketRef.current) {
      socketRef.current.onopen = null;
      socketRef.current.onmessage = null;
      socketRef.current.onerror = null;
      socketRef.current.onclose = null;
      socketRef.current.close();
      socketRef.current = null;
    }
  }, []);

  const openSocket = React.useCallback(
    (targetUrl: string) => {
      closeSocket();
      setWsUrl(targetUrl);
      setStatus("connecting");
      setErrorMessage(null);

      const ws = new WebSocket(targetUrl);
      socketRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        setErrorMessage(null);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(String(event.data)) as WsMessage;
          if (msg.type === "snapshot") {
            setMeta(msg.payload.meta);
            setTelemetry(msg.payload.telemetry.slice(-720));
            setForecasts(msg.payload.forecasts.slice(-720));
            setDecisions(msg.payload.decisions.slice(-720));
          } else if (msg.type === "telemetry") {
            setTelemetry((prev) => [...prev.slice(-719), msg.payload]);
          } else if (msg.type === "forecast") {
            setForecasts((prev) => [...prev.slice(-719), msg.payload]);
          } else if (msg.type === "decision") {
            setDecisions((prev) => [...prev.slice(-719), msg.payload]);
          }
        } catch {
          setErrorMessage("Malformed telemetry frame received");
        }
      };

      ws.onerror = () => {
        setStatus("error");
        setErrorMessage(`Could not connect to ${targetUrl}`);
      };

      ws.onclose = () => {
        socketRef.current = null;
        setStatus((curr) => (curr === "error" ? "error" : "off"));
      };
    },
    [closeSocket],
  );

  const disconnect = React.useCallback(() => {
    closeSocket();
    setStatus("off");
    setMeta(null);
    setTelemetry([]);
    setForecasts([]);
    setDecisions([]);
    setErrorMessage(null);
  }, [closeSocket]);

  React.useEffect(() => {
    if (initialAutoUrl) {
      const ws = new WebSocket(initialAutoUrl);
      socketRef.current = ws;
      ws.onopen = () => {
        setStatus("connected");
        setErrorMessage(null);
      };
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(String(event.data)) as WsMessage;
          if (msg.type === "snapshot") {
            setMeta(msg.payload.meta);
            setTelemetry(msg.payload.telemetry.slice(-720));
            setForecasts(msg.payload.forecasts.slice(-720));
            setDecisions(msg.payload.decisions.slice(-720));
          } else if (msg.type === "telemetry") {
            setTelemetry((prev) => [...prev.slice(-719), msg.payload]);
          } else if (msg.type === "forecast") {
            setForecasts((prev) => [...prev.slice(-719), msg.payload]);
          } else if (msg.type === "decision") {
            setDecisions((prev) => [...prev.slice(-719), msg.payload]);
          }
        } catch {
          setErrorMessage("Malformed telemetry frame received");
        }
      };
      ws.onerror = () => {
        setStatus("error");
        setErrorMessage(`Could not connect to ${initialAutoUrl}`);
      };
      ws.onclose = () => {
        socketRef.current = null;
      };
    }
    return () => {
      closeSocket();
    };
  }, [initialAutoUrl, closeSocket]);

  return {
    status,
    meta,
    telemetry,
    forecasts,
    decisions,
    errorMessage,
    wsUrl,
    connect: (url?: string) => openSocket(url || wsUrl),
    disconnect,
  };
}
