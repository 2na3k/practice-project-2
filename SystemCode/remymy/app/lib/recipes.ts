export type RecipeSummary = {
  id: string;
  title: string;
  ingredient_count: number;
  ingredient_preview: string[];
  quality_status: string;
};

export type RecipeDetail = {
  id: string;
  title: string;
  ingredients: string[];
  instructions: string[];
  quality_status: string;
};

// One row of a user's history from the Data API: whether they liked a recipe they cooked.
export type RecipeRating = {
  user_id: number;
  recipe_id: string;
  like: boolean;
  timestamp: string;
};

export type RecipePage = {
  items: RecipeSummary[];
  total: number;
  limit: number;
  offset: number;
};

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRecipeSummary(value: unknown): value is RecipeSummary {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<RecipeSummary>;
  return typeof item.id === "string" && typeof item.title === "string" &&
    typeof item.ingredient_count === "number" && Number.isInteger(item.ingredient_count) &&
    isStringArray(item.ingredient_preview) && typeof item.quality_status === "string";
}

export function parseRecipePage(value: unknown): RecipePage {
  if (!value || typeof value !== "object") throw new Error("Recipe service returned an unexpected response.");
  const page = value as Partial<RecipePage>;
  if (!Array.isArray(page.items) || !page.items.every(isRecipeSummary) ||
    typeof page.total !== "number" || typeof page.limit !== "number" || typeof page.offset !== "number") {
    throw new Error("Recipe service returned an unexpected response.");
  }
  return page as RecipePage;
}

export function parseRecipeDetail(value: unknown): RecipeDetail {
  if (!value || typeof value !== "object") throw new Error("Recipe service returned an unexpected response.");
  const detail = value as Partial<RecipeDetail>;
  if (typeof detail.id !== "string" || typeof detail.title !== "string" ||
    !isStringArray(detail.ingredients) || !isStringArray(detail.instructions) ||
    typeof detail.quality_status !== "string") {
    throw new Error("Recipe service returned an unexpected response.");
  }
  return detail as RecipeDetail;
}

export function parseRecipeRating(value: unknown): RecipeRating {
  if (!value || typeof value !== "object") throw new Error("Rating service returned an unexpected response.");
  const rating = value as Partial<RecipeRating>;
  if (typeof rating.user_id !== "number" || typeof rating.recipe_id !== "string" ||
    typeof rating.like !== "boolean" || typeof rating.timestamp !== "string") {
    throw new Error("Rating service returned an unexpected response.");
  }
  return rating as RecipeRating;
}

export function apiError(value: unknown, fallback: string): string {
  if (value && typeof value === "object" && "error" in value) {
    const error = (value as { error?: unknown }).error;
    if (typeof error === "string" && error.trim()) return error;
  }
  return fallback;
}

// The Data API answers errors as FastAPI does, {detail}; the app's own routes as {error}.
export function dataApiError(value: unknown, fallback: string): string {
  if (value && typeof value === "object" && "detail" in value) {
    const detail = (value as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return apiError(value, fallback);
}

export async function fetchRecipePage(query: { q: string; limit: number; offset: number }, signal?: AbortSignal): Promise<RecipePage> {
  const params = new URLSearchParams({ q: query.q, limit: String(query.limit), offset: String(query.offset) });
  const response = await fetch(`/api/recipes?${params.toString()}`, { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(apiError(body, "Recipes could not be loaded."));
  return parseRecipePage(body);
}

export async function fetchRecipeDetail(id: string, signal?: AbortSignal): Promise<RecipeDetail> {
  const response = await fetch(`/api/recipes/${encodeURIComponent(id)}`, { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(apiError(body, "This recipe could not be loaded."));
  return parseRecipeDetail(body);
}

// Adds whether the user liked the recipe to their history, through the app's rating route.
export async function saveRecipeRating(id: string, username: string, like: boolean, signal?: AbortSignal): Promise<RecipeRating> {
  const response = await fetch(`/api/recipes/${encodeURIComponent(id)}/rating`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, like }),
    cache: "no-store",
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Your rating could not be saved."));
  return parseRecipeRating(body);
}
