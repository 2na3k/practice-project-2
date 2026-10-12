import { apiError } from "./recipes";

// The app has no sign-in yet, so it asks for the user the Data API seeds for demos.
export const RECOMMENDATION_USERNAME = "demo";

export type BuyingProfile = {
  user_id: number;
  avg_price: number;
  avg_nutrient_values: Record<string, number>;
};

// A grocery food from the priced FDC catalog, not a recipe: id is its FDC id and
// score the model's probability that the user likes it.
export type RecommendedFood = {
  id: string;
  title: string;
  score: number;
};

export type Recommendations = {
  profile: BuyingProfile | null;
  items: RecommendedFood[];
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
  if (!(result.profile === null || isBuyingProfile(result.profile)) ||
    !Array.isArray(result.items) || !result.items.every(isRecommendedFood)) {
    throw new Error("Recommendation service returned an unexpected response.");
  }
  return result as Recommendations;
}

// The Data API answers errors as FastAPI does, {detail}; the app's own route as {error}.
function recommendationError(value: unknown): string {
  if (value && typeof value === "object" && "detail" in value) {
    const detail = (value as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return apiError(value, "Recommendations could not be loaded.");
}

export async function fetchRecommendations(username: string, topK: number, signal?: AbortSignal): Promise<Recommendations> {
  const params = new URLSearchParams({ top_k: String(topK) });
  const response = await fetch(`/api/recommend/${encodeURIComponent(username)}?${params.toString()}`, { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(recommendationError(body));
  return parseRecommendations(body);
}

export function formatPrice(value: number): string {
  return `S$${value.toFixed(2)}`;
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
