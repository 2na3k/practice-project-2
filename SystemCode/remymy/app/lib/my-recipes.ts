import { dataApiError } from "./recipes";

// Nutrients per 100 g, in the Data API's names; the user may enter any of them.
export const NUTRIENT_FIELDS = [
  { key: "energy_kcal", label: "Energy", unit: "kcal" },
  { key: "protein_g", label: "Protein", unit: "g" },
  { key: "fat_g", label: "Fat", unit: "g" },
  { key: "carb_g", label: "Carbs", unit: "g" },
  { key: "fiber_g", label: "Fibre", unit: "g" },
  { key: "sugars_g", label: "Sugars", unit: "g" },
  { key: "satfat_g", label: "Saturated fat", unit: "g" },
  { key: "sodium_mg", label: "Sodium", unit: "mg" },
  { key: "cholesterol_mg", label: "Cholesterol", unit: "mg" },
] as const;
export type NutrientKey = (typeof NUTRIENT_FIELDS)[number]["key"];
const NUTRIENT_KEYS = new Set<string>(NUTRIENT_FIELDS.map((field) => field.key));

// The recipe classifier's labels (label_cols of api/weights/recipe_classifier/config.json), offered as tags whether or
// not the classifier suggested them.
export const CLASSIFIER_LABELS = [
  "easy", "breakfast", "weeknight_dinner", "one_pot_one_pan", "ready_to_eat", "kid_friendly", "hearty_filling",
  "protein_centerpiece", "dessert_sweet_treat", "crispy_crunchy", "smoky_grilled", "spicy", "umami_rich",
  "street_food_style", "advanced_gourmet", "generic",
] as const;

// The Data API's limits on a recipe (UserRecipeCreate in api/src/remymy_api/schemas.py).
export const LIMITS = { title: 200, line: 500, ingredients: 100, instructions: 100, tags: 30, tag: 40, query: 500 } as const;
const TAG_PATTERN = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;

export type TagSource = "classifier" | "user";
export type RecipeTag = { label: string; source: TagSource };

export type NewRecipe = {
  title: string;
  ingredients: string[];
  instructions: string[];
  tags: RecipeTag[];
  nutrients: Partial<Record<NutrientKey, number>>;
};

export type SavedRecipe = NewRecipe & { id: string; user_id: number; created_at: string };

export type TagSuggestion = {
  // what the classifier predicts, and every label's probability (or vote share)
  labels: string[];
  scores: Record<string, number>;
  model: string;
};

// "dessert_sweet_treat" → "Dessert sweet treat"
export function tagText(label: string): string {
  const words = label.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// A tag the user typed, in the classifier's form: "Grandma's  Curry!" → "grandma_s_curry"; null when nothing is left.
export function normalizeTag(text: string): string | null {
  const tag = text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, LIMITS.tag).replace(/_+$/, "");
  return tag || null;
}

// One trimmed, non-empty line each.
export function splitLines(text: string): string[] {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

// The nutrients the user filled in, as numbers; `invalid` names those that are not a number of 0 or more.
export function parseNutrients(draft: Partial<Record<NutrientKey, string>>): {
  nutrients: Partial<Record<NutrientKey, number>>;
  invalid: NutrientKey[];
} {
  const nutrients: Partial<Record<NutrientKey, number>> = {};
  const invalid: NutrientKey[] = [];
  for (const { key } of NUTRIENT_FIELDS) {
    const text = draft[key]?.trim();
    if (!text) continue;
    const value = Number(text);
    if (Number.isFinite(value) && value >= 0) nutrients[key] = value;
    else invalid.push(key);
  }
  return { nutrients, invalid };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validLines(value: unknown, max: number): value is string[] {
  return Array.isArray(value) && value.length <= max &&
    value.every((line) => typeof line === "string" && line.trim().length > 0 && line.length <= LIMITS.line);
}

export function validNutrients(value: unknown): value is Partial<Record<NutrientKey, number>> {
  return isRecord(value) &&
    Object.entries(value).every(([key, amount]) => NUTRIENT_KEYS.has(key) && typeof amount === "number" && Number.isFinite(amount));
}

// The recipe as the Data API accepts it, or what is wrong with it; the app's recipes route and the form both check.
export function validateNewRecipe(value: unknown): { recipe: NewRecipe } | { error: string } {
  if (!isRecord(value)) return { error: "Send a recipe." };
  const { title, ingredients, instructions = [], tags = [], nutrients = {} } = value;
  if (typeof title !== "string" || !title.trim() || title.length > LIMITS.title) {
    return { error: `Give the recipe a title of up to ${LIMITS.title} characters.` };
  }
  if (!validLines(ingredients, LIMITS.ingredients) || ingredients.length === 0) {
    return { error: `List 1 to ${LIMITS.ingredients} ingredients, one per line, each up to ${LIMITS.line} characters.` };
  }
  if (!validLines(instructions, LIMITS.instructions)) {
    return { error: `List up to ${LIMITS.instructions} steps, one per line, each up to ${LIMITS.line} characters.` };
  }
  if (!Array.isArray(tags) || tags.length > LIMITS.tags || !tags.every((tag) =>
    isRecord(tag) && typeof tag.label === "string" && tag.label.length <= LIMITS.tag && TAG_PATTERN.test(tag.label) &&
    (tag.source === "classifier" || tag.source === "user"))) {
    return { error: `Use up to ${LIMITS.tags} tags of letters, digits and underscores.` };
  }
  if (new Set(tags.map((tag) => (tag as RecipeTag).label)).size !== tags.length) return { error: "Use each tag once." };
  if (!validNutrients(nutrients)) return { error: "Nutrients must be numbers per 100 g." };
  return { recipe: { title: title.trim(), ingredients, instructions, tags: tags as RecipeTag[], nutrients } };
}

export function parseTagSuggestion(value: unknown): TagSuggestion {
  if (!isRecord(value) || !Array.isArray(value.labels) || !value.labels.every((label) => typeof label === "string") ||
    !isRecord(value.scores) || !Object.values(value.scores).every((score) => typeof score === "number") ||
    typeof value.model !== "string") {
    throw new Error("The classifier returned an unexpected response.");
  }
  return { labels: value.labels as string[], scores: value.scores as Record<string, number>, model: value.model };
}

export function parseSavedRecipe(value: unknown): SavedRecipe {
  const checked = validateNewRecipe(value);
  if ("error" in checked || !isRecord(value) || typeof value.id !== "string" || typeof value.user_id !== "number" ||
    typeof value.created_at !== "string") {
    throw new Error("Recipe service returned an unexpected response.");
  }
  return { ...checked.recipe, id: value.id, user_id: value.user_id, created_at: value.created_at };
}

// The classifier's tags for a dish, through the app's classify route. It was trained on single foods described the
// FoodData Central way, so the dish name (not the ingredient list) is what it reads, with any nutrients per 100 g.
export async function suggestTags(title: string, nutrients: Partial<Record<NutrientKey, number>>,
  signal?: AbortSignal): Promise<TagSuggestion> {
  const response = await fetch("/api/classify", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query: title.trim().slice(0, LIMITS.query), nutrients }),
    cache: "no-store",
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Tag suggestions are unavailable."));
  return parseTagSuggestion(body);
}

// Saves the user's own recipe through the app's recipes route (the Data API's POST /api/recipes).
export async function saveMyRecipe(username: string, recipe: NewRecipe, signal?: AbortSignal): Promise<SavedRecipe> {
  const response = await fetch("/api/recipes", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, recipe }),
    cache: "no-store",
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Your recipe could not be saved."));
  return parseSavedRecipe(body);
}
