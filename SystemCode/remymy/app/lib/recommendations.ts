import { dataApiError } from "./recipes";

export type BuyingProfile = {
  user_id: number;
  avg_price: number;
  avg_nutrient_values: Record<string, number>;
};

// A food from the priced FDC catalog (id its FDC id, all digits) or a Food.com recipe (id "openrecipes:<n>"); score
// is the model's probability that the user likes it, 0 for a cold-start pick.
export type RecommendedFood = {
  id: string;
  title: string;
  score: number;
};

// Whether a recommended id is a catalog food's FDC id rather than a recipe's, as the Data API's is_food_id tells them.
export function isFoodId(id: string): boolean {
  return /^\d+$/.test(id);
}

export type Recommendations = {
  profile: BuyingProfile | null;
  items: RecommendedFood[];
  // "model" when the model ranked the items for the user; "popular" until they have rated enough for it: the foods
  // rated by the most users that their preferences allow, which no model scored
  source: "model" | "popular";
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isBuyingProfile(value: unknown): value is BuyingProfile {
  if (!value || typeof value !== "object") return false;
  const profile = value as Partial<BuyingProfile>;
  const nutrients = profile.avg_nutrient_values;
  return isFiniteNumber(profile.user_id) && isFiniteNumber(profile.avg_price) &&
    Boolean(nutrients) && typeof nutrients === "object" && !Array.isArray(nutrients) &&
    Object.values(nutrients as object).every(isFiniteNumber);
}

function isRecommendedFood(value: unknown): value is RecommendedFood {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<RecommendedFood>;
  return typeof item.id === "string" && typeof item.title === "string" && isFiniteNumber(item.score);
}

export function parseRecommendations(value: unknown): Recommendations {
  if (!value || typeof value !== "object") throw new Error("Recommendation service returned an unexpected response.");
  const result = value as Partial<Recommendations>;
  // a Data API from before cold-start picks sends no source: its picks are the model's
  const source = result.source ?? "model";
  if (!(result.profile === null || isBuyingProfile(result.profile)) ||
    !Array.isArray(result.items) || !result.items.every(isRecommendedFood) ||
    (source !== "model" && source !== "popular")) {
    throw new Error("Recommendation service returned an unexpected response.");
  }
  return { ...result, source } as Recommendations;
}

export async function fetchRecommendations(username: string, topK: number, signal?: AbortSignal): Promise<Recommendations> {
  const params = new URLSearchParams({ top_k: String(topK) });
  const response = await fetch(`/api/recommend/${encodeURIComponent(username)}?${params.toString()}`, { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Recommendations could not be loaded."));
  return parseRecommendations(body);
}

// The catalog's nutrients are per 100 g; these are the ones worth a glance.
const HIGHLIGHTS: { key: string; unit: string; label?: string }[] = [
  { key: "energy_kcal", unit: "kcal" },
  { key: "protein_g", unit: "g", label: "protein" },
  { key: "carb_g", unit: "g", label: "carbs" },
  { key: "fat_g", unit: "g", label: "fat" },
];

export function nutrientHighlights(values: Record<string, number>): string[] {
  return HIGHLIGHTS.filter(({ key }) => isFiniteNumber(values[key])).map(({ key, unit, label }) => {
    const value = values[key];
    const amount = value >= 10 ? String(Math.round(value)) : value.toFixed(1);
    return label ? `${amount} ${unit} ${label}` : `${amount} ${unit}`;
  });
}
