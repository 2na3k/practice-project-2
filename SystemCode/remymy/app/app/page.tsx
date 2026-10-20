"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowRight,
  Bookmark,
  Check,
  History as HistoryIcon,
  Circle,
  LoaderCircle,
  MessageCircle,
  Plus,
  RotateCcw,
  ChefHat,
  Sparkles,
  Utensils,
  X,
} from "lucide-react";
import { Composer } from "@/components/Composer";
import { MealCard } from "@/components/MealCard";
import type { MealRecommendation } from "@/components/MealCard";
import { MealDetailModal } from "@/components/MealDetailModal";
import { PreferencePanel } from "@/components/PreferencePanel";
import { Sidebar, type AppView } from "@/components/Sidebar";
import { UserAvatar } from "@/components/UserAvatar";
import { DEMO_PROFILE } from "@/lib/demo-profile";
import { ConnectionPanel } from "@/components/ConnectionPanel";
import { RecipeCard } from "@/components/RecipeCard";
import { RecipeDetailModal } from "@/components/RecipeDetailModal";
import { CatalogItemModal } from "@/components/CatalogItemModal";
import { AddRecipePanel } from "@/components/AddRecipePanel";
import { RecommendationPanel } from "@/components/RecommendationPanel";
import { RecipeLikeButtons, type RecipeRatingState } from "@/components/RecipeLikeButtons";
import {
  DEFAULT_PREFERENCES,
  MEALS,
  type Meal,
  type Preferences,
} from "@/lib/meals";
import { normalizeBudget, validBudget } from "@/lib/budget";
import { demoReply } from "@/lib/demo-chat";
import { alignRecommendations, interpretRequest, recommendMeals } from "@/lib/recommender";
import type {
  AgentStage,
  ChatResult,
  ChatStreamEvent,
  ChatTurn,
  ConnectionStatus,
} from "@/lib/chat-types";
import { fetchRecipeDetail, fetchRecipeLikes, fetchRecipePage, saveRecipeRating, type RecipeDetail, type RecipePage, type RecipeSummary } from "@/lib/recipes";
import { RECOMMENDATION_USERNAME, fetchRecommendations, formatPrice, nutrientHighlights, type BuyingProfile } from "@/lib/recommendations";
import { parseLikes, parseSavedFoods, type SavedFood } from "@/lib/saved";
import { fetchHistory, formatHistoryDate, type HistoryItem } from "@/lib/history";
import { fetchCatalogItem, type CatalogItem } from "@/lib/catalog";

const FILTERS = [
  { id: "all", label: "All cravings" },
  { id: "quick", label: "Quick bites" },
  { id: "plant", label: "Plant-powered" },
  { id: "protein", label: "Protein-packed" },
] as const;

type ChatMessage = {
  id: string;
  role: ChatTurn["role"];
  text: string;
  recommendations?: MealRecommendation[];
  status?: "pending" | "error";
  source?: string;
  stages?: AgentStage[];
};

type FailedRequest = {
  text: string;
  assistantId: string;
  history: ChatTurn[];
  preferences: Preferences;
};

function validPreferences(value: unknown): value is Preferences {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Preferences>;
  return (
    (candidate.diet === "any" || candidate.diet === "vegetarian" || candidate.diet === "vegan" || candidate.diet === "halal") &&
    typeof candidate.maxMinutes === "number" && Number.isFinite(candidate.maxMinutes) &&
    candidate.maxMinutes >= 1 && candidate.maxMinutes <= 90 &&
    Array.isArray(candidate.exclude) && candidate.exclude.every((item) => typeof item === "string") &&
    (candidate.goal === "balanced" || candidate.goal === "protein" || candidate.goal === "light") &&
    typeof candidate.cuisine === "string" &&
    // preferences saved before there was a budget have none
    validBudget(candidate.budget)
  );
}

function validRecommendations(value: unknown): value is MealRecommendation[] {
  return Array.isArray(value) && value.every((item) => {
    if (!item || typeof item !== "object") return false;
    const recommendation = item as Partial<MealRecommendation>;
    const meal = recommendation.meal;
    return Boolean(
      meal && typeof meal === "object" && typeof meal.id === "string" &&
      typeof meal.title === "string" && typeof meal.description === "string" &&
      typeof meal.cuisine === "string" && typeof meal.minutes === "number" &&
      typeof meal.calories === "number" && typeof meal.protein === "number" &&
      Array.isArray(meal.tags) && meal.tags.every((tag) => typeof tag === "string") &&
      Array.isArray(meal.ingredients) && meal.ingredients.every((ingredient) => typeof ingredient === "string") &&
      Array.isArray(meal.steps) && meal.steps.every((step) => typeof step === "string") &&
      typeof meal.image === "string" && typeof meal.color === "string" &&
      typeof recommendation.score === "number" && Array.isArray(recommendation.reasons) &&
      recommendation.reasons.every((reason) => typeof reason === "string"),
    );
  });
}

function validChatResult(value: unknown): value is ChatResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ChatResult>;
  return typeof result.text === "string" && validPreferences(result.preferences) && validRecommendations(result.recommendations) &&
    (result.toolsUsed === undefined || (Array.isArray(result.toolsUsed) && result.toolsUsed.every((tool) => typeof tool === "string")));
}

function validConnectionStatus(value: unknown): value is ConnectionStatus {
  if (!value || typeof value !== "object") return false;
  const status = value as Partial<ConnectionStatus>;
  return typeof status.connected === "boolean" &&
    (status.provider === undefined || typeof status.provider === "string") &&
    (status.model === undefined || typeof status.model === "string") &&
    (status.expiresAt === undefined || typeof status.expiresAt === "number");
}

function connectionFromPayload(value: unknown): ConnectionStatus | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { status?: unknown };
  return validConnectionStatus(candidate.status) ? candidate.status : validConnectionStatus(value) ? value : null;
}

function errorMessage(value: unknown, fallback: string): string {
  if (value && typeof value === "object" && "error" in value) {
    const message = (value as { error?: unknown }).error;
    if (typeof message === "string" && message.trim()) return message;
  }
  return fallback;
}

function readMcpMessage(value: unknown): string | null {
  if (!value || typeof value !== "object" || !("message" in value)) return null;
  const message = (value as { message?: unknown }).message;
  return typeof message === "string" && message.trim() ? message : null;
}

function validAgentStage(value: unknown): value is AgentStage {
  if (!value || typeof value !== "object") return false;
  const stage = value as Partial<AgentStage>;
  return typeof stage.id === "string" && typeof stage.label === "string" && typeof stage.detail === "string" &&
    (stage.status === "active" || stage.status === "complete" || stage.status === "error");
}

function upsertStage(stages: AgentStage[] | undefined, stage: AgentStage): AgentStage[] {
  const current = stages ?? [];
  const index = current.findIndex((item) => item.id === stage.id);
  if (index < 0) return [...current, stage];
  return current.map((item, itemIndex) => itemIndex === index ? stage : item);
}

async function readChatStream(response: Response, onStage: (stage: AgentStage) => void): Promise<ChatResult> {
  if (!response.body) throw new Error("The agent stream could not be opened.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  let result: ChatResult | null = null;
  const readLine = (line: string) => {
    if (!line.trim()) return;
    const event: unknown = JSON.parse(line);
    if (!event || typeof event !== "object" || !("type" in event)) return;
    const candidate = event as Partial<ChatStreamEvent>;
    if (candidate.type === "stage" && "stage" in candidate && validAgentStage(candidate.stage)) onStage(candidate.stage);
    if (candidate.type === "result" && "result" in candidate && validChatResult(candidate.result)) result = candidate.result;
    if (candidate.type === "error" && "error" in candidate && typeof candidate.error === "string") throw new Error(candidate.error);
  };
  while (true) {
    const { value, done } = await reader.read();
    buffered += decoder.decode(value, { stream: !done });
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    lines.forEach(readLine);
    if (done) break;
  }
  readLine(buffered);
  if (!result) throw new Error("The agent finished without a result.");
  return result;
}

function makeId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function boundedHistory(messages: ChatMessage[]): ChatTurn[] {
  return messages.filter((message) => !message.status).map(({ role, text }) => ({ role, text: text.slice(0, 2000) })).slice(-24);
}

export default function HomePage() {
  const [view, setView] = useState<AppView>("discover");
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [savedIds, setSavedIds] = useState<string[]>([]);
  const [savedFoods, setSavedFoods] = useState<SavedFood[]>([]);
  // like or not of each demo meal, by meal id, kept in this browser
  const [mealLikes, setMealLikes] = useState<Record<string, boolean>>({});
  // like or not of each recommended recipe, by fdc_id, as the Data API's users database holds it
  const [foodRatings, setFoodRatings] = useState<Record<string, RecipeRatingState>>({});
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [connection, setConnection] = useState<ConnectionStatus>({ connected: false });
  const [connectionLoadError, setConnectionLoadError] = useState("");
  const [mealShortlist, setMealShortlist] = useState<MealRecommendation[] | null>(null);
  // how many foods "Picked for you" last loaded; null until it has loaded. The banner shows only when it found none.
  const [pickCount, setPickCount] = useState<number | null>(null);
  const [activeMeal, setActiveMeal] = useState<Meal | null>(null);
  const [recipePage, setRecipePage] = useState<RecipePage | null>(null);
  const [recipeSearch, setRecipeSearch] = useState("");
  const [recipePageNumber, setRecipePageNumber] = useState(0);
  const [recipeRetryNonce, setRecipeRetryNonce] = useState(0);
  const [recipeBusy, setRecipeBusy] = useState(false);
  const [recipeLoadError, setRecipeLoadError] = useState("");
  const [activeRecipe, setActiveRecipe] = useState<RecipeDetail | null>(null);
  const [recipeDetailBusy, setRecipeDetailBusy] = useState(false);
  const [recipeDetailError, setRecipeDetailError] = useState("");
  // the recommended recipe whose details are open, from the Data API's catalog
  const [activeCatalogItem, setActiveCatalogItem] = useState<CatalogItem | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const catalogRef = useRef<AbortController | null>(null);
  // the demo user's history, read again from the Data API each time the History page opens; null until first read
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [historyNonce, setHistoryNonce] = useState(0);
  // averages of the priced ingredients in the rated recipes, which the recommend route works out from the history
  const [buyingProfile, setBuyingProfile] = useState<BuyingProfile | null>(null);
  const [notice, setNotice] = useState("");
  const [mcpMessage, setMcpMessage] = useState("");
  const [mcpBusy, setMcpBusy] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [failedRequest, setFailedRequest] = useState<FailedRequest | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const recipeDetailRef = useRef<AbortController | null>(null);
  const pendingAssistantRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const closeMeal = useCallback(() => setActiveMeal(null), []);
  const closeRecipe = useCallback(() => {
    recipeDetailRef.current?.abort();
    recipeDetailRef.current = null;
    setActiveRecipe(null);
    setRecipeDetailError("");
    setRecipeDetailBusy(false);
  }, []);

  useEffect(() => {
    if (view !== "meals" || mealShortlist) return;
    const controller = new AbortController();
    void Promise.resolve().then(() => {
      if (controller.signal.aborted) return null;
      setRecipeBusy(true);
      setRecipeLoadError("");
      return fetchRecipePage({ q: recipeSearch.trim(), limit: 24, offset: recipePageNumber * 24 }, controller.signal);
    })
      .then((nextPage) => { if (nextPage && mountedRef.current) setRecipePage(nextPage); })
      .catch((error) => { if (!controller.signal.aborted && mountedRef.current) setRecipeLoadError(error instanceof Error ? error.message : "Recipes could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted && mountedRef.current) setRecipeBusy(false); });
    return () => controller.abort();
  }, [mealShortlist, recipePageNumber, recipeRetryNonce, recipeSearch, view]);

  useEffect(() => {
    if (view !== "history") return;
    const controller = new AbortController();
    void Promise.resolve().then(() => {
      if (controller.signal.aborted) return null;
      setHistoryError("");
      return fetchHistory(RECOMMENDATION_USERNAME, controller.signal);
    })
      .then((items) => { if (items && mountedRef.current) setHistory(items); })
      .catch((error) => { if (!controller.signal.aborted && mountedRef.current) setHistoryError(error instanceof Error ? error.message : "Your history could not be loaded."); });
    // the profile is extra: if it cannot be read, the History page goes on without it
    void fetchRecommendations(RECOMMENDATION_USERNAME, 1, controller.signal)
      .then((result) => { if (mountedRef.current) setBuyingProfile(result.profile); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [historyNonce, view]);

  const openCatalogItem = useCallback((id: string) => {
    catalogRef.current?.abort();
    const controller = new AbortController();
    catalogRef.current = controller;
    setActiveCatalogItem(null);
    setCatalogError("");
    setCatalogBusy(true);
    void fetchCatalogItem(id, controller.signal)
      .then((item) => { if (mountedRef.current) setActiveCatalogItem(item); })
      .catch((error) => { if (!controller.signal.aborted && mountedRef.current) setCatalogError(error instanceof Error ? error.message : "This recipe could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted && mountedRef.current) { setCatalogBusy(false); catalogRef.current = null; } });
  }, []);

  const closeCatalogItem = useCallback(() => {
    catalogRef.current?.abort();
    catalogRef.current = null;
    setActiveCatalogItem(null);
    setCatalogError("");
    setCatalogBusy(false);
  }, []);

  const openRecipe = useCallback((summary: Pick<RecipeSummary, "id">) => {
    recipeDetailRef.current?.abort();
    const controller = new AbortController();
    recipeDetailRef.current = controller;
    setActiveRecipe(null);
    setRecipeDetailError("");
    setRecipeDetailBusy(true);
    void fetchRecipeDetail(summary.id, controller.signal)
      .then((detail) => { if (mountedRef.current) setActiveRecipe(detail); })
      .catch((error) => { if (!controller.signal.aborted && mountedRef.current) setRecipeDetailError(error instanceof Error ? error.message : "This recipe could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted && mountedRef.current) { setRecipeDetailBusy(false); recipeDetailRef.current = null; } });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    let storedPreferences: Preferences | null = null;
    let storedSaved: string[] | null = null;
    let storedFoods: SavedFood[] = [];
    let storedMealLikes: Record<string, boolean> = {};
    try {
      const preferencesValue = window.localStorage.getItem("remymy-preferences");
      const savedValue = window.localStorage.getItem("remymy-saved-meals");
      storedFoods = parseSavedFoods(JSON.parse(window.localStorage.getItem("remymy-saved-foods") ?? "[]"));
      const mealLikes = parseLikes(JSON.parse(window.localStorage.getItem("remymy-meal-likes") ?? "{}"));
      storedMealLikes = Object.fromEntries(Object.entries(mealLikes).filter(([id]) => MEALS.some((meal) => meal.id === id)));
      if (preferencesValue) {
        const parsed: unknown = JSON.parse(preferencesValue);
        // a budget saved as null, or none at all, is stored as 0, no limit
        if (validPreferences(parsed)) storedPreferences = { ...parsed, budget: normalizeBudget(parsed.budget) };
      }
      if (savedValue) {
        const parsed: unknown = JSON.parse(savedValue);
        if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string" && MEALS.some((meal) => meal.id === item))) storedSaved = parsed;
      }
    } catch {
      // Browser storage is optional and can be disabled by an embed or privacy mode.
    }
    const frame = window.requestAnimationFrame(() => {
      if (!mountedRef.current) return;
      if (storedPreferences) setPreferences(storedPreferences);
      if (storedSaved) setSavedIds(storedSaved);
      // merged under anything chosen before storage was read
      setSavedFoods((current) => [...storedFoods.filter((food) => !current.some((item) => item.id === food.id)), ...current]);
      setMealLikes((current) => ({ ...storedMealLikes, ...current }));
      setIsHydrated(true);
    });
    return () => {
      mountedRef.current = false;
      window.cancelAnimationFrame(frame);
      requestRef.current?.abort();
      recipeDetailRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!isHydrated) return;
    try { window.localStorage.setItem("remymy-preferences", JSON.stringify(preferences)); } catch { /* best effort */ }
  }, [isHydrated, preferences]);

  useEffect(() => {
    if (!isHydrated) return;
    try { window.localStorage.setItem("remymy-saved-meals", JSON.stringify(savedIds)); } catch { /* best effort */ }
  }, [isHydrated, savedIds]);

  useEffect(() => {
    if (!isHydrated) return;
    try {
      window.localStorage.setItem("remymy-saved-foods", JSON.stringify(savedFoods));
      window.localStorage.setItem("remymy-meal-likes", JSON.stringify(mealLikes));
    } catch { /* best effort */ }
  }, [isHydrated, mealLikes, savedFoods]);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/connection", { method: "GET", cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const data: unknown = await response.json().catch(() => null);
        if (!response.ok) throw new Error(errorMessage(data, "Connection status is unavailable."));
        const status = connectionFromPayload(data);
        if (!status) throw new Error("Connection status returned an unexpected response.");
        if (mountedRef.current) setConnection(status);
      })
      .catch((error) => {
        if (!controller.signal.aborted && mountedRef.current) setConnectionLoadError(error instanceof Error ? error.message : "Connection status is unavailable.");
      });
    return () => controller.abort();
  }, []);

  // The recipe likes live in the Data API: read them again each time the picks or Saved come up, so the buttons show
  // what the database holds. A like still being saved keeps its choice until the save answers.
  const showsRecipeLikes = view === "saved" || (view === "discover" && chat.length === 0);
  useEffect(() => {
    if (!showsRecipeLikes) return;
    const controller = new AbortController();
    void fetchRecipeLikes(RECOMMENDATION_USERNAME, controller.signal)
      .then((likes) => {
        if (!mountedRef.current) return;
        setFoodRatings((current) => {
          const ids = new Set([...Object.keys(current), ...Object.keys(likes)]);
          return Object.fromEntries([...ids].map((id) => [id, { ...current[id], like: current[id]?.saving ? current[id].like : likes[id] }]));
        });
      })
      // without the Data API the buttons still save; they show its likes again on the next load
      .catch(() => {});
    return () => controller.abort();
  }, [showsRecipeLikes]);

  const navigate = useCallback((nextView: AppView) => {
    if (nextView === "meals") setMealShortlist(null);
    setView(nextView);
  }, []);

  const savedMeals = useMemo(() => MEALS.filter((meal) => savedIds.includes(meal.id)), [savedIds]);
  const enginePreferences = useMemo<Preferences>(() => {
    if (filter === "quick") return { ...preferences, maxMinutes: Math.min(preferences.maxMinutes, 20) };
    if (filter === "plant") return { ...preferences, diet: preferences.diet === "vegan" ? "vegan" : "vegetarian" };
    if (filter === "protein") return { ...preferences, goal: "protein" };
    return preferences;
  }, [filter, preferences]);
  const browseRecommendations = useMemo(() => mealShortlist ?? recommendMeals(enginePreferences, undefined, MEALS.length), [enginePreferences, mealShortlist]);

  const flashNotice = useCallback((text: string) => {
    setNotice(text);
    window.setTimeout(() => { if (mountedRef.current) setNotice(""); }, 2600);
  }, []);

  const toggleSaved = useCallback((meal: Meal) => {
    const isSaved = savedIds.includes(meal.id);
    setSavedIds((current) => current.includes(meal.id) ? current.filter((id) => id !== meal.id) : [...current, meal.id]);
    flashNotice(isSaved ? `${meal.title} removed from saved meals` : `${meal.title} saved for later`);
  }, [flashNotice, savedIds]);

  const toggleSavedFood = useCallback((food: SavedFood) => {
    const isSaved = savedFoods.some((item) => item.id === food.id);
    setSavedFoods((current) => current.some((item) => item.id === food.id) ? current.filter((item) => item.id !== food.id) : [...current, food]);
    flashNotice(isSaved ? `${food.title} removed from saved recipes` : `${food.title} saved for later`);
  }, [flashNotice, savedFoods]);

  // A recommended recipe is a catalog item; the Data API's rating route takes its fdc_id as the recipe_id.
  const rateFood = useCallback((id: string, like: boolean) => {
    const update = (next: RecipeRatingState) => setFoodRatings((current) => ({ ...current, [id]: { ...current[id], ...next } }));
    update({ saving: true, error: undefined });
    void saveRecipeRating(id, RECOMMENDATION_USERNAME, like)
      .then((saved) => { if (mountedRef.current) update({ like: saved.like, saving: false }); })
      .catch((error) => { if (mountedRef.current) update({ saving: false, error: error instanceof Error ? error.message : "Your rating could not be saved." }); });
  }, []);

  const rateMeal = useCallback((meal: Meal, like: boolean) => setMealLikes((current) => ({ ...current, [meal.id]: like })), []);

  const stopRequest = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    const pendingId = pendingAssistantRef.current;
    if (pendingId) setChat((current) => current.filter((message) => message.id !== pendingId));
    pendingAssistantRef.current = null;
    setIsBusy(false);
  }, []);

  const submitMessage = useCallback(async (rawText: string, retry = false) => {
    const text = rawText.trim().slice(0, 2000);
    if (!text || isBusy) return;
    const retryRequest = retry ? failedRequest : null;
    if (retry && !retryRequest) return;
    const assistantId = retryRequest?.assistantId ?? makeId("assistant");
    const currentHistory = retryRequest?.history ?? boundedHistory([...chat, { id: makeId("user"), role: "user", text }]);
    const requestPreferences = retryRequest?.preferences ?? preferences;
    const userMessage: ChatMessage = { id: makeId("user"), role: "user", text };
    const responseSource = connection.connected ? `${connection.provider ?? "AI"} · ${connection.model ?? "connected"}` : "Demo";
    setChat((current) => retryRequest ? current.map((message) => message.id === assistantId ? { ...message, text: "", status: "pending", source: responseSource } : message) : [...current, userMessage, { id: assistantId, role: "assistant", text: "", status: "pending", source: responseSource }]);
    setFailedRequest(null);
    setIsBusy(true);
    pendingAssistantRef.current = assistantId;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      let result: ChatResult;
      if (connection.connected) {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
          body: JSON.stringify({ messages: currentHistory, preferences: requestPreferences }),
          signal: controller.signal,
        });
        if (!response.ok) {
          const data: unknown = await response.json().catch(() => null);
          throw new Error(errorMessage(data, "Your connected model could not answer."));
        }
        result = await readChatStream(response, (stage) => {
          if (!mountedRef.current) return;
          setChat((current) => current.map((message) => message.id === assistantId
            ? { ...message, stages: upsertStage(message.stages, stage) }
            : message));
        });
      } else {
        result = await Promise.resolve(demoReply(text, requestPreferences, chat.length));
      }
      if (controller.signal.aborted || !mountedRef.current) return;
      const alignedPreferences = interpretRequest(text, result.preferences);
      const alignedRecommendations = !connection.connected && result.recommendations.length
        ? alignRecommendations(result.recommendations, alignedPreferences, text)
        : result.recommendations;
      setPreferences(alignedPreferences);
      setChat((current) => current.map((message) => message.id === assistantId ? {
        ...message,
        text: result.text,
        status: undefined,
        source: result.toolsUsed?.includes("say_hello")
          ? `${responseSource} · MCP`
          : result.toolsUsed?.includes("recommend_meals") ? `${responseSource} · MCP` : responseSource,
        recommendations: alignedRecommendations,
      } : message));
    } catch (error) {
      if (controller.signal.aborted || !mountedRef.current) return;
      const message = error instanceof Error ? error.message : "The model could not answer.";
      setChat((current) => current.map((item) => item.id === assistantId ? { ...item, text: message, status: "error", recommendations: undefined } : item));
      setFailedRequest({ text, assistantId, history: currentHistory, preferences: requestPreferences });
    } finally {
      if (mountedRef.current && !controller.signal.aborted) {
        setIsBusy(false);
        pendingAssistantRef.current = null;
        requestRef.current = null;
      }
    }
  }, [chat, connection.connected, connection.model, connection.provider, failedRequest, isBusy, preferences]);

  const startNewChat = useCallback(() => {
    stopRequest();
    setChat([]);
    setFailedRequest(null);
    setMealShortlist(null);
    setView("discover");
  }, [stopRequest]);
  const savePreferences = useCallback((next: Preferences) => {
    stopRequest();
    setPreferences(next);
    setMealShortlist(null);
    setFilter("all");
    setNotice("Preferences updated");
    window.setTimeout(() => { if (mountedRef.current) setNotice(""); }, 2600);
  }, [stopRequest]);
  const showShortlist = useCallback((recommendations: MealRecommendation[]) => {
    setMealShortlist(recommendations.slice(0, 6));
    setView("meals");
  }, []);
  const tryMcpHello = useCallback(async () => {
    setMcpBusy(true);
    setMcpMessage("");
    try {
      const response = await fetch("/api/mcp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Remymy" }),
      });
      const data: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(data, "The MCP service could not be reached."));
      setMcpMessage(readMcpMessage(data) ?? "Hello from the Python MCP service.");
    } catch (error) {
      setMcpMessage(error instanceof Error ? error.message : "The MCP service could not be reached.");
    } finally {
      setMcpBusy(false);
    }
  }, []);
  const modelLabel = connection.connected ? `${connection.provider ?? "AI"} · ${connection.model ?? "connected"}` : "Local demo";
  const hasChat = chat.length > 0;
  const savedRecommendations = useMemo(() => savedMeals.map((meal) => ({ meal, score: 1, reasons: ["Saved by you"] })), [savedMeals]);

  return (
    <div className="app-shell chat-shell">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <Sidebar view={view} onViewChange={navigate} onNewChat={startNewChat} connection={connection} />
      <main className={`app-main workspace-main ${view === "discover" ? (hasChat ? "is-chat" : "is-welcome") : ""}`} id="main-content">
        {view === "discover" ? (
          <>
            <header className="workspace-header">
              <div><p className="eyebrow">YOUR DAILY DOSE OF DELICIOUS</p><h1 className="workspace-title">{hasChat ? "Your food conversation" : "What sounds good?"}</h1></div>
              <div className="workspace-actions"><button className="model-status-button" onClick={() => setView("connection")}><span className={`connection-dot ${connection.connected ? "connected" : ""}`} />{modelLabel}</button>{connectionLoadError && <span className="connection-load-note">Status unavailable</span>}{hasChat && <button className="header-new-chat" onClick={startNewChat}><Plus className="header-new-chat-icon" size={15} aria-hidden="true" />New chat</button>}<button className="header-avatar" onClick={() => setView("preferences")} aria-label={`Signed in as ${DEMO_PROFILE.name}, open your preferences`} title={DEMO_PROFILE.name}><UserAvatar size={34} /></button></div>
            </header>
            {hasChat ? <ChatWorkspace messages={chat} failedAssistantId={failedRequest?.assistantId} onRetry={() => void submitMessage(failedRequest?.text ?? "", true)} retryAvailable={Boolean(failedRequest) && !isBusy} onOpenMeal={setActiveMeal} mealLikes={mealLikes} onRateMeal={rateMeal} onViewShortlist={showShortlist} onSubmit={(text) => void submitMessage(text)} onStop={stopRequest} busy={isBusy} modelLabel={modelLabel} onConnection={() => setView("connection")} /> : (
              <section className="welcome-screen">
                <RecommendationPanel onItemCount={setPickCount} ratings={foodRatings} onRate={rateFood} savedIds={savedFoods.map((food) => food.id)} onSave={toggleSavedFood} onOpen={openCatalogItem} />
                {pickCount === 0 && <div className="hero-grid"><div className="hero-copy"><p className="eyebrow">THE KIND OF HELPFUL YOU CAN EAT</p><h2 className="hero-title">Less overthinking.<br /><em>More good food.</em></h2><p className="hero-description">A tiny nudge toward something delicious, tuned to your cravings, your time, and the ingredients you love.</p></div><div className="hero-art" aria-label="Illustration of a colorful bowl of food" role="img"><div className="hero-sun" /><div className="hero-plate"><span className="hero-food hero-food-one" /><span className="hero-food hero-food-two" /><span className="hero-food hero-food-three" /><span className="hero-food hero-food-four" /><span className="hero-leaf" /></div><span className="hero-art-label">GOOD THINGS<br /><strong>COMING UP</strong></span></div></div>}
                <Composer onSubmit={(text) => void submitMessage(text)} onStop={stopRequest} busy={isBusy} welcome modelLabel={modelLabel} onConnection={() => setView("connection")} />
                <p className="welcome-hint">Try a craving, a time limit, or an ingredient you want to use up.</p>
                <div className="mcp-demo">
                  <button className="mcp-demo-button" onClick={() => void tryMcpHello()} disabled={mcpBusy}>
                    <MessageCircle size={14} aria-hidden="true" />
                    {mcpBusy ? "Calling Python MCP…" : "Try MCP hello"}
                  </button>
                  {mcpMessage && <span className={`mcp-demo-result ${mcpMessage.startsWith("The Python MCP") ? "is-error" : ""}`} role="status">{mcpMessage}</span>}
                </div>
              </section>
            )}
          </>
        ) : view === "meals" ? mealShortlist ? <MealsView recommendations={browseRecommendations} preferences={preferences} isShortlist filter={filter} onFilterChange={(next) => { setMealShortlist(null); setFilter(next); }} onSave={toggleSaved} savedIds={savedIds} onOpen={setActiveMeal} mealLikes={mealLikes} onRateMeal={rateMeal} onPreferences={() => navigate("preferences")} onBrowseAll={() => setMealShortlist(null)} /> : <RecipesView page={recipePage} search={recipeSearch} busy={recipeBusy} error={recipeLoadError} pageNumber={recipePageNumber} onSearch={(next) => { setRecipeSearch(next); setRecipePageNumber(0); }} onRetry={() => setRecipeRetryNonce((current) => current + 1)} onPageChange={setRecipePageNumber} onOpen={openRecipe} />
          : view === "saved" ? <SavedView recommendations={savedRecommendations} onSave={toggleSaved} onOpen={setActiveMeal} mealLikes={mealLikes} onRateMeal={rateMeal} foods={savedFoods} foodRatings={foodRatings} onRateFood={rateFood} onToggleFood={toggleSavedFood} onOpenFood={openCatalogItem} onDiscover={() => navigate("discover")} />
            : view === "add-recipe" ? <AddRecipePanel username={RECOMMENDATION_USERNAME} onSaved={(recipe) => flashNotice(`${recipe.title} added to your recipes`)} />
            : view === "history" ? <HistoryView items={history} profile={buyingProfile} error={historyError} onRetry={() => setHistoryNonce((current) => current + 1)} onOpenRecipe={openRecipe} onOpenFood={openCatalogItem} />
            : view === "preferences" ? <PreferencePanel preferences={preferences} onSave={savePreferences} />
              : <ConnectionPanel connection={connection} onConnectionChange={setConnection} onBackToChat={() => setView("discover")} />}
      </main>
      {notice && <div className="notice" role="status"><Bookmark size={16} aria-hidden="true" />{notice}</div>}
      <MealDetailModal meal={activeMeal} saved={activeMeal ? savedIds.includes(activeMeal.id) : false} onClose={closeMeal} onSave={toggleSaved} />
      <RecipeDetailModal recipe={activeRecipe} loading={recipeDetailBusy} error={recipeDetailError} onClose={closeRecipe} onOpenFood={(id) => { closeRecipe(); openCatalogItem(id); }} />
      <CatalogItemModal item={activeCatalogItem} loading={catalogBusy} error={catalogError} onClose={closeCatalogItem} rating={activeCatalogItem ? foodRatings[String(activeCatalogItem.fdc_id)] : undefined} onRate={rateFood} saved={activeCatalogItem ? savedFoods.some((food) => food.id === String(activeCatalogItem.fdc_id)) : false} onSave={(item) => toggleSavedFood({ id: String(item.fdc_id), title: item.description })} />
    </div>
  );
}

function ChatWorkspace({ messages, failedAssistantId, onRetry, retryAvailable, onOpenMeal, mealLikes, onRateMeal, onViewShortlist, onSubmit, onStop, busy, modelLabel, onConnection }: { messages: ChatMessage[]; failedAssistantId?: string; onRetry: () => void; retryAvailable: boolean; onOpenMeal: (meal: Meal) => void; mealLikes: Record<string, boolean>; onRateMeal: (meal: Meal, like: boolean) => void; onViewShortlist: (recommendations: MealRecommendation[]) => void; onSubmit: (text: string) => void; onStop: () => void; busy: boolean; modelLabel: string; onConnection: () => void; }) {
  return <section className="chat-workspace" aria-label="Your meal conversation"><div className="chat-scroll"><div className="chat-messages">{messages.map((message) => <article className={`chat-message ${message.role === "user" ? "is-user" : "is-assistant"}`} key={message.id}>{message.role === "assistant" && <div className="assistant-avatar" aria-hidden="true"><MessageCircle size={16} /></div>}<div className="message-body">{message.role === "assistant" && <span className="message-author">Remymy · {message.source ?? "Demo"}</span>}{message.role === "assistant" && message.stages && message.stages.length > 0 && <AgentStages stages={message.stages} pending={message.status === "pending"} />}{message.status === "pending" ? !message.stages?.length && <span className="chat-pending" aria-label="Remymy is thinking"><LoaderCircle className="spin" size={16} /> Starting the agent…</span> : <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div> }}>{message.text}</ReactMarkdown>}{message.status === "error" && message.id === failedAssistantId && retryAvailable && <button className="chat-retry" onClick={onRetry}><RotateCcw size={14} /> Try again</button>}{message.recommendations && message.recommendations.length > 0 && <div className="chat-meal-list" aria-label="Meal suggestions">{message.recommendations.slice(0, 6).map((recommendation) => <div className="chat-meal-row" key={recommendation.meal.id}><button className="chat-meal-open" onClick={() => onOpenMeal(recommendation.meal)}><span className="chat-meal-thumb" style={{ background: recommendation.meal.color }} aria-hidden="true"><Utensils size={15} /></span><span className="chat-meal-info"><strong>{recommendation.meal.title}</strong><small>{recommendation.meal.minutes} min · {recommendation.meal.cuisine}</small></span><ArrowRight size={15} aria-hidden="true" /></button><RecipeLikeButtons title={recommendation.meal.title} rating={{ like: mealLikes[recommendation.meal.id] }} onRate={(like) => onRateMeal(recommendation.meal, like)} /></div>)}<button className="shortlist-link" onClick={() => onViewShortlist(message.recommendations ?? [])}>View shortlist <ArrowRight size={14} /></button></div>}</div></article>)}</div></div><div className="chat-composer-dock"><Composer onSubmit={onSubmit} onStop={onStop} busy={busy} welcome={false} modelLabel={modelLabel} onConnection={onConnection} /></div></section>;
}

function AgentStages({ stages, pending }: { stages: AgentStage[]; pending: boolean }) {
  return <div className={`agent-stages ${pending ? "is-running" : ""}`} aria-label="Agent progress" aria-live="polite">
    <div className="agent-stages-heading"><span>Agent flow</span>{pending && <span className="agent-live"><span /> Live</span>}</div>
    <ol>{stages.map((stage) => <li key={stage.id} className={`agent-stage is-${stage.status}`}>
      <span className="agent-stage-marker" aria-hidden="true">{stage.status === "complete" ? <Check size={12} /> : stage.status === "active" ? <LoaderCircle className="spin" size={13} /> : <Circle size={10} />}</span>
      <span><strong>{stage.label}</strong><small>{stage.detail}</small></span>
    </li>)}</ol>
  </div>;
}

function MealsView({ recommendations, preferences, isShortlist, filter, onFilterChange, onSave, savedIds, onOpen, mealLikes, onRateMeal, onPreferences, onBrowseAll }: { recommendations: MealRecommendation[]; preferences: Preferences; isShortlist: boolean; filter: (typeof FILTERS)[number]["id"]; onFilterChange: (filter: (typeof FILTERS)[number]["id"]) => void; onSave: (meal: Meal) => void; savedIds: string[]; onOpen: (meal: Meal) => void; mealLikes: Record<string, boolean>; onRateMeal: (meal: Meal, like: boolean) => void; onPreferences: () => void; onBrowseAll: () => void; }) {
  return <section className="browse-view"><div className="view-heading"><p className="eyebrow">A LITTLE INSPIRATION</p><h1>{isShortlist ? "Your shortlist." : "Meals for the mood."}</h1><p>{isShortlist ? "The ideas from your conversation, ready for a closer look." : "Browse the small, cheerful catalog and find something that fits today."}</p></div><div className="content-toolbar"><div><p className="eyebrow">TUNED TO YOUR DEFAULTS</p><h2>{recommendations.length ? `${recommendations.length} good ideas` : "Nothing matches yet"}</h2><div className="constraint-summary" aria-label="Active meal preferences"><span>Up to {preferences.maxMinutes} min</span>{preferences.diet !== "any" && <span>{preferences.diet}</span>}{preferences.goal !== "balanced" && <span>{preferences.goal} goal</span>}{preferences.cuisine !== "any" && <span>{preferences.cuisine}</span>}{preferences.exclude.map((item) => <span key={item}>No {item}</span>)}<button className="constraint-edit" onClick={onPreferences}>Edit preferences</button></div></div><div className="filter-chips" role="group" aria-label="Filter meals">{FILTERS.map((item) => <button key={item.id} className={`filter-chip ${filter === item.id && !isShortlist ? "is-active" : ""}`} onClick={() => onFilterChange(item.id)} aria-pressed={filter === item.id && !isShortlist}>{item.label}</button>)}</div></div>{isShortlist && <button className="text-button" onClick={onBrowseAll}>Browse all meals <ArrowRight size={14} /></button>}{recommendations.length ? <div className="shortlist-grid">{recommendations.map((recommendation) => <MealCard key={recommendation.meal.id} recommendation={recommendation} saved={savedIds.includes(recommendation.meal.id)} onSave={onSave} onOpen={onOpen} like={mealLikes[recommendation.meal.id]} onRate={onRateMeal} />)}</div> : <div className="empty-state"><div className="empty-state-icon"><Utensils size={22} /></div><h3>Let&apos;s widen the plate.</h3><p>No meals in this demo match all your preferences. Adjust the time or ingredients to see more ideas.</p><div className="empty-actions"><button className="detail-action" onClick={onPreferences}>Edit preferences <ArrowRight size={17} /></button></div></div>}</section>;
}

function RecipesView({ page, search, busy, error, pageNumber, onSearch, onRetry, onPageChange, onOpen }: { page: RecipePage | null; search: string; busy: boolean; error: string; pageNumber: number; onSearch: (value: string) => void; onRetry: () => void; onPageChange: (value: number) => void; onOpen: (recipe: RecipeSummary) => void; }) {
  const pageSize = page?.limit ?? 24;
  const totalPages = page ? Math.max(1, Math.ceil(page.total / pageSize)) : 1;
  return <section className="browse-view">
    <div className="view-heading"><p className="eyebrow">SOURCE-BACKED CATALOG</p><h1>Recipes for the mood.</h1><p>Browse the imported Open Recipes records. Only fields available in the source are shown.</p></div>
    <div className="recipe-search-row"><label htmlFor="recipe-search">Search recipes</label><input id="recipe-search" value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Try soup, pasta, chicken…" /><span>{page ? `${page.total.toLocaleString()} recipes` : ""}</span></div>
    {error ? <div className="empty-state recipe-state" role="alert"><h3>Recipes unavailable.</h3><p>{error}</p><button className="detail-action" onClick={onRetry}>Try again <ArrowRight size={17} /></button></div> : busy && !page ? <div className="empty-state recipe-state" role="status"><LoaderCircle className="spin" size={22} /><h3>Loading recipes…</h3><p>Fetching source-backed records.</p></div> : page && page.items.length > 0 ? <>
      <div className="shortlist-grid">{page.items.map((recipe) => <RecipeCard key={recipe.id} recipe={recipe} onOpen={onOpen} />)}</div>
      <nav className="recipe-pagination" aria-label="Recipe pagination"><button onClick={() => onPageChange(Math.max(0, pageNumber - 1))} disabled={pageNumber === 0 || busy}>Previous</button><span>Page {pageNumber + 1} of {totalPages}</span><button onClick={() => onPageChange(Math.min(totalPages - 1, pageNumber + 1))} disabled={pageNumber >= totalPages - 1 || busy}>Next</button></nav>
    </> : <div className="empty-state recipe-state"><div className="empty-state-icon"><Utensils size={22} /></div><h3>No recipes found.</h3><p>Try a different title or ingredient search.</p></div>}
  </section>;
}

function SavedView({ recommendations, onSave, onOpen, mealLikes, onRateMeal, foods, foodRatings, onRateFood, onToggleFood, onOpenFood, onDiscover }: { recommendations: MealRecommendation[]; onSave: (meal: Meal) => void; onOpen: (meal: Meal) => void; mealLikes: Record<string, boolean>; onRateMeal: (meal: Meal, like: boolean) => void; foods: SavedFood[]; foodRatings: Record<string, RecipeRatingState>; onRateFood: (id: string, like: boolean) => void; onToggleFood: (food: SavedFood) => void; onOpenFood: (id: string) => void; onDiscover: () => void; }) {
  const empty = recommendations.length === 0 && foods.length === 0;
  return <section className="saved-view"><div className="view-heading"><p className="eyebrow">YOUR LITTLE ARCHIVE</p><h1>Saved for later.</h1><p>The recipes that made you pause. They&apos;ll be here when you&apos;re ready.</p></div>
    {!empty && <div className="shortlist-grid">
      {recommendations.map((recommendation) => <MealCard key={recommendation.meal.id} recommendation={recommendation} saved onSave={onSave} onOpen={onOpen} like={mealLikes[recommendation.meal.id]} onRate={onRateMeal} showImage={false} />)}
      {foods.map((food) => <article className="meal-card recipe-card saved-recipe-card" key={food.id}><div className="meal-card-body"><div className="meal-card-meta"><span><ChefHat size={14} aria-hidden="true" /> Picked for you</span></div><button className="meal-card-title" onClick={() => onOpenFood(food.id)}>{food.title}</button><div className="meal-card-footer"><span className="rank-note"><Sparkles size={14} aria-hidden="true" /> Saved by you</span><div className="meal-card-actions"><RecipeLikeButtons title={food.title} rating={foodRatings[food.id]} onRate={(like) => onRateFood(food.id, like)} /><button className="save-button is-saved" onClick={() => onToggleFood(food)} aria-pressed aria-label={`Remove ${food.title} from saved recipes`} title="Saved"><Bookmark size={17} fill="currentColor" aria-hidden="true" /></button></div></div></div></article>)}
    </div>}
    {empty && <div className="empty-state"><div className="empty-state-icon"><Bookmark size={22} /></div><h3>Nothing saved yet.</h3><p>When a recipe looks good, tap the bookmark and it&apos;ll land here.</p><button className="detail-action" onClick={onDiscover}>Find something good <ArrowRight size={17} /></button></div>}
  </section>;
}

function HistoryView({ items, profile, error, onRetry, onOpenRecipe, onOpenFood }: { items: HistoryItem[] | null; profile: BuyingProfile | null; error: string; onRetry: () => void; onOpenRecipe: (recipe: { id: string }) => void; onOpenFood: (id: string) => void; }) {
  return <section className="saved-view history-view"><div className="view-heading"><p className="eyebrow">WHAT YOU&apos;VE RATED</p><h1>Your history.</h1><p>Every recipe you liked or didn&apos;t, newest first, as the database holds it.</p></div>
    {profile && items && items.length > 0 && <div className="history-profile"><div><strong>What your ratings add up to</strong><small>Averages of the priced ingredients in the recipes you rated, nutrients per 100 g</small></div><div className="for-you-profile"><span>{formatPrice(profile.avg_price)} avg. ingredient</span>{nutrientHighlights(profile.avg_nutrient_values).map((highlight) => <span key={highlight}>{highlight}</span>)}</div></div>}
    {error ? <div className="empty-state recipe-state" role="alert"><h3>History unavailable.</h3><p>{error}</p><button className="detail-action" onClick={onRetry}>Try again <ArrowRight size={17} /></button></div>
      : items === null ? <div className="empty-state recipe-state" role="status"><LoaderCircle className="spin" size={22} /><h3>Loading your history…</h3></div>
        : items.length === 0 ? <div className="empty-state"><div className="empty-state-icon"><HistoryIcon size={22} /></div><h3>Nothing rated yet.</h3><p>Tap the tick or the cross on a recipe and it&apos;ll show up here.</p></div>
          : <ol className="history-list">{items.map((item, index) => <li className="history-item" key={`${item.recipe_id}-${index}`}>
            <span className="history-icon" aria-hidden="true">{item.kind === "food" ? <ChefHat size={16} /> : <Utensils size={16} />}</span>
            <span className="history-info">{item.title ? <button className="history-title" onClick={() => item.kind === "food" ? onOpenFood(item.recipe_id) : onOpenRecipe({ id: item.recipe_id })}>{item.title}</button> : <strong className="history-title">{item.recipe_id}</strong>}<small>{item.kind === "food" ? "Picked for you" : "Recipe"} · {formatHistoryDate(item.timestamp)}</small></span>
            <span className={`history-like ${item.like ? "is-like" : "is-dislike"}`}>{item.like ? <Check size={13} aria-hidden="true" /> : <X size={13} aria-hidden="true" />}{item.like ? "Liked" : "Not liked"}</span>
          </li>)}</ol>}
  </section>;
}

