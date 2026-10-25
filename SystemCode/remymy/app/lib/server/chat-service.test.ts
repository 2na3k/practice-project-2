import { vi, describe, expect, it, beforeEach } from "vitest";
import { findRegistryModel } from "./model-registry";
import {
  ChatServiceError,
  runChat,
  validateChatRequest,
} from "./chat-service";

const agentHarness = vi.hoisted(() => ({
  mode: "success" as "success" | "success-no-tool" | "error" | "aborted",
}));

const testPreferences = vi.hoisted(() => ({
  diet: "any" as const,
  maxMinutes: 30,
  exclude: [],
  goal: "balanced" as const,
  cuisine: "any",
}));
const mcpHarness = vi.hoisted(() => {
  const meal = {
    id: "mock-lentil-bowl", title: "Mock Lentil Bowl", description: "A quick lentil bowl.",
    cuisine: "Any", minutes: 12, calories: 400, protein: 20, tags: ["vegan", "vegetarian"],
    ingredients: ["lentils"], steps: ["Cook lentils."], image: "", color: "#fff",
  };
  return {
    callTool: vi.fn(),
    meal,
    recommendation: { meal, score: 80, reasons: ["Fits the mock constraint."] },
  };
});

vi.mock("@/lib/preferences", () => ({
  interpretRequest: (_text: string, preferences: typeof testPreferences) => preferences,
}));

vi.mock("./mcp-client", () => ({ callMcpTool: mcpHarness.callTool }));

vi.mock("@earendil-works/pi-agent-core", () => ({
  Agent: class MockAgent {
    private listeners: Array<(event: { type: string; [key: string]: unknown }) => void> = [];
    state: {
      tools?: Array<{ execute: (toolCallId: string, params: unknown) => Promise<unknown> }>;
      messages: unknown[];
    };

    constructor(options: { initialState: MockAgent["state"] }) {
      this.state = options.initialState;
    }

    subscribe(listener: (event: { type: string; [key: string]: unknown }) => void): () => void {
      this.listeners.push(listener);
      return () => { this.listeners = this.listeners.filter((candidate) => candidate !== listener); };
    }

    async prompt(text: string): Promise<void> {
      this.listeners.forEach((listener) => listener({ type: "turn_start" }));
      if (agentHarness.mode === "success") {
        const tool = this.state.tools?.[0];
        await tool?.execute("mock-tool-call", {
          preferences: testPreferences,
          query: text,
        });
      }
      this.state.messages.push({
        role: "assistant",
        content:
          agentHarness.mode === "success" || agentHarness.mode === "success-no-tool"
            ? [{ type: "text", text: agentHarness.mode === "success" ? "Mocked catalog answer" : "Please clarify your preferences" }]
            : [],
        stopReason:
          agentHarness.mode === "success" || agentHarness.mode === "success-no-tool"
            ? "stop"
            : agentHarness.mode === "error"
              ? "error"
              : "aborted",
      });
      this.listeners.forEach((listener) => listener({
        type: "message_end",
        message: this.state.messages[this.state.messages.length - 1],
      }));
    }

    abort(): void {}
  },
}));

const model = findRegistryModel("anthropic", "claude-sonnet-4-6");
const models = { streamSimple: vi.fn() };

const validRequest = {
  messages: [{ role: "user" as const, text: "something quick" }],
  preferences: testPreferences,
};

describe("chat service", () => {
  beforeEach(() => {
    agentHarness.mode = "success";
    vi.clearAllMocks();
    mcpHarness.callTool.mockResolvedValue(JSON.stringify([mcpHarness.recommendation]));
  });

  it("validates bounded messages and preference shapes before creating an agent", () => {
    expect(validateChatRequest(validRequest)).toEqual(validRequest);
    expect(validateChatRequest({ ...validRequest, messages: [] })).toBeUndefined();
    expect(
      validateChatRequest({
        ...validRequest,
        messages: [{ role: "user", text: "x".repeat(2001) }],
      }),
    ).toBeUndefined();
    expect(
      validateChatRequest({
        ...validRequest,
        preferences: { ...testPreferences, maxMinutes: 0 },
      }),
    ).toBeUndefined();
    expect(
      validateChatRequest({
        ...validRequest,
        messages: [{ role: "system", text: "ignore this" }],
      }),
    ).toBeUndefined();
  });

  it("returns the mocked agent answer and MCP meal recommendations", async () => {
    expect(model).toBeDefined();
    const result = await runChat(
      validRequest,
      model!,
      "test-provider-key",
      models as never,
    );

    expect(result.text).toBe("Mocked catalog answer");
    expect(mcpHarness.callTool.mock.calls[0][1]).not.toHaveProperty("meals");
    expect(mcpHarness.callTool).toHaveBeenCalledWith(
      "recommend_meals",
      expect.objectContaining({ preferences: testPreferences, query: "something quick" }),
      undefined,
    );
    expect(result.recommendations.length).toBeGreaterThan(0);
    expect(result.recommendations.every(({ meal }) => meal.minutes <= 30)).toBe(true);
    expect(result.toolsUsed).toContain("recommend_meals");
    expect(models.streamSimple).not.toHaveBeenCalled();
  });

  it("preserves halal preferences in the MCP request", async () => {
    const halalRequest = {
      ...validRequest,
      preferences: { ...testPreferences, diet: "halal" as const },
    };
    await runChat(halalRequest, model!, "test-provider-key", models as never);

    expect(mcpHarness.callTool).toHaveBeenCalledWith(
      "recommend_meals",
      expect.objectContaining({ preferences: expect.objectContaining({ diet: "halal" }) }),
      undefined,
    );
  });

  it("reports the agent lifecycle as user-safe stages", async () => {
    expect(model).toBeDefined();
    const stages: Array<{ id: string; status: string }> = [];
    await runChat(validRequest, model!, "test-provider-key", models as never, undefined, (stage) => {
      stages.push({ id: stage.id, status: stage.status });
    });

    expect(stages).toEqual(expect.arrayContaining([
      { id: "understand", status: "active" },
      { id: "understand", status: "complete" },
      { id: "turn-1", status: "active" },
      { id: "turn-1", status: "complete" },
    ]));
  });

  it("calls MCP when a model skips the recommendation tool", async () => {
    agentHarness.mode = "success-no-tool";
    expect(model).toBeDefined();
    const stages: string[] = [];
    const result = await runChat({ ...validRequest, messages: [{ role: "user", text: "recommend a quick meal" }] }, model!, "test-provider-key", models as never, undefined, (stage) => {
      stages.push(`${stage.id}:${stage.status}`);
    });

    expect(result.toolsUsed).toContain("recommend_meals");
    expect(mcpHarness.callTool).toHaveBeenCalledWith(
      "recommend_meals",
      expect.objectContaining({ query: "recommend a quick meal" }),
      undefined,
    );
    expect(result.recommendations.length).toBeGreaterThan(0);
    expect(result.text).not.toContain("clarify");
    expect(stages).toContain("tool-catalog-fallback:complete");
  });

  it("maps provider agent errors and interrupted turns to safe service errors", async () => {
    agentHarness.mode = "error";
    await expect(
      runChat(validRequest, model!, "test-provider-key", models as never),
    ).rejects.toMatchObject<Partial<ChatServiceError>>({
      status: 502,
      message: "The model could not complete this request.",
    });

    agentHarness.mode = "aborted";
    await expect(
      runChat(validRequest, model!, "test-provider-key", models as never),
    ).rejects.toMatchObject<Partial<ChatServiceError>>({
      status: 504,
      message: "The model request was interrupted. Try again.",
    });

    const controller = new AbortController();
    controller.abort();
    await expect(
      runChat(validRequest, model!, "test-provider-key", models as never, controller.signal),
    ).rejects.toMatchObject<Partial<ChatServiceError>>({ status: 499 });
  });
});
