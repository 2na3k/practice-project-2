import { Agent, type AgentTool } from "@earendil-works/pi-agent-core";
import {
  Type,
  type Api,
  type AssistantMessage,
  type Message,
  type Model,
  type Models,
} from "@earendil-works/pi-ai";
import type { AgentStage, ChatRequest, ChatResult } from "@/lib/chat-types";
import { validBudget } from "../budget";
import type { Preferences } from "@/lib/meals";
import { interpretRequest, type MealRecommendation } from "@/lib/recommender";
import { MAX_CHAT_TEXT_CHARS, MAX_CHAT_TURNS } from "./request-guards";
import { callMcpTool } from "./mcp-client";
import { withLangfuseSpan } from "./langfuse";

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
    diet: Type.Union([Type.Literal("any"), Type.Literal("vegetarian"), Type.Literal("vegan"), Type.Literal("halal")]),
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
    (candidate.diet === "any" || candidate.diet === "vegetarian" || candidate.diet === "vegan" || candidate.diet === "halal") &&
    typeof candidate.maxMinutes === "number" && Number.isFinite(candidate.maxMinutes) &&
    candidate.maxMinutes >= 1 && candidate.maxMinutes <= 90 &&
    Array.isArray(candidate.exclude) && candidate.exclude.every((item) => typeof item === "string" && item.length <= 80) &&
    (candidate.goal === "balanced" || candidate.goal === "protein" || candidate.goal === "light") &&
    typeof candidate.cuisine === "string" && candidate.cuisine.length <= 80 &&
    validBudget(candidate.budget)
  );
};

// The lower of two budgets; one that is 0 or unset sets no limit, so the result is 0 only when neither does.
const lowerBudget = (a: number | undefined, b: number | undefined): number => {
  const limits = [a, b].filter((budget): budget is number => typeof budget === "number" && budget > 0);
  return limits.length ? Math.min(...limits) : 0;
};

/** Keep restrictions established by the user outside the model's control. */
const preserveHardPreferences = (candidate: Preferences, baseline: Preferences): Preferences => ({
  diet:
    baseline.diet === "vegan" || (baseline.diet === "vegetarian" && candidate.diet === "any") || (baseline.diet === "halal" && candidate.diet !== "vegan" && candidate.diet !== "halal")
      ? baseline.diet
      : candidate.diet,
  maxMinutes: Math.min(candidate.maxMinutes, baseline.maxMinutes),
  exclude: Array.from(new Set([
    ...baseline.exclude,
    ...candidate.exclude,
    ...(baseline.diet === "halal" && candidate.diet === "vegan" ? ["halal"] : []),
  ])),

  goal: candidate.goal,
  cuisine: candidate.cuisine,
  // the model may lower the budget but never raise or drop it
  ...((baseline.budget !== undefined || candidate.budget !== undefined) && { budget: lowerBudget(baseline.budget, candidate.budget) }),
});

const textFromAssistant = (message: AssistantMessage | undefined): string =>
  message?.content
    .filter((block): block is { type: "text"; text: string } => block.type === "text")
    .map((block) => block.text)
    .join("")
    .trim() ?? "";

const isMealRecommendationRequest = (text: string): boolean =>
  /\b(?:meal|food|eat|dinner|lunch|breakfast|dish|recipe|halal|crav\w*|hungry|cook\w*|meat|beef|chicken|turkey|pork|fish)\b/i.test(text);

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

const fetchMealRecommendations = async (
  preferences: Preferences,
  query: string,
  signal?: AbortSignal,
): Promise<MealRecommendation[]> => {
  const text = await callMcpTool("recommend_meals", {
    preferences,
    query: query.slice(0, MAX_CHAT_TEXT_CHARS),
  }, signal);
  const result: unknown = JSON.parse(text);
  if (!Array.isArray(result) || !result.every((item) =>
    item && typeof item === "object" &&
    "meal" in item && item.meal && typeof item.meal === "object" &&
    "id" in item.meal && typeof item.meal.id === "string" &&
    "title" in item.meal && typeof item.meal.title === "string" &&
    "score" in item && typeof item.score === "number" &&
    "reasons" in item && Array.isArray(item.reasons) && item.reasons.every((reason: unknown) => typeof reason === "string"),
  )) throw new Error("MCP returned invalid meal recommendations.");
  return result as MealRecommendation[];
};

const mealTool = (
  baseline: Preferences,
  onResult: (preferences: Preferences, recommendations: MealRecommendation[]) => void,
): AgentTool<typeof recommendationParameters, { preferences: Preferences; recommendations: MealRecommendation[] }> => ({
  name: "recommend_meals",
  label: "Recommend meals",
  description: "Use the Remymy MCP recommend_meals tool for every meal recommendation. Only present meals returned by MCP.",
  parameters: recommendationParameters,
  executionMode: "sequential",
  execute: async (_toolCallId, params, signal) => {
    const requested = isPreferences(params.preferences) ? params.preferences : baseline;
    const preferences = preserveHardPreferences(requested, baseline);
    const query = typeof params.query === "string" ? params.query : "";
    const interpreted = preserveHardPreferences(interpretRequest(query, preferences), baseline);
    const recommendations = await fetchMealRecommendations(interpreted, query, signal);
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
  execute: async (_toolCallId, params, signal) => withLangfuseSpan("say_hello", async (span) => {
    const name = params.name?.trim() || "there";
    span.update({ input: { name }, metadata: { tool: "say_hello", source: "mcp" } });
    const text = await callMcpTool("say_hello", { name }, signal);
    onUsed();
    span.update({ output: { text } });
    return {
      content: [{ type: "text", text }],
      details: { source: "mcp", tool: "say_hello" },
    };
  }, { asType: "tool" }),
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
    toolsUsed.add("recommend_meals");
    latestPreferences = nextPreferences;
    latestRecommendations = recommendations;
  });
  const systemPrompt = [
    "You are Remymy, a warm and concise meal recommendation assistant.",
    "The only authoritative meals are the recommendations returned by the Remymy MCP recommend_meals tool.",
    "Call the MCP recommend_meals tool whenever the user asks for meal ideas, changes preferences, or asks what to eat.",
    "Call say_hello whenever the user asks to test MCP or asks you to say hello; it is backed by the Remymy Python MCP service.",
    `The authoritative starting preference state is ${JSON.stringify(preferences)}. Preserve its diet, time limit, exclusions, and budget (most to spend on one meal, in SGD; 0 for no limit) in every tool call.`,
    "If the user wants meat-free meals, pass diet vegetarian or vegan into recommend_meals. Never list meat, poultry, or fish when that constraint is active.",
    "For halal requests, pass diet halal. The MCP returns vegan-tagged recipes that pass listed-ingredient screens; they are not halal-certified, so state that users must verify the source and cross-contamination.",
    "For halal requests, preserve the halal exclusion and say results are screened against listed ingredients only, not halal-certified; meat dishes are excluded because the source has no halal certification data.",
    "Keep the final answer brief, human, and in simple markdown. Mention only meals returned by the tool.",
    "Do not make medical or nutrition guarantees; recipe times and nutrition values are estimates from the source data.",
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
  onStage?: (stage: AgentStage) => void,
): Promise<ChatResult> {
  return withLangfuseSpan("pi-agent-chat", async (span) => {
    const input = { messages: request.messages, preferences: request.preferences };
    span.update({
      input,
      metadata: { provider: model.provider, model: model.id, runtime: "pi-agent-core" },
    });
    span.setTraceIO?.({ input });
    const latest = request.messages[request.messages.length - 1];
    if (!latest || latest.role !== "user") throw new ChatServiceError(400, "The last chat message must be from the user.");
    onStage?.({ id: "understand", label: "Understanding your request", detail: "Reading your message and saved food preferences", status: "active" });
    const effectivePreferences = interpretRequest(latest.text, request.preferences);
    onStage?.({ id: "understand", label: "Understanding your request", detail: "Preferences and constraints are ready", status: "complete" });
    const initialMessages = toAgentMessages(model, request.messages).slice(0, -1);
    const mealAgent = createMealAgent(model, apiKey, effectivePreferences, models, initialMessages);
    let turn = 0;
    let activeTurnStage = "";
    const toolStages = new Map<string, string>();
    mealAgent.agent.subscribe((event) => {
      if (event.type === "turn_start") {
        turn += 1;
        activeTurnStage = `turn-${turn}`;
        onStage?.({
          id: activeTurnStage,
          label: turn === 1 ? "Planning the best next step" : "Writing your answer",
          detail: turn === 1 ? `${model.name} is deciding which tools to use` : `${model.name} is turning the results into a useful reply`,
          status: "active",
        });
      } else if (event.type === "message_end" && event.message.role === "assistant" && activeTurnStage) {
        onStage?.({
          id: activeTurnStage,
          label: turn === 1 ? "Planning the best next step" : "Writing your answer",
          detail: turn === 1 ? "The next action has been selected" : "The response is ready",
          status: "complete",
        });
      } else if (event.type === "tool_execution_start") {
        const id = `tool-${event.toolCallId}`;
        toolStages.set(event.toolCallId, id);
        const isMealMcp = event.toolName === "recommend_meals";
        onStage?.({
          id,
          label: isMealMcp ? "Calling MCP meal recommendations" : "Calling the MCP service",
          detail: isMealMcp ? "Sending your preferences to the Python MCP tool" : "Running say_hello through the Python service",
          status: "active",
        });
      } else if (event.type === "tool_execution_end") {
        const id = toolStages.get(event.toolCallId) ?? `tool-${event.toolCallId}`;
        const isMealMcp = event.toolName === "recommend_meals";
        onStage?.({
          id,
          label: isMealMcp ? "Calling MCP meal recommendations" : "Calling the MCP service",
          detail: event.isError ? "The tool could not complete" : isMealMcp ? "MCP recommendations are ready" : "The MCP call completed",
          status: event.isError ? "error" : "complete",
        });
      }
    });
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
    const preferences = preserveHardPreferences(mealAgent.getPreferences(), effectivePreferences);
    let toolRecommendations = mealAgent.getRecommendations();
    let usedCatalogFallback = false;
    if (!toolRecommendations.length && isMealRecommendationRequest(latest.text)) {
      usedCatalogFallback = true;
      const fallbackStageId = "tool-catalog-fallback";
      onStage?.({ id: fallbackStageId, label: "Calling MCP meal recommendations", detail: "The model skipped the tool, so Remymy is asking MCP directly", status: "active" });
      toolRecommendations = await fetchMealRecommendations(preferences, latest.text, signal);
      onStage?.({ id: fallbackStageId, label: "Calling MCP meal recommendations", detail: `${toolRecommendations.length} MCP recommendations are ready`, status: "complete" });
    }
    const recommendations = toolRecommendations;
    const toolsUsed = mealAgent.getToolsUsed();
    if (usedCatalogFallback && !toolsUsed.includes("recommend_meals")) toolsUsed.push("recommend_meals");
    const fallbackText = recommendations.length
      ? `I found ${recommendations.slice(0, 3).map((item) => `**${item.meal.title}**`).join(", ")}. Open any meal below for the full details.`
      : "I couldn't find a match in the Food.com recipe data with those preferences.";
    const result = {
      text: usedCatalogFallback ? fallbackText : text || fallbackText,
      preferences,
      recommendations,
      toolsUsed,
    };
    span.update({
      output: {
        text: result.text,
        toolsUsed: result.toolsUsed,
        recommendationCount: result.recommendations.length,
        titles: result.recommendations.slice(0, 8).map((item) => item.meal.title),
      },
    });
    span.setTraceIO?.({ output: result });
    return result;
  }, { asType: "agent", traceName: "remymy-agent-chat" });
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
