// The recipes the user saved, which the Data API's database keeps, and what the app keeps in this browser next to
// preferences and saved meals: the like or not of each meal and recipe (a recommended recipe's like also goes to the
// Data API; the demo meals are not in it, so theirs stay here), and the saved recipes of before they were in the
// database, which go up to it once.

import { dataApiError } from "./recipes";

export type SavedFood = {
  id: string;
  title: string;
  // a Food.com recipe, a recommended catalog food by its fdc_id, or one a user wrote; saved before there were recipes,
  // a food
  kind?: "recipe" | "food" | "user";
};

const KINDS: ReadonlySet<unknown> = new Set(["recipe", "food", "user"]);

export function parseSavedFoods(value: unknown): SavedFood[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is SavedFood => {
    if (!item || typeof item !== "object") return false;
    const food = item as Partial<SavedFood>;
    return typeof food.id === "string" && typeof food.title === "string" && (food.kind === undefined || KINDS.has(food.kind));
  });
}

// A saved recipe as the Data API answers it (its schemas.SavedItem) → a SavedFood; null for anything else.
function savedItem(value: unknown): SavedFood | null {
  if (!value || typeof value !== "object") return null;
  const item = value as { recipe_id?: unknown; kind?: unknown; title?: unknown };
  if (typeof item.recipe_id !== "string" || !KINDS.has(item.kind)) return null;
  return {
    id: item.recipe_id,
    // a recipe whose title the database does not have shows its id
    title: typeof item.title === "string" && item.title ? item.title : item.recipe_id,
    kind: item.kind as SavedFood["kind"],
  };
}

const savedPath = (username: string, id?: string) =>
  `/api/users/${encodeURIComponent(username)}/saved${id === undefined ? "" : `/${encodeURIComponent(id)}`}`;

// The recipes the user saved, newest first.
export async function fetchSaved(username: string, signal?: AbortSignal): Promise<SavedFood[]> {
  const response = await fetch(savedPath(username), { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Your saved recipes could not be loaded."));
  if (!Array.isArray(body)) throw new Error("Your saved recipes could not be loaded.");
  return body.map(savedItem).filter((item): item is SavedFood => item !== null);
}

// Save a recipe for the user, by its id; null when the database does not know the recipe.
export async function saveItem(username: string, id: string, signal?: AbortSignal): Promise<SavedFood | null> {
  const response = await fetch(savedPath(username, id), { method: "PUT", cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 404 && dataApiError(body, "") === "Recipe not found.") return null;
  if (!response.ok) throw new Error(dataApiError(body, "That recipe could not be saved."));
  const saved = savedItem(body);
  if (!saved) throw new Error("That recipe could not be saved.");
  return saved;
}

// Take a recipe out of the user's saved ones.
export async function unsaveItem(username: string, id: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(savedPath(username, id), { method: "DELETE", cache: "no-store", signal });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new Error(dataApiError(body, "That recipe could not be removed from your saved ones."));
  }
}

// id → true (liked) or false (not); anything else in the stored value is dropped.
export function parseLikes(value: unknown): Record<string, boolean> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([, like]) => typeof like === "boolean"));
}
