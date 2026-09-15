"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowRight,
  Bookmark,
  LoaderCircle,
  MessageCircle,
  Plus,
  RotateCcw,
  Utensils,
} from "lucide-react";
import { Composer } from "@/components/Composer";
import { MealCard } from "@/components/MealCard";
import type { MealRecommendation } from "@/components/MealCard";
import { MealDetailModal } from "@/components/MealDetailModal";
import { PreferencePanel } from "@/components/PreferencePanel";
import { Sidebar, type AppView } from "@/components/Sidebar";
import { ConnectionPanel } from "@/components/ConnectionPanel";
import {
  DEFAULT_PREFERENCES,
  MEALS,
  type Meal,
  type Preferences,
} from "@/lib/meals";
import { demoReply } from "@/lib/demo-chat";
import { recommendMeals } from "@/lib/recommender";
import type {
  ChatResult,
  ChatTurn,
  ConnectionStatus,
} from "@/lib/chat-types";

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
    (candidate.diet === "any" || candidate.diet === "vegetarian" || candidate.diet === "vegan") &&
    typeof candidate.maxMinutes === "number" && Number.isFinite(candidate.maxMinutes) &&
    candidate.maxMinutes >= 1 && candidate.maxMinutes <= 90 &&
    Array.isArray(candidate.exclude) && candidate.exclude.every((item) => typeof item === "string") &&
    (candidate.goal === "balanced" || candidate.goal === "protein" || candidate.goal === "light") &&
    typeof candidate.cuisine === "string"
  );
}

function validRecommendations(value: unknown): value is MealRecommendation[] {
  return Array.isArray(value) && value.every((item) => {
    if (!item || typeof item !== "object") return false;
    const recommendation = item as Partial<MealRecommendation>;
    const meal = recommendation.meal;
    return Boolean(
      meal && typeof meal === "object" && typeof meal.id === "string" &&
      MEALS.some((candidate) => candidate.id === meal.id) &&
      typeof recommendation.score === "number" && Array.isArray(recommendation.reasons) &&
      recommendation.reasons.every((reason) => typeof reason === "string"),
    );
  });
}

function validChatResult(value: unknown): value is ChatResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ChatResult>;
  return typeof result.text === "string" && validPreferences(result.preferences) && validRecommendations(result.recommendations);
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
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [connection, setConnection] = useState<ConnectionStatus>({ connected: false });
  const [connectionLoadError, setConnectionLoadError] = useState("");
  const [mealShortlist, setMealShortlist] = useState<MealRecommendation[] | null>(null);
  const [activeMeal, setActiveMeal] = useState<Meal | null>(null);
  const [notice, setNotice] = useState("");
  const [isHydrated, setIsHydrated] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [failedRequest, setFailedRequest] = useState<FailedRequest | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const pendingAssistantRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const closeMeal = useCallback(() => setActiveMeal(null), []);

  useEffect(() => {
    mountedRef.current = true;
    let storedPreferences: Preferences | null = null;
    let storedSaved: string[] | null = null;
    try {
      const preferencesValue = window.localStorage.getItem("remymy-preferences");
      const savedValue = window.localStorage.getItem("remymy-saved-meals");
      if (preferencesValue) {
        const parsed: unknown = JSON.parse(preferencesValue);
        if (validPreferences(parsed)) storedPreferences = parsed;
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
      setIsHydrated(true);
    });
    return () => {
      mountedRef.current = false;
      window.cancelAnimationFrame(frame);
      requestRef.current?.abort();
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

  const toggleSaved = useCallback((meal: Meal) => {
    const isSaved = savedIds.includes(meal.id);
    setSavedIds((current) => current.includes(meal.id) ? current.filter((id) => id !== meal.id) : [...current, meal.id]);
    setNotice(isSaved ? `${meal.title} removed from saved meals` : `${meal.title} saved for later`);
    window.setTimeout(() => { if (mountedRef.current) setNotice(""); }, 2600);
  }, [savedIds]);

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
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: currentHistory, preferences: requestPreferences }),
          signal: controller.signal,
        });
        const data: unknown = await response.json().catch(() => null);
        if (!response.ok) throw new Error(errorMessage(data, "Your connected model could not answer."));
        if (!validChatResult(data)) throw new Error("The connected model returned an unexpected response.");
        result = data;
      } else {
        result = await Promise.resolve(demoReply(text, requestPreferences, chat.length));
      }
      if (controller.signal.aborted || !mountedRef.current) return;
      setPreferences(result.preferences);
      setChat((current) => current.map((message) => message.id === assistantId ? { ...message, text: result.text, status: undefined, recommendations: result.recommendations } : message));
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
              <div className="workspace-actions"><button className="model-status-button" onClick={() => setView("connection")}><span className={`connection-dot ${connection.connected ? "connected" : ""}`} />{modelLabel}</button>{connectionLoadError && <span className="connection-load-note">Status unavailable</span>}{hasChat && <button className="header-new-chat" onClick={startNewChat}><Plus className="header-new-chat-icon" size={15} aria-hidden="true" />New chat</button>}</div>
            </header>
            {hasChat ? <ChatWorkspace messages={chat} failedAssistantId={failedRequest?.assistantId} onRetry={() => void submitMessage(failedRequest?.text ?? "", true)} retryAvailable={Boolean(failedRequest) && !isBusy} onOpenMeal={setActiveMeal} onViewShortlist={showShortlist} onSubmit={(text) => void submitMessage(text)} onStop={stopRequest} busy={isBusy} modelLabel={modelLabel} onConnection={() => setView("connection")} /> : (
              <section className="welcome-screen">
                <div className="hero-grid"><div className="hero-copy"><p className="eyebrow">THE KIND OF HELPFUL YOU CAN EAT</p><h2 className="hero-title">Less overthinking.<br /><em>More good food.</em></h2><p className="hero-description">A tiny nudge toward something delicious, tuned to your cravings, your time, and the ingredients you love.</p></div><div className="hero-art" aria-label="Illustration of a colorful bowl of food" role="img"><div className="hero-sun" /><div className="hero-plate"><span className="hero-food hero-food-one" /><span className="hero-food hero-food-two" /><span className="hero-food hero-food-three" /><span className="hero-food hero-food-four" /><span className="hero-leaf" /></div><span className="hero-art-label">GOOD THINGS<br /><strong>COMING UP</strong></span></div></div>
                <Composer onSubmit={(text) => void submitMessage(text)} onStop={stopRequest} busy={isBusy} welcome modelLabel={modelLabel} onConnection={() => setView("connection")} />
                <p className="welcome-hint">Try a craving, a time limit, or an ingredient you want to use up.</p>
              </section>
            )}
          </>
        ) : view === "meals" ? <MealsView recommendations={browseRecommendations} preferences={preferences} isShortlist={Boolean(mealShortlist)} filter={filter} onFilterChange={(next) => { setMealShortlist(null); setFilter(next); }} onSave={toggleSaved} savedIds={savedIds} onOpen={setActiveMeal} onPreferences={() => navigate("preferences")} onBrowseAll={() => setMealShortlist(null)} />
          : view === "saved" ? <SavedView recommendations={savedRecommendations} onSave={toggleSaved} onOpen={setActiveMeal} onDiscover={() => navigate("discover")} />
            : view === "preferences" ? <PreferencePanel preferences={preferences} onSave={savePreferences} />
              : <ConnectionPanel connection={connection} onConnectionChange={setConnection} onBackToChat={() => setView("discover")} />}
      </main>
      {notice && <div className="notice" role="status"><Bookmark size={16} aria-hidden="true" />{notice}</div>}
      <MealDetailModal meal={activeMeal} saved={activeMeal ? savedIds.includes(activeMeal.id) : false} onClose={closeMeal} onSave={toggleSaved} />
    </div>
  );
}

function ChatWorkspace({ messages, failedAssistantId, onRetry, retryAvailable, onOpenMeal, onViewShortlist, onSubmit, onStop, busy, modelLabel, onConnection }: { messages: ChatMessage[]; failedAssistantId?: string; onRetry: () => void; retryAvailable: boolean; onOpenMeal: (meal: Meal) => void; onViewShortlist: (recommendations: MealRecommendation[]) => void; onSubmit: (text: string) => void; onStop: () => void; busy: boolean; modelLabel: string; onConnection: () => void; }) {
  return <section className="chat-workspace" aria-label="Your meal conversation"><div className="chat-scroll"><div className="chat-messages">{messages.map((message) => <article className={`chat-message ${message.role === "user" ? "is-user" : "is-assistant"}`} key={message.id}>{message.role === "assistant" && <div className="assistant-avatar" aria-hidden="true"><MessageCircle size={16} /></div>}<div className="message-body">{message.role === "assistant" && <span className="message-author">Remymy · {message.source ?? "Demo"}</span>}{message.status === "pending" ? <span className="chat-pending" aria-label="Remymy is thinking"><LoaderCircle className="spin" size={16} /> Thinking…</span> : <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div> }}>{message.text}</ReactMarkdown>}{message.status === "error" && message.id === failedAssistantId && retryAvailable && <button className="chat-retry" onClick={onRetry}><RotateCcw size={14} /> Try again</button>}{message.recommendations && message.recommendations.length > 0 && <div className="chat-meal-list" aria-label="Meal suggestions">{message.recommendations.slice(0, 3).map((recommendation) => <button className="chat-meal-row" key={recommendation.meal.id} onClick={() => onOpenMeal(recommendation.meal)}><span className="chat-meal-thumb" style={{ background: recommendation.meal.color }} aria-hidden="true"><Utensils size={15} /></span><span className="chat-meal-info"><strong>{recommendation.meal.title}</strong><small>{recommendation.meal.minutes} min · {recommendation.meal.cuisine}</small></span><ArrowRight size={15} aria-hidden="true" /></button>)}<button className="shortlist-link" onClick={() => onViewShortlist(message.recommendations ?? [])}>View shortlist <ArrowRight size={14} /></button></div>}</div></article>)}</div></div><div className="chat-composer-dock"><Composer onSubmit={onSubmit} onStop={onStop} busy={busy} welcome={false} modelLabel={modelLabel} onConnection={onConnection} /></div></section>;
}

function MealsView({ recommendations, preferences, isShortlist, filter, onFilterChange, onSave, savedIds, onOpen, onPreferences, onBrowseAll }: { recommendations: MealRecommendation[]; preferences: Preferences; isShortlist: boolean; filter: (typeof FILTERS)[number]["id"]; onFilterChange: (filter: (typeof FILTERS)[number]["id"]) => void; onSave: (meal: Meal) => void; savedIds: string[]; onOpen: (meal: Meal) => void; onPreferences: () => void; onBrowseAll: () => void; }) {
  return <section className="browse-view"><div className="view-heading"><p className="eyebrow">A LITTLE INSPIRATION</p><h1>{isShortlist ? "Your shortlist." : "Meals for the mood."}</h1><p>{isShortlist ? "The ideas from your conversation, ready for a closer look." : "Browse the small, cheerful catalog and find something that fits today."}</p></div><div className="content-toolbar"><div><p className="eyebrow">TUNED TO YOUR DEFAULTS</p><h2>{recommendations.length ? `${recommendations.length} good ideas` : "Nothing matches yet"}</h2><div className="constraint-summary" aria-label="Active meal preferences"><span>Up to {preferences.maxMinutes} min</span>{preferences.diet !== "any" && <span>{preferences.diet}</span>}{preferences.goal !== "balanced" && <span>{preferences.goal} goal</span>}{preferences.cuisine !== "any" && <span>{preferences.cuisine}</span>}{preferences.exclude.map((item) => <span key={item}>No {item}</span>)}<button className="constraint-edit" onClick={onPreferences}>Edit preferences</button></div></div><div className="filter-chips" role="group" aria-label="Filter meals">{FILTERS.map((item) => <button key={item.id} className={`filter-chip ${filter === item.id && !isShortlist ? "is-active" : ""}`} onClick={() => onFilterChange(item.id)} aria-pressed={filter === item.id && !isShortlist}>{item.label}</button>)}</div></div>{isShortlist && <button className="text-button" onClick={onBrowseAll}>Browse all meals <ArrowRight size={14} /></button>}{recommendations.length ? <div className="shortlist-grid">{recommendations.map((recommendation) => <MealCard key={recommendation.meal.id} recommendation={recommendation} saved={savedIds.includes(recommendation.meal.id)} onSave={onSave} onOpen={onOpen} />)}</div> : <div className="empty-state"><div className="empty-state-icon"><Utensils size={22} /></div><h3>Let&apos;s widen the plate.</h3><p>No meals in this demo match all your preferences. Adjust the time or ingredients to see more ideas.</p><div className="empty-actions"><button className="detail-action" onClick={onPreferences}>Edit preferences <ArrowRight size={17} /></button></div></div>}</section>;
}

function SavedView({ recommendations, onSave, onOpen, onDiscover }: { recommendations: MealRecommendation[]; onSave: (meal: Meal) => void; onOpen: (meal: Meal) => void; onDiscover: () => void; }) {
  return <section className="saved-view"><div className="view-heading"><p className="eyebrow">YOUR LITTLE ARCHIVE</p><h1>Saved for later.</h1><p>The meals that made you pause. They&apos;ll be here when you&apos;re ready.</p></div>{recommendations.length ? <div className="shortlist-grid">{recommendations.map((recommendation) => <MealCard key={recommendation.meal.id} recommendation={recommendation} saved onSave={onSave} onOpen={onOpen} />)}</div> : <div className="empty-state"><div className="empty-state-icon"><Bookmark size={22} /></div><h3>No saved meals yet.</h3><p>When something looks good, tap the bookmark and it&apos;ll land here.</p><button className="detail-action" onClick={onDiscover}>Find something good <ArrowRight size={17} /></button></div>}</section>;
}
