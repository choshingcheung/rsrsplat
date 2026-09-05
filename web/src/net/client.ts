/**
 * Talking to the physics service — or standing in for it.
 *
 * `MockService` and `SocketService` present the same interface, so the rest of the app never
 * learns which one it has. `connect()` tries the real socket and falls back to the mock if
 * it cannot reach it, which means the app works on a machine with no Python running, on a
 * venue network that has fallen over, and in the thirty seconds after someone closes the
 * wrong terminal window.
 *
 * The fallback is automatic rather than a flag, because a flag has to be remembered under
 * pressure. The readout says which one is live, so it is never a silent substitution.
 */

import type { ClientMessage, ServerMessage } from "../types/protocol";
import { MockService } from "./mock";

export interface Service {
  send(message: ClientMessage): void;
  stop(): void;
}

export type Transport = "service" | "local";

export interface ConnectOptions {
  url?: string;
  onMessage: (message: ServerMessage) => void;
  /** Called once the transport is decided, and again if it changes. */
  onTransport?: (transport: Transport) => void;
  /** How long to wait for the service before giving up on it, ms. */
  timeout?: number;
}

const DEFAULT_URL = `ws://${location.hostname}:8000/ws`;
const DEFAULT_TIMEOUT = 1200;

/** The real thing: a WebSocket carrying the same JSON the fixtures describe. */
export class SocketService implements Service {
  private queue: ClientMessage[] = [];

  constructor(
    private socket: WebSocket,
    private onMessage: (message: ServerMessage) => void,
  ) {
    socket.addEventListener("message", (event) => {
      try {
        this.onMessage(JSON.parse(event.data as string) as ServerMessage);
      } catch {
        // A frame the other side of a hand-mirrored contract sent that we cannot parse is a
        // bug worth seeing, but not worth dropping a live scene over.
        console.warn("rsrsplat: unparseable server message", event.data);
      }
    });
  }

  send(message: ClientMessage): void {
    if (this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
      return;
    }
    // Sent before the socket finished opening: hold it rather than lose it. A scene.load
    // dropped here leaves a session that never starts and no error to explain why.
    this.queue.push(message);
  }

  flush(): void {
    for (const message of this.queue.splice(0)) this.send(message);
  }

  stop(): void {
    this.socket.close();
  }
}

/**
 * Reach the service if it is there, and stand in for it if it is not.
 *
 * Resolves as soon as either is ready, so nothing upstream has to care.
 */
export async function connect(options: ConnectOptions): Promise<Service> {
  const url = options.url ?? DEFAULT_URL;
  const timeout = options.timeout ?? DEFAULT_TIMEOUT;

  const useMock = (why: string): Service => {
    console.info(`rsrsplat: using the in-browser service (${why})`);
    options.onTransport?.("local");
    return new MockService({ onMessage: options.onMessage });
  };

  let socket: WebSocket;
  try {
    socket = new WebSocket(url);
  } catch (error) {
    return useMock(error instanceof Error ? error.message : "could not open a socket");
  }

  return new Promise<Service>((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      socket.close();
      resolve(useMock(`no response from ${url}`));
    }, timeout);

    socket.addEventListener("open", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const service = new SocketService(socket, options.onMessage);
      service.flush();
      options.onTransport?.("service");
      resolve(service);
    });

    socket.addEventListener("error", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(useMock(`could not reach ${url}`));
    });

    // A socket that drops mid-session is not recovered into the mock: the scene it was
    // simulating is gone, and quietly swapping in a different physics engine mid-demo would
    // be worse than saying so.
    socket.addEventListener("close", () => {
      if (settled) options.onTransport?.("local");
    });
  });
}
