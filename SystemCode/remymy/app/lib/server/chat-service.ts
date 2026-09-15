import { Agent, type AgentTool } from "@earendil-works/pi-agent-core";
import {
  Type,
  type Api,
  type AssistantMessage,
  type Message,
  type Model,
  type Models,
} from "@earendil-works/pi-ai";
import type { ChatRequest, ChatResult } from "@/lib/chat-types";
import type { Preferences } from "@/lib/meals";
import { interpretRequest, recommendMeals, type MealRecommendation } from "@/lib/recommender";
import { MAX_CHAT_TEXT_CHARS, MAX_CHAT_TURNS } from "./request-guards";
import { callMcpTool } from "./mcp-client";

export const CHAT_TIMEOUT_MS = 60_000;
export const MAX_TOOL_TURNS = 4;

const emptyUsage = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

const recommendationParameters = Type.Object({
  preferences: Type.Object({
    diet: Type.Union([Type.Literal("any"), Type.Literal("vegetarian"), Type.Literal("vegan")]),
    maxMinutes: Type.Number({ minimum: 1, maximum: 90 }),
    exclude: Type.Array(Type.String({ maxLength: 80 }), { maxItems: 24 }),
    goal: Type.Union([Type.Literal("balanced"), Type.Literal("protein"), Type.Literal("light")]),
    cuisine: Type.String({ maxLength: 80 }),
  }),
  query: Type.Optional(Type.String({ maxLength: MAX_CHAT_TEXT_CHARS })),
});

const mcpHelloParameters = Type.Object({
  name: Type.Optional(Type.String({ maxLength: 80 })),
});

const isPreferences = (value: unknown): value is Preferences => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<Preferences>;
  return (
    (candidate.diet === "any" || candidate.diet === "vegetarian" || candidate.diet === "vegan") &&
    typeof candidate.maxMinutes === "number" && Number.isFinite(candidate.maxMinutes) &&
    candidate.maxMinutes >= 1 && candidate.maxMinutes <= 90 &&
    Array.isArray(candidate.exclude) && candidate.exclude.every((item) => typeof item === "string" && item.length <= 80) &&
    (candidate.goal === "balanced" || candidate.goal === "protein" || candidate.goal === "light") &&
    typeof candidate.cuisine === "string" && candidate.cuisine.length <= 80
  );
};

/** Keep restrictions established by the user outside the model's control. */
const preserveHardPreferences = (candidate: Preferences, baseline: Preferences): Preferences => ({
  diet:
    baseline.diet === "vegan" || (baseline.diet === "vegetarian" && candidate.diet === "any")
      ? baseline.diet
      : candidate.diet,
  maxMinutes: Math.min(candidate.maxMinutes, baseline.maxMinutes),
  exclude: Array.from(new Set([...baseline.exclude, ...candidate.exclude])),
  goal: candidate.goal,
  cuisine: candidate.cuisine,
});

const textFromAssistant = (message: AssistantMessage | undefined): string =>
  message?.content
    .filter((block): block is { type: "text"; text: string } => block.type === "text")
    .map((block) => block.text)
    .join("")
    .trim() ?? "";

const toAssistantHistory = (model: Model<Api>, text: string): AssistantMessage => ({
  role: "assistant",
  content: [{ type: "text", text }],
  api: model.api,
  provider: model.provider,
  model: model.id,
  usage: emptyUsage,
  stopReason: "stop",
  timestamp: Date.now(),
});

const toAgentMessages = (model: Model<Api>, messages: ChatRequest["messages"]): Message[] =>
  messages.map((message) =>
    message.role === "user"
      ? { role: "user", content: message.text, timestamp: Date.now() }
      : toAssistantHistory(model, message.text),
  );

const mealTool = (baseline: Preferences, onResult: (preferences: Preferences, recommendations: MealRecommendation[]) => void): AgentTool<typeof recommendationParameters, { preferences: Preferences; recommendations: MealRecommendation[] }> => ({
  name: "recommend_meals",
  label: "Recommend meals",
  description: "Search the curated Remymy MEALS catalog. Use this for every meal recommendation and never invent meals outside its results.",
  parameters: recommendationParameters,
  executionMode: "sequential",
  execute: async (_toolCallId, params) => {
    const requestedPreferences = isPreferences(params.preferences) ? params.preferences : baseline;
    const preferences = preserveHardPreferences(requestedPreferences, baseline);
    const query = typeof params.query === "string" ? params.query.slice(0, MAX_CHAT_TEXT_CHARS) : undefined;
    const interpreted = preserveHardPreferences(query ? interpretRequest(query, preferences) : preferences, baseline);
    const recommendations = recommendMeals(interpreted);
    onResult(interpreted, recommendations);
    return {
      content: [{ type: "text", text: JSON.stringify({ preferences: interpreted, recommendations }) }],
      details: { preferences: interpreted, recommendations },
    };
  },
});

const mcpHelloTool = (onUsed: () => void): AgentTool<typeof mcpHelloParameters, { source: "mcp"; tool: "say_hello" }> => ({
  name: "say_hello",
  label: "Say hello",
  description: "Call the connected Remymy Python MCP service to say hello. Use this when the user asks to test MCP or asks you to say hello.",
  parameters: mcpHelloParameters,
  executionMode: "sequential",
  execute: async (_toolCallId, params, signal) => {
    const name = params.name?.trim() || "there";
    const text = await callMcpTool("say_hello", { name }, signal);
    onUsed();
    return {
      content: [{ type: "text", text }],
      details: { source: "mcp", tool: "say_hello" },
    };
  },
});

export type MealAgent = {
  agent: Agent;
  getPreferences: () => Preferences;
  getRecommendations: () => MealRecommendation[];
  getToolsUsed: () => string[];
};

export function createMealAgent(
  model: Model<Api>,
  apiKey: string,
  preferences: Preferences,
  models: Models,
  initialMessages: Message[],
): MealAgent {
  let latestPreferences = preferences;
  let latestRecommendations: MealRecommendation[] = [];
  const toolsUsed = new Set<string>();
  let toolTurns = 0;
  const tool = mealTool(preferences, (nextPreferences, recommendations) => {
    toolTurns += 1;
    latestPreferences = nextPreferences;
    latestRecommendations = recommendations;
  });
  const systemPrompt = [
    "You are Remymy, a warm and concise meal recommendation assistant.",
    "The only authoritative meals are the curated MEALS returned by the recommend_meals tool.",
    "Call recommend_meals whenever the user asks for meal ideas, changes preferences, or asks what to eat.",
    "Call say_hello whenever the user asks to test MCP or asks you to say hello; it is backed by the Remymy Python MCP service.",
    `The authoritative starting preference state is ${JSON.stringify(preferences)}. Preserve its diet, time limit, and exclusions in every tool call.`,
    "Keep the final answer brief, human, and in simple markdown. Mention only meals returned by the tool.",
    "Do not provide medical or nutrition guarantees; the catalog contains demo estimates.",
  ].join(" ");
  const agent = new Agent({
    initialState: {
      systemPrompt,
      model,
      thinkingLevel: model.reasoning ? "minimal" : "off",
      tools: [tool, mcpHelloTool(() => toolsUsed.add("say_hello"))],
      messages: initialMessages,
    },
    streamFn: (activeModel, context, options) =>
      models.streamSimple(activeModel, context, {
        ...options,
        timeoutMs: CHAT_TIMEOUT_MS,
        maxRetries: 0,
      }),
    getApiKey: () => apiKey,
    toolExecution: "sequential",
    maxRetryDelayMs: CHAT_TIMEOUT_MS,
    beforeToolCall: async () =>
      toolTurns >= MAX_TOOL_TURNS
        ? { block: true, reason: "Recommendation lookup limit reached.", terminate: true }
        : undefined,
    shouldStopAfterTurn: () => toolTurns >= MAX_TOOL_TURNS,
  });
  return {
    agent,
    getPreferences: () => latestPreferences,
    getRecommendations: () => latestRecommendations,
    getToolsUsed: () => [...toolsUsed],
  };
}

export class ChatServiceError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ChatServiceError";
  }
}

export async function runChat(
  request: ChatRequest,
  model: Model<Api>,
  apiKey: string,
  models: Models,
  signal?: AbortSignal,
): Promise<ChatResult> {
  const latest = request.messages[request.messages.length - 1];
  if (!latest || latest.role !== "user") throw new ChatServiceError(400, "The last chat message must be from the user.");
  const effectivePreferences = interpretRequest(latest.text, request.preferences);
  const initialMessages = toAgentMessages(model, request.messages).slice(0, -1);
  const mealAgent = createMealAgent(model, apiKey, effectivePreferences, models, initialMessages);
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    mealAgent.agent.abort();
  }, CHAT_TIMEOUT_MS);
  const abort = () => mealAgent.agent.abort();
  signal?.addEventListener("abort", abort, { once: true });
  try {
    if (signal?.aborted) throw new ChatServiceError(499, "The request was cancelled.");
    await mealAgent.agent.prompt(latest.text);
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
  if (timedOut) throw new ChatServiceError(504, "The model request timed out. Try again.");
  if (signal?.aborted) throw new ChatServiceError(499, "The request was cancelled.");
  const finalMessage = [...mealAgent.agent.state.messages].reverse().find((message): message is AssistantMessage => message.role === "assistant");
  if (!finalMessage || finalMessage.stopReason === "error") throw new ChatServiceError(502, "The model could not complete this request.");
  if (finalMessage.stopReason === "aborted") throw new ChatServiceError(504, "The model request was interrupted. Try again.");
  const text = textFromAssistant(finalMessage);
  const recommendations = mealAgent.getRecommendations();
  return {
    text: text || (recommendations.length ? `I found ${recommendations.length} options in the Remymy catalog.` : "I couldn't find a match in the Remymy catalog with those preferences."),
    preferences: mealAgent.getPreferences(),
    recommendations,
    toolsUsed: mealAgent.getToolsUsed(),
  };
}

export function validateChatRequest(value: unknown): ChatRequest | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const candidate = value as Partial<ChatRequest>;
  if (!Array.isArray(candidate.messages) || candidate.messages.length < 1 || candidate.messages.length > MAX_CHAT_TURNS) return undefined;
  if (!candidate.messages.every((message) =>
    message && typeof message === "object" &&
    (message.role === "user" || message.role === "assistant") &&
    typeof message.text === "string" && message.text.trim().length > 0 && message.text.length <= MAX_CHAT_TEXT_CHARS,
  )) return undefined;
  if (!isPreferences(candidate.preferences)) return undefined;
  return { messages: candidate.messages, preferences: candidate.preferences };
}
