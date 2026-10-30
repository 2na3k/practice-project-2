"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import { MealDetailModal } from "@/components/MealDetailModal";
import { PreferencePanel } from "@/components/PreferencePanel";
import { Sidebar, type AppView } from "@/components/Sidebar";
import { UserAvatar } from "@/components/UserAvatar";
import { GUEST_PROFILE } from "@/lib/guest-profile";
import { ConnectionPanel } from "@/components/ConnectionPanel";
import { RecipeCard } from "@/components/RecipeCard";
import { RecipeDetailModal } from "@/components/RecipeDetailModal";
import { CatalogItemModal } from "@/components/CatalogItemModal";
import { AddRecipePanel } from "@/components/AddRecipePanel";
import { MyRecipesView } from "@/components/MyRecipesView";
import { RecommendationPanel } from "@/components/RecommendationPanel";
import { RecipeLikeButtons, type RecipeRatingState } from "@/components/RecipeLikeButtons";
import { DEFAULT_PREFERENCES, interpretRequest, type Preferences } from "@/lib/preferences";
import { normalizeAllergies, validAllergies } from "@/lib/allergens";
import { normalizeBudget, validBudget } from "@/lib/budget";
import type {
  AgentStage,
  ChatResult,
  ChatStreamEvent,
  ChatTurn,
  ConnectionStatus,
  Meal,
  MealRecommendation,
} from "@/lib/chat-types";
import { fetchRecipeDetail, fetchRecipeLikes, fetchRecipePage, saveRecipeRating, type RecipeDetail, type RecipePage, type RecipeSummary } from "@/lib/recipes";
import { fetchRecommendations, isFoodId, nutrientHighlights, type BuyingProfile } from "@/lib/recommendations";
import { fetchSaved, parseSavedFoods, saveItem, unsaveItem, type SavedFood } from "@/lib/saved";
import { logImpression } from "@/lib/impressions";
import { saveUserPreferences } from "@/lib/user-preferences";
import { fetchHistory, formatHistoryDate, type HistoryItem } from "@/lib/history";
import { fetchCatalogItem, formatPrice, type CatalogItem } from "@/lib/catalog";

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
    // preferences saved before there was a budget, or allergies, have none
    validBudget(candidate.budget) &&
    validAllergies(candidate.allergies)
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
    (result.toolsUsed === undefined || (Array.isArray(result.toolsUsed) && result.toolsUsed.every((tool) => typeof tool === "string"))) &&
    (result.trace_id === undefined || result.trace_id === null || typeof result.trace_id === "string");
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
  // the preferences the chat carries from one message to the next, and what Preferences saves for the recommendations
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  // this browser's guest user, whose history, likes, saved recipes, preferences and own recipes the Data API keeps;
  // null until /api/guest answers
  const [guest, setGuest] = useState<string | null>(null);
  const [savedFoods, setSavedFoods] = useState<SavedFood[]>([]);
  // like or not of each recipe, by its id, as the Data API's users database holds it
  const [foodRatings, setFoodRatings] = useState<Record<string, RecipeRatingState>>({});
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [connection, setConnection] = useState<ConnectionStatus>({ connected: false });
  const [connectionLoadError, setConnectionLoadError] = useState("");
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
  // the guest's history, read again from the Data API each time the History page opens; null until first read
  const [history, setHistory] = useState<HistoryItem[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [historyNonce, setHistoryNonce] = useState(0);
  // averages of the priced ingredients in the rated recipes, which the recommend route works out from the history
  const [buyingProfile, setBuyingProfile] = useState<BuyingProfile | null>(null);
  const [notice, setNotice] = useState("");
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
    if (view !== "meals") return;
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
  }, [recipePageNumber, recipeRetryNonce, recipeSearch, view]);

  useEffect(() => {
    if (view !== "history" || !guest) return;
    const controller = new AbortController();
    void Promise.resolve().then(() => {
      if (controller.signal.aborted) return null;
      setHistoryError("");
      return fetchHistory(guest, controller.signal);
    })
      .then((items) => { if (items && mountedRef.current) setHistory(items); })
      .catch((error) => { if (!controller.signal.aborted && mountedRef.current) setHistoryError(error instanceof Error ? error.message : "Your history could not be loaded."); });
    // the profile is extra: if it cannot be read, the History page goes on without it
    void fetchRecommendations(guest, 1, controller.signal)
      .then((result) => { if (mountedRef.current) setBuyingProfile(result.profile); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [guest, historyNonce, view]);

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
    try {
      const preferencesValue = window.localStorage.getItem("remymy-preferences");
      if (preferencesValue) {
        const parsed: unknown = JSON.parse(preferencesValue);
        // a budget saved as null, or none at all, is stored as 0, no limit
        if (validPreferences(parsed)) {
          storedPreferences = { ...parsed, budget: normalizeBudget(parsed.budget), allergies: normalizeAllergies(parsed.allergies) };
        }
      }
    } catch {
      // Browser storage is optional and can be disabled by an embed or privacy mode.
    }
    const frame = window.requestAnimationFrame(() => {
      if (!mountedRef.current) return;
      if (storedPreferences) setPreferences(storedPreferences);
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

  // This browser's guest user: the Data API keeps its history, likes, saved recipes, preferences and own recipes.
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/guest", { method: "GET", cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const data: unknown = await response.json().catch(() => null);
        if (!response.ok) throw new Error(errorMessage(data, "The guest user could not be opened."));
        const username = data && typeof data === "object" && "username" in data ? (data as { username?: unknown }).username : undefined;
        if (typeof username !== "string" || !username) throw new Error("The guest user returned an unexpected response.");
        if (mountedRef.current) setGuest(username);
      })
      .catch(() => {
        // without a guest the Data API's history and likes stay empty until the next load
      });
    return () => controller.abort();
  }, []);

  // The saved recipes live in the Data API's database. Those this browser kept from before go up to it once, and then
  // leave the browser; one the database does not know is dropped.
  useEffect(() => {
    if (!isHydrated || !guest) return;
    const controller = new AbortController();
    let kept: SavedFood[] = [];
    try { kept = parseSavedFoods(JSON.parse(window.localStorage.getItem("remymy-saved-foods") ?? "[]")); } catch { /* optional */ }
    void fetchSaved(guest, controller.signal)
      .then(async (saved) => {
        const missing = kept.filter((food) => !saved.some((item) => item.id === food.id));
        const results = await Promise.allSettled(missing.map((food) => saveItem(guest, food.id, controller.signal)));
        const added = results.flatMap((result) => result.status === "fulfilled" && result.value ? [result.value] : []);
        // a save that failed keeps the browser's copy for the next load
        if (results.every((result) => result.status === "fulfilled")) {
          try { window.localStorage.removeItem("remymy-saved-foods"); } catch { /* best effort */ }
        }
        if (!mountedRef.current || controller.signal.aborted) return;
        const loaded = [...added, ...saved];
        // merged over anything saved before the list came back
        setSavedFoods((current) => [...current.filter((food) => !loaded.some((item) => item.id === food.id)), ...loaded]);
      })
      // without the Data API, Saved shows none of them until the next load
      .catch(() => {});
    return () => controller.abort();
  }, [guest, isHydrated]);

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
  // an open recipe shows its like too, from whichever view it was opened
  const showsRecipeLikes = view === "saved" || (view === "discover" && chat.length === 0) || activeRecipe !== null;
  useEffect(() => {
    if (!showsRecipeLikes || !guest) return;
    const controller = new AbortController();
    void fetchRecipeLikes(guest, controller.signal)
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
  }, [guest, showsRecipeLikes]);

  const flashNotice = useCallback((text: string) => {
    setNotice(text);
    window.setTimeout(() => { if (mountedRef.current) setNotice(""); }, 2600);
  }, []);

  // Saved at once on screen, and in the Data API's database behind it; one it could not save goes back as it was.
  const toggleSavedFood = useCallback((food: SavedFood) => {
    if (!guest) {
      flashNotice("Your saved recipes are not open yet. Try again in a moment.");
      return;
    }
    const isSaved = savedFoods.some((item) => item.id === food.id);
    const others = (current: SavedFood[]) => current.filter((item) => item.id !== food.id);
    setSavedFoods((current) => isSaved ? others(current) : [food, ...others(current)]);
    const request = isSaved
      ? unsaveItem(guest, food.id)
      : saveItem(guest, food.id).then((saved) => {
        if (!saved) throw new Error(`${food.title} could not be saved.`);
      });
    void request.catch((error) => {
      if (!mountedRef.current) return;
      setSavedFoods((current) => isSaved ? [food, ...others(current)] : others(current));
      flashNotice(error instanceof Error ? error.message : "Your saved recipes could not be updated.");
    });
    flashNotice(isSaved ? `${food.title} removed from saved recipes` : `${food.title} saved for later`);
  }, [flashNotice, guest, savedFoods]);

  // A recipe or a catalog item, by its id: the Data API's rating route takes it as the recipe_id.
  const rateFood = useCallback((id: string, like: boolean) => {
    const update = (next: RecipeRatingState) => setFoodRatings((current) => ({ ...current, [id]: { ...current[id], ...next } }));
    if (!guest) {
      update({ saving: false, error: "Your rating could not be saved yet. Try again in a moment." });
      return;
    }
    update({ saving: true, error: undefined });
    void saveRecipeRating(id, guest, like)
      .then((saved) => { if (mountedRef.current) update({ like: saved.like, saving: false }); })
      .catch((error) => { if (mountedRef.current) update({ saving: false, error: error instanceof Error ? error.message : "Your rating could not be saved." }); });
  }, [guest]);

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
    if (!connection.connected) {
      flashNotice("Connect an AI provider to start chatting");
      setView("connection");
      return;
    }
    const retryRequest = retry ? failedRequest : null;
    if (retry && !retryRequest) return;
    const assistantId = retryRequest?.assistantId ?? makeId("assistant");
    const currentHistory = retryRequest?.history ?? boundedHistory([...chat, { id: makeId("user"), role: "user", text }]);
    const requestPreferences = retryRequest?.preferences ?? preferences;
    const userMessage: ChatMessage = { id: makeId("user"), role: "user", text };
    const responseSource = `${connection.provider ?? "AI"} · ${connection.model ?? "connected"}`;
    setChat((current) => retryRequest ? current.map((message) => message.id === assistantId ? { ...message, text: "", status: "pending", source: responseSource } : message) : [...current, userMessage, { id: assistantId, role: "assistant", text: "", status: "pending", source: responseSource }]);
    setFailedRequest(null);
    setIsBusy(true);
    pendingAssistantRef.current = assistantId;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
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
      const result = await readChatStream(response, (stage) => {
        if (!mountedRef.current) return;
        setChat((current) => current.map((message) => message.id === assistantId
          ? { ...message, stages: upsertStage(message.stages, stage) }
          : message));
      });
      if (controller.signal.aborted || !mountedRef.current) return;
      setPreferences(interpretRequest(text, result.preferences));
      setChat((current) => current.map((message) => message.id === assistantId ? {
        ...message,
        text: result.text,
        status: undefined,
        source: result.toolsUsed?.includes("say_hello") || result.toolsUsed?.includes("recommend_meals")
          ? `${responseSource} · MCP`
          : responseSource,
        recommendations: result.recommendations,
      } : message));
      if (guest && result.recommendations.length > 0) {
        logImpression(guest, result.trace_id ?? null, result.recommendations.map((item) => item.meal.id));
      }
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
  }, [chat, connection.connected, connection.model, connection.provider, failedRequest, flashNotice, isBusy, preferences]);

  const startNewChat = useCallback(() => {
    stopRequest();
    setChat([]);
    setFailedRequest(null);
    setView("discover");
  }, [stopRequest]);
  const savePreferences = useCallback((next: Preferences) => {
    stopRequest();
    setPreferences(next);
    // kept in this browser either way; the Data API's copy, the guest's, is what its recommendations use
    if (!guest) {
      flashNotice("Preferences kept in this browser; they reach your recommendations once your guest profile opens.");
      return;
    }
    void saveUserPreferences(guest, next)
      .then(() => { if (mountedRef.current) flashNotice("Preferences updated"); })
      .catch((error) => {
        if (mountedRef.current) flashNotice(error instanceof Error ? error.message : "Your preferences could not be saved.");
      });
  }, [flashNotice, guest, stopRequest]);
  const modelLabel = connection.connected ? `${connection.provider ?? "AI"} · ${connection.model ?? "connected"}` : "Not connected";
  const hasChat = chat.length > 0;
  const guestPending = <div className="empty-state recipe-state" role="status"><LoaderCircle className="spin" size={22} /><h3>Opening your guest profile…</h3></div>;

  return (
    <div className="app-shell chat-shell">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <Sidebar view={view} onViewChange={setView} onNewChat={startNewChat} connection={connection} />
      <main className={`app-main workspace-main ${view === "discover" ? (hasChat ? "is-chat" : "is-welcome") : ""}`} id="main-content">
        {view === "discover" ? (
          <>
            <header className="workspace-header">
              <div><p className="eyebrow">YOUR DAILY DOSE OF DELICIOUS</p><h1 className="workspace-title">{hasChat ? "Your food conversation" : "What sounds good?"}</h1></div>
              <div className="workspace-actions"><button className="model-status-button" onClick={() => setView("connection")}><span className={`connection-dot ${connection.connected ? "connected" : ""}`} />{modelLabel}</button>{connectionLoadError && <span className="connection-load-note">Status unavailable</span>}{hasChat && <button className="header-new-chat" onClick={startNewChat}><Plus className="header-new-chat-icon" size={15} aria-hidden="true" />New chat</button>}<button className="header-avatar" onClick={() => setView("preferences")} aria-label={`Signed in as ${GUEST_PROFILE.name}, open your preferences`} title={GUEST_PROFILE.name}><UserAvatar size={34} /></button></div>
            </header>
            {hasChat ? <ChatWorkspace messages={chat} failedAssistantId={failedRequest?.assistantId} onRetry={() => void submitMessage(failedRequest?.text ?? "", true)} retryAvailable={Boolean(failedRequest) && !isBusy} onOpenMeal={setActiveMeal} onSubmit={(text) => void submitMessage(text)} onStop={stopRequest} busy={isBusy} modelLabel={modelLabel} onConnection={() => setView("connection")} /> : (
              <section className="welcome-screen">
                {guest && <RecommendationPanel username={guest} onItemCount={setPickCount} ratings={foodRatings} onRate={rateFood} savedIds={savedFoods.map((food) => food.id)} onSave={toggleSavedFood} onOpen={(id) => isFoodId(id) ? openCatalogItem(id) : openRecipe({ id })} />}
                {pickCount === 0 && <div className="hero-grid"><div className="hero-copy"><p className="eyebrow">THE KIND OF HELPFUL YOU CAN EAT</p><h2 className="hero-title">Less overthinking.<br /><em>More good food.</em></h2><p className="hero-description">A tiny nudge toward something delicious, tuned to your cravings, your time, and the ingredients you love.</p></div><div className="hero-art" aria-label="Illustration of a colorful bowl of food" role="img"><div className="hero-sun" /><div className="hero-plate"><span className="hero-food hero-food-one" /><span className="hero-food hero-food-two" /><span className="hero-food hero-food-three" /><span className="hero-food hero-food-four" /><span className="hero-leaf" /></div><span className="hero-art-label">GOOD THINGS<br /><strong>COMING UP</strong></span></div></div>}
                <Composer onSubmit={(text) => void submitMessage(text)} onStop={stopRequest} busy={isBusy} welcome modelLabel={modelLabel} onConnection={() => setView("connection")} />
                <p className="welcome-hint">Try a craving, a time limit, or an ingredient you want to use up.</p>
              </section>
            )}
          </>
        ) : view === "meals" ? <RecipesView page={recipePage} search={recipeSearch} busy={recipeBusy} error={recipeLoadError} pageNumber={recipePageNumber} onSearch={(next) => { setRecipeSearch(next); setRecipePageNumber(0); }} onRetry={() => setRecipeRetryNonce((current) => current + 1)} onPageChange={setRecipePageNumber} onOpen={openRecipe} />
          : view === "saved" ? <SavedView foods={savedFoods} foodRatings={foodRatings} onRateFood={rateFood} onToggleFood={toggleSavedFood} onOpenFood={openCatalogItem} onOpenRecipe={openRecipe} onDiscover={() => setView("discover")} />
            : view === "add-recipe" ? guest ? <AddRecipePanel username={guest} onSaved={(recipe) => flashNotice(`${recipe.title} added to your recipes`)} /> : guestPending
            : view === "my-recipes" ? guest ? <MyRecipesView username={guest} onOpen={openRecipe} onAdd={() => setView("add-recipe")} /> : guestPending
            : view === "history" ? <HistoryView items={history} profile={buyingProfile} error={historyError} onRetry={() => setHistoryNonce((current) => current + 1)} onOpenRecipe={openRecipe} onOpenFood={openCatalogItem} />
            : view === "preferences" ? <PreferencePanel preferences={preferences} onSave={savePreferences} />
              : <ConnectionPanel connection={connection} onConnectionChange={setConnection} onBackToChat={() => setView("discover")} />}
      </main>
      {notice && <div className="notice" role="status"><Bookmark size={16} aria-hidden="true" />{notice}</div>}
      <MealDetailModal meal={activeMeal} onClose={closeMeal} />
      <RecipeDetailModal recipe={activeRecipe} loading={recipeDetailBusy} error={recipeDetailError} onClose={closeRecipe} rating={activeRecipe ? foodRatings[activeRecipe.id] : undefined} onRate={rateFood} saved={activeRecipe ? savedFoods.some((food) => food.id === activeRecipe.id) : false} onSave={(recipe) => toggleSavedFood({ id: recipe.id, title: recipe.title, kind: "recipe" })} onOpenFood={(id) => { closeRecipe(); openCatalogItem(id); }} />
      <CatalogItemModal item={activeCatalogItem} loading={catalogBusy} error={catalogError} onClose={closeCatalogItem} rating={activeCatalogItem ? foodRatings[String(activeCatalogItem.fdc_id)] : undefined} onRate={rateFood} saved={activeCatalogItem ? savedFoods.some((food) => food.id === String(activeCatalogItem.fdc_id)) : false} onSave={(item) => toggleSavedFood({ id: String(item.fdc_id), title: item.description })} onOpenFood={openCatalogItem} />
    </div>
  );
}

function ChatWorkspace({ messages, failedAssistantId, onRetry, retryAvailable, onOpenMeal, onSubmit, onStop, busy, modelLabel, onConnection }: { messages: ChatMessage[]; failedAssistantId?: string; onRetry: () => void; retryAvailable: boolean; onOpenMeal: (meal: Meal) => void; onSubmit: (text: string) => void; onStop: () => void; busy: boolean; modelLabel: string; onConnection: () => void; }) {
  return <section className="chat-workspace" aria-label="Your meal conversation"><div className="chat-scroll"><div className="chat-messages">{messages.map((message) => <article className={`chat-message ${message.role === "user" ? "is-user" : "is-assistant"}`} key={message.id}>{message.role === "assistant" && <div className="assistant-avatar" aria-hidden="true"><MessageCircle size={16} /></div>}<div className="message-body">{message.role === "assistant" && <span className="message-author">Remymy · {message.source ?? "AI"}</span>}{message.role === "assistant" && message.stages && message.stages.length > 0 && <AgentStages stages={message.stages} pending={message.status === "pending"} />}{message.status === "pending" ? !message.stages?.length && <span className="chat-pending" aria-label="Remymy is thinking"><LoaderCircle className="spin" size={16} /> Starting the agent…</span> : <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div> }}>{message.text}</ReactMarkdown>}{message.status === "error" && message.id === failedAssistantId && retryAvailable && <button className="chat-retry" onClick={onRetry}><RotateCcw size={14} /> Try again</button>}{message.recommendations && message.recommendations.length > 0 && <div className="chat-meal-list" aria-label="Meal suggestions">{message.recommendations.slice(0, 6).map((recommendation) => <div className="chat-meal-row" key={recommendation.meal.id}><button className="chat-meal-open" onClick={() => onOpenMeal(recommendation.meal)}><span className="chat-meal-thumb" style={{ background: recommendation.meal.color }} aria-hidden="true"><Utensils size={15} /></span><span className="chat-meal-info"><strong>{recommendation.meal.title}</strong><small>{recommendation.meal.minutes} min · {recommendation.meal.cuisine}</small></span><ArrowRight size={15} aria-hidden="true" /></button></div>)}</div>}</div></article>)}</div></div><div className="chat-composer-dock"><Composer onSubmit={onSubmit} onStop={onStop} busy={busy} welcome={false} modelLabel={modelLabel} onConnection={onConnection} /></div></section>;
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

function SavedView({ foods, foodRatings, onRateFood, onToggleFood, onOpenFood, onOpenRecipe, onDiscover }: { foods: SavedFood[]; foodRatings: Record<string, RecipeRatingState>; onRateFood: (id: string, like: boolean) => void; onToggleFood: (food: SavedFood) => void; onOpenFood: (id: string) => void; onOpenRecipe: (recipe: { id: string }) => void; onDiscover: () => void; }) {
  const empty = foods.length === 0;
  return <section className="saved-view"><div className="view-heading"><p className="eyebrow">YOUR LITTLE ARCHIVE</p><h1>Saved for later.</h1><p>The recipes that made you pause. They&apos;ll be here when you&apos;re ready.</p></div>
    {!empty && <div className="shortlist-grid">
      {foods.map((food) => <article className="meal-card recipe-card saved-recipe-card" key={food.id}><div className="meal-card-body"><div className="meal-card-meta">{food.kind === "recipe" || food.kind === "user" ? <span><Utensils size={14} aria-hidden="true" /> Recipe</span> : <span><ChefHat size={14} aria-hidden="true" /> Picked for you</span>}</div><button className="meal-card-title" onClick={() => food.kind === "recipe" || food.kind === "user" ? onOpenRecipe({ id: food.id }) : onOpenFood(food.id)}>{food.title}</button><div className="meal-card-footer"><span className="rank-note"><Sparkles size={14} aria-hidden="true" /> Saved by you</span><div className="meal-card-actions"><RecipeLikeButtons title={food.title} rating={foodRatings[food.id]} onRate={(like) => onRateFood(food.id, like)} /><button className="save-button is-saved" onClick={() => onToggleFood(food)} aria-pressed aria-label={`Remove ${food.title} from saved recipes`} title="Saved"><Bookmark size={17} fill="currentColor" aria-hidden="true" /></button></div></div></div></article>)}
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

