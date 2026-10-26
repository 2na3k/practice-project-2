import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearAllSessions,
  createSession,
  deleteSession,
  getSession,
  publicConnectionStatus,
  SESSION_TTL_MS,
} from "./session-store";

describe("saved connection sessions", () => {
  let storePath: string;

  beforeEach(() => {
    storePath = join(mkdtempSync(join(tmpdir(), "remymy-sessions-")), "sessions.json");
    vi.stubEnv("SESSION_STORE_PATH", storePath);
    vi.stubEnv("SESSION_SECRET", "test-session-secret");
  });

  afterEach(() => {
    clearAllSessions();
    vi.unstubAllEnvs();
  });

  it("stores the key privately while exposing only connection metadata", () => {
    const created = createSession("anthropic", "claude-fable-5", "test-key", 1000);
    const session = getSession(created.token, 1000);

    expect(session).toMatchObject({
      provider: "anthropic",
      model: "claude-fable-5",
      apiKey: "test-key",
      expiresAt: 1000 + SESSION_TTL_MS,
    });
    expect(publicConnectionStatus(session, 1000)).toEqual({
      connected: true,
      provider: "anthropic",
      model: "claude-fable-5",
      expiresAt: 1000 + SESSION_TTL_MS,
    });
    expect(JSON.stringify(publicConnectionStatus(session, 1000))).not.toMatch(
      /test-key|apiKey/i,
    );
  });

  it("expires sessions at the eight-hour boundary", () => {
    const created = createSession("openai", "gpt-4", "test-key", 10_000);

    expect(getSession(created.token, 10_000 + SESSION_TTL_MS - 1)).toBeDefined();
    expect(getSession(created.token, 10_000 + SESSION_TTL_MS)).toBeUndefined();
    expect(publicConnectionStatus(undefined, 10_000)).toEqual({ connected: false });
  });

  it("disconnects a session and makes the token unusable", () => {
    const created = createSession("anthropic", "claude-fable-5", "test-key", 0);

    deleteSession(created.token);

    expect(getSession(created.token, 0)).toBeUndefined();
    expect(publicConnectionStatus(undefined, 0)).toEqual({ connected: false });
  });

  it("keeps the session across a restart without writing the key in plain text", async () => {
    const created = createSession("anthropic", "claude-fable-5", "test-key", 0);

    expect(readFileSync(storePath, "utf8")).not.toContain("test-key");

    vi.resetModules();
    const restarted = await import("./session-store");

    expect(restarted.getSession(created.token, 0)).toMatchObject({ provider: "anthropic", apiKey: "test-key" });
  });

  it("forgets the key when the session secret changes", () => {
    const created = createSession("openai", "gpt-4", "test-key", 0);

    vi.stubEnv("SESSION_SECRET", "rotated-secret");

    expect(getSession(created.token, 0)).toBeUndefined();
  });
});
