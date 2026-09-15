import { vi, describe, expect, it, beforeEach } from "vitest";
import { findRegistryModel } from "./model-registry";
import {
  ChatServiceError,
  runChat,
  validateChatRequest,
} from "./chat-service";

const agentHarness = vi.hoisted(() => ({
  mode: "success" as "success" | "error" | "aborted",
}));

const testPreferences = vi.hoisted(() => ({
  diet: "any" as const,
  maxMinutes: 30,
  exclude: [],
  goal: "balanced" as const,
  cuisine: "any",
}));

// Vitest supports the virtual-module option at runtime; its bundled typings lag
// this API, so keep the test explicit about the intentional type mismatch.
// @ts-expect-error virtual module mock
vi.mock("@/lib/meals", () => ({ DEFAULT_PREFERENCES: testPreferences }), {
  virtual: true,
});
vi.mock("@/lib/recommender", () => ({
  interpretRequest: (_text: string, preferences: typeof testPreferences) => preferences,
  recommendMeals: () => [
    {
      meal: { title: "Mock Lentil Bowl", minutes: 12 },
      score: 80,
      reasons: ["Fits the mock constraint."],
    },
  ],
}),
// @ts-expect-error Vitest supports this virtual-module option at runtime.
{ virtual: true });

vi.mock("@earendil-works/pi-agent-core", () => ({
  Agent: class MockAgent {
    state: {
      tools?: Array<{ execute: (toolCallId: string, params: unknown) => Promise<unknown> }>;
      messages: unknown[];
    };

    constructor(options: { initialState: MockAgent["state"] }) {
      this.state = options.initialState;
    }

    async prompt(text: string): Promise<void> {
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
          agentHarness.mode === "success"
            ? [{ type: "text", text: "Mocked catalog answer" }]
            : [],
        stopReason:
          agentHarness.mode === "success"
            ? "stop"
            : agentHarness.mode === "error"
              ? "error"
              : "aborted",
      });
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

  it("returns the mocked agent answer and catalog tool recommendations", async () => {
    expect(model).toBeDefined();
    const result = await runChat(
      validRequest,
      model!,
      "test-provider-key",
      models as never,
    );

    expect(result.text).toBe("Mocked catalog answer");
    expect(result.recommendations.length).toBeGreaterThan(0);
    expect(result.recommendations.every(({ meal }) => meal.minutes <= 30)).toBe(true);
    expect(models.streamSimple).not.toHaveBeenCalled();
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
