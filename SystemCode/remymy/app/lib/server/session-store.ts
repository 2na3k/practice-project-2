import { randomBytes } from "node:crypto";
import type { ConnectionStatus } from "@/lib/chat-types";

export const CONNECTION_COOKIE = "remymy_connection";
export const SESSION_TTL_MS = 8 * 60 * 60 * 1000;
export const MAX_SESSIONS = 256;

type PrivateSession = {
  provider: string;
  model: string;
  apiKey: string;
  expiresAt: number;
};

type SessionStore = Map<string, PrivateSession>;
const SESSION_STORE_KEY = Symbol.for("remymy.connection.sessions");
const globalState = globalThis as typeof globalThis & { [SESSION_STORE_KEY]?: SessionStore };
const sessions: SessionStore = globalState[SESSION_STORE_KEY] ?? new Map<string, PrivateSession>();
globalState[SESSION_STORE_KEY] = sessions;

function newToken(): string {
  return randomBytes(32).toString("base64url");
}

function removeExpired(now = Date.now()): void {
  for (const [token, session] of sessions) {
    if (session.expiresAt <= now) sessions.delete(token);
  }
}

export function createSession(provider: string, model: string, apiKey: string, now = Date.now()): { token: string; expiresAt: number } {
  removeExpired(now);
  if (sessions.size >= MAX_SESSIONS) {
    const oldest = [...sessions.entries()].sort(([, a], [, b]) => a.expiresAt - b.expiresAt)[0];
    if (oldest) sessions.delete(oldest[0]);
  }
  const token = newToken();
  const expiresAt = now + SESSION_TTL_MS;
  sessions.set(token, { provider, model, apiKey, expiresAt });
  return { token, expiresAt };
}

export function getSession(token: string | undefined, now = Date.now()): (PrivateSession & { token: string }) | undefined {
  if (!token) return undefined;
  const session = sessions.get(token);
  if (!session) return undefined;
  if (session.expiresAt <= now) {
    sessions.delete(token);
    return undefined;
  }
  return { token, ...session };
}

export function deleteSession(token: string | undefined): void {
  if (token) sessions.delete(token);
}

export function publicConnectionStatus(session: PrivateSession | undefined, now = Date.now()): ConnectionStatus {
  if (!session || session.expiresAt <= now) return { connected: false };
  return { connected: true, provider: session.provider, model: session.model, expiresAt: session.expiresAt };
}

/** Test seam: confirms memory-only state and permits deterministic expiry tests without exposing keys. */
export function clearAllSessions(): void {
  sessions.clear();
}
