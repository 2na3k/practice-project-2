import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { getPiModels } from "./model-registry";
import {
  clearAllSessions,
  CONNECTION_COOKIE,
  createSession,
} from "./session-store";

const routeMocks = vi.hoisted(() => ({
  runChat: vi.fn(),
}));

// Route handlers use Next path aliases, while this small app keeps Vitest
// config intentionally minimal. Virtual forwarding keeps these tests focused
// on route behavior without changing production resolution.
// @ts-expect-error Vitest supports the virtual-module option at runtime.
vi.mock("@/lib/server/request-guards", async () => import("./request-guards"), {
  virtual: true,
});
// @ts-expect-error Vitest supports the virtual-module option at runtime.
vi.mock("@/lib/server/session-store", async () => import("./session-store"), {
  virtual: true,
});
// @ts-expect-error Vitest supports the virtual-module option at runtime.
vi.mock("@/lib/server/model-registry", async () => import("./model-registry"), {
  virtual: true,
});
vi.mock("@/lib/server/chat-service", () => ({
  ChatServiceError: class ChatServiceError extends Error {
    constructor(public readonly status: number, message: string) {
      super(message);
    }
  },
  runChat: routeMocks.runChat,
  validateChatRequest: vi.fn(() => ({
    messages: [{ role: "user", text: "hello" }],
    preferences: {
      diet: "any",
      maxMinutes: 30,
      exclude: [],
      goal: "balanced",
      cuisine: "any",
    },
  })),
// @ts-expect-error Vitest supports the virtual-module option at runtime.
}), { virtual: true });
// @ts-expect-error Vitest supports the virtual-module option at runtime.
vi.mock("@/lib/server/langfuse", async () => import("./langfuse"), {
  virtual: true,
});

import {
  DELETE as deleteConnection,
  GET as getConnection,
  PATCH as patchConnection,
  POST as postConnection,
} from "../../app/api/connection/route";
import { POST as postChat } from "../../app/api/chat/route";

const requestWithCookie = (
  path: string,
  init: RequestInit = {},
  cookie?: string,
): Request => {
  const request = new Request(`http://remymy.test${path}`, {
    ...init,
    headers: {
      origin: "http://remymy.test",
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...init.headers,
    },
  });
  Object.defineProperty(request, "cookies", {
    value: { get: (name: string) => (name === CONNECTION_COOKIE && cookie ? { value: cookie } : undefined) },
  });
  return request;
};

const cookieValue = (response: Response): string => {
  const header = response.headers.get("set-cookie") ?? "";
  return decodeURIComponent(header.match(new RegExp(`${CONNECTION_COOKIE}=([^;]+)`))?.[1] ?? "");
};

describe("connection and chat routes", () => {
  const models = getPiModels();
  const completeSimple = vi.spyOn(models, "completeSimple");

  beforeEach(() => {
    clearAllSessions();
    completeSimple.mockResolvedValue({ stopReason: "stop" } as never);
    routeMocks.runChat.mockReset();
    routeMocks.runChat.mockResolvedValue({
      text: "Mocked response",
      preferences: {
        diet: "any",
        maxMinutes: 30,
        exclude: [],
        goal: "balanced",
        cuisine: "any",
      },
      recommendations: [],
    });
  });

  afterEach(() => {
    clearAllSessions();
    completeSimple.mockReset();
  });

  it("connects, switches models with the server-held key, reads status, and disconnects", async () => {
    const first = await postConnection(
      requestWithCookie("/api/connection", {
        method: "POST",
        body: JSON.stringify({
          provider: "anthropic",
          model: "claude-sonnet-4-6",
          apiKey: "mock-secret-key",
        }),
      }) as never,
    );
    expect(first.status).toBe(200);
    const firstBody = first.clone();
    expect(await first.json()).toMatchObject({
      connected: true,
      provider: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(await firstBody.text()).not.toContain("mock-secret-key");
    const firstToken = cookieValue(first);
    expect(firstToken).toBeTruthy();

    const status = await getConnection(requestWithCookie("/api/connection", {}, firstToken) as never);
    expect(await status.json()).toMatchObject({ connected: true, provider: "anthropic" });

    const switched = await patchConnection(
      requestWithCookie("/api/connection", {
        method: "PATCH",
        body: JSON.stringify({ model: "claude-haiku-4-5" }),
      }, firstToken) as never,
    );
    expect(switched.status).toBe(200);
    expect(await switched.json()).toMatchObject({ connected: true, model: "claude-haiku-4-5" });
    expect(completeSimple).toHaveBeenCalledTimes(2);
    expect(completeSimple.mock.calls[1]?.[2]).toMatchObject({ apiKey: "mock-secret-key" });
    const switchedToken = cookieValue(switched);

    const disconnected = await deleteConnection(requestWithCookie("/api/connection", { method: "DELETE" }, switchedToken) as never);
    expect(await disconnected.json()).toEqual({ connected: false });
    const finalStatus = await getConnection(requestWithCookie("/api/connection", {}, switchedToken) as never);
    expect(await finalStatus.json()).toEqual({ connected: false });
  });

  it("normalizes a pasted Bearer prefix and gives Groq auth failures a useful message", async () => {
    completeSimple.mockResolvedValueOnce({
      stopReason: "error",
      errorMessage: '401: {"message":"Invalid API Key","code":"invalid_api_key"}',
    } as never);

    const failed = await postConnection(
      requestWithCookie("/api/connection", {
        method: "POST",
        body: JSON.stringify({
          provider: "groq",
          model: "llama-3.3-70b-versatile",
          apiKey: "Bearer gsk_test_invalid",
        }),
      }) as never,
    );

    expect(failed.status).toBe(502);
    expect(await failed.json()).toEqual({
      error: "Groq rejected this API key. Paste an active Groq key, usually beginning with gsk_.",
    });
    expect(completeSimple).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "groq", id: "llama-3.3-70b-versatile" }),
      expect.anything(),
      expect.objectContaining({ apiKey: "gsk_test_invalid" }),
    );
  });

  it("rejects unauthenticated chat and redacts provider errors", async () => {
    const unauthenticated = await postChat(
      requestWithCookie("/api/chat", { method: "POST", body: "{}" }) as never,
    );
    expect(unauthenticated.status).toBe(401);

    const session = createSession("anthropic", "claude-sonnet-4-6", "mock-secret-key");
    routeMocks.runChat.mockRejectedValueOnce(new Error("provider key mock-secret-key failed"));
    const failed = await postChat(
      requestWithCookie("/api/chat", { method: "POST", body: "{}" }, session.token) as never,
    );
    expect(failed.status).toBe(502);
    const payload = await failed.json();
    expect(payload).toEqual({ error: "The request could not be completed." });
    expect(JSON.stringify(payload)).not.toContain("mock-secret-key");
  });

  it("streams agent stages before the final chat result when requested", async () => {
    const session = createSession("anthropic", "claude-sonnet-4-6", "mock-secret-key");
    routeMocks.runChat.mockImplementationOnce(async (...args: unknown[]) => {
      const onStage = args[5] as ((stage: unknown) => void) | undefined;
      onStage?.({
        id: "understand",
        label: "Understanding your request",
        detail: "Preferences and constraints are ready",
        status: "complete",
      });
      return {
        text: "Mocked streamed response",
        preferences: {
          diet: "any",
          maxMinutes: 30,
          exclude: [],
          goal: "balanced",
          cuisine: "any",
        },
        recommendations: [],
      };
    });

    const response = await postChat(
      requestWithCookie("/api/chat", {
        method: "POST",
        headers: { accept: "application/x-ndjson" },
        body: "{}",
      }, session.token) as never,
    );

    expect(response.headers.get("content-type")).toContain("application/x-ndjson");
    const events = (await response.text()).trim().split("\n").map((line) => JSON.parse(line));
    expect(events[0]).toMatchObject({ type: "stage", stage: { id: "understand", status: "complete" } });
    expect(events[1]).toMatchObject({ type: "result", result: { text: "Mocked streamed response" } });
  });
});
