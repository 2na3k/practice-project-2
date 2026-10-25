import { createCipheriv, createDecipheriv, randomBytes, scryptSync } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
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

// The key is encrypted at rest; the rest of the session is metadata.
type StoredSession = {
  provider: string;
  model: string;
  expiresAt: number;
  iv: string;
  tag: string;
  apiKey: string;
};

type StoredFile = { version: 1; sessions: Record<string, StoredSession> };

function storePath(): string {
  return process.env.SESSION_STORE_PATH ?? join(process.cwd(), ".remymy", "sessions.json");
}

// Keeps provider keys across restarts of the app. Only the cookie token and this encrypted file are needed to reconnect.
function cipherKey(): Buffer {
  const secret = process.env.SESSION_SECRET;
  if (!secret) throw new Error("SESSION_SECRET is not set, so provider keys cannot be saved.");
  return scryptSync(secret, "remymy-session-keys", 32);
}

function encryptKey(apiKey: string): Pick<StoredSession, "iv" | "tag" | "apiKey"> {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", cipherKey(), iv);
  const encrypted = Buffer.concat([cipher.update(apiKey, "utf8"), cipher.final()]);
  return { iv: iv.toString("base64"), tag: cipher.getAuthTag().toString("base64"), apiKey: encrypted.toString("base64") };
}

function decryptKey(session: StoredSession): string | undefined {
  try {
    const decipher = createDecipheriv("aes-256-gcm", cipherKey(), Buffer.from(session.iv, "base64"));
    decipher.setAuthTag(Buffer.from(session.tag, "base64"));
    return Buffer.concat([decipher.update(Buffer.from(session.apiKey, "base64")), decipher.final()]).toString("utf8");
  } catch {
    // a wrong or rotated SESSION_SECRET makes the saved key unreadable; the person connects again
    return undefined;
  }
}

function newToken(): string {
  return randomBytes(32).toString("base64url");
}

function readFile(): StoredFile {
  const path = storePath();
  if (!existsSync(path)) return { version: 1, sessions: {} };
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as Partial<StoredFile>;
    if (parsed.version === 1 && parsed.sessions && typeof parsed.sessions === "object") return parsed as StoredFile;
  } catch {
    // an unreadable file starts again empty rather than failing every request
  }
  return { version: 1, sessions: {} };
}

function writeFile(file: StoredFile): void {
  const path = storePath();
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  // write beside the file and rename, so a crash never leaves half a file
  const temporary = `${path}.tmp`;
  writeFileSync(temporary, JSON.stringify(file), { mode: 0o600 });
  renameSync(temporary, path);
}

function removeExpired(file: StoredFile, now: number): void {
  for (const [token, session] of Object.entries(file.sessions)) {
    if (session.expiresAt <= now) delete file.sessions[token];
  }
}

export function createSession(provider: string, model: string, apiKey: string, now = Date.now()): { token: string; expiresAt: number } {
  const file = readFile();
  removeExpired(file, now);
  const stored = Object.entries(file.sessions);
  if (stored.length >= MAX_SESSIONS) {
    const oldest = stored.sort(([, a], [, b]) => a.expiresAt - b.expiresAt)[0];
    if (oldest) delete file.sessions[oldest[0]];
  }
  const token = newToken();
  const expiresAt = now + SESSION_TTL_MS;
  file.sessions[token] = { provider, model, expiresAt, ...encryptKey(apiKey) };
  writeFile(file);
  return { token, expiresAt };
}

export function getSession(token: string | undefined, now = Date.now()): (PrivateSession & { token: string }) | undefined {
  if (!token) return undefined;
  const file = readFile();
  const session = file.sessions[token];
  if (!session) return undefined;
  const apiKey = session.expiresAt > now ? decryptKey(session) : undefined;
  if (!apiKey) {
    delete file.sessions[token];
    writeFile(file);
    return undefined;
  }
  return { token, provider: session.provider, model: session.model, expiresAt: session.expiresAt, apiKey };
}

export function deleteSession(token: string | undefined): void {
  if (!token) return;
  const file = readFile();
  if (!(token in file.sessions)) return;
  delete file.sessions[token];
  writeFile(file);
}

export function publicConnectionStatus(session: PrivateSession | undefined, now = Date.now()): ConnectionStatus {
  if (!session || session.expiresAt <= now) return { connected: false };
  return { connected: true, provider: session.provider, model: session.model, expiresAt: session.expiresAt };
}

/** Test seam: removes every saved session so each test starts from an empty store. */
export function clearAllSessions(): void {
  rmSync(storePath(), { force: true });
}
