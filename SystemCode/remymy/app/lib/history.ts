import { dataApiError } from "./recipes";

// One row of a user's history from the Data API: a recipe of the recipe database, or a recommended catalog food
// rated by its FDC id, and whether they liked it.
export type HistoryItem = {
  recipe_id: string;
  kind: "recipe" | "food";
  // null when its database no longer has it
  title: string | null;
  like: boolean;
  timestamp: string;
};

function isHistoryItem(value: unknown): value is HistoryItem {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<HistoryItem>;
  return typeof item.recipe_id === "string" && (item.kind === "recipe" || item.kind === "food") &&
    (item.title === null || typeof item.title === "string") && typeof item.like === "boolean" &&
    typeof item.timestamp === "string";
}

export function parseHistory(value: unknown): HistoryItem[] {
  if (!Array.isArray(value) || !value.every(isHistoryItem)) throw new Error("History service returned an unexpected response.");
  return value;
}

export async function fetchHistory(username: string, signal?: AbortSignal): Promise<HistoryItem[]> {
  const response = await fetch(`/api/users/${encodeURIComponent(username)}/history?limit=200`, { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Your history could not be loaded."));
  return parseHistory(body);
}

// "12 Oct 2026, 16:20" in the viewer's own locale and time zone.
export function formatHistoryDate(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

