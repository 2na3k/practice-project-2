import { normalizeAllergies, type AllergenCode } from "./allergens";
import { normalizeBudget } from "./budget";
import type { Preferences } from "./meals";
import { dataApiError } from "./recipes";

// The preferences as the Data API's users database keeps them (schemas.Preferences).
export type DataApiPreferences = {
  special_diet: "vegetarian" | "halal" | null;
  cuisines: string[];
  preferred_nutrient: string | null;
  budget: number;
  allergies: AllergenCode[];
};

// The app's preferences in the Data API's shape. Its diets have no vegan, so a vegan is saved as vegetarian, the
// nearest it filters by; the time limit and the ingredients to skip stay in this browser.
export function toDataApiPreferences(preferences: Preferences): DataApiPreferences {
  return {
    special_diet: preferences.diet === "any" ? null : preferences.diet === "halal" ? "halal" : "vegetarian",
    cuisines: preferences.cuisine && preferences.cuisine !== "any" ? [preferences.cuisine] : [],
    preferred_nutrient: preferences.goal === "protein" ? "protein" : null,
    budget: normalizeBudget(preferences.budget),
    allergies: normalizeAllergies(preferences.allergies),
  };
}

// Save the preferences to the user in the Data API, which drops the recommendations it made for the old ones.
export async function saveUserPreferences(username: string, preferences: Preferences, signal?: AbortSignal): Promise<void> {
  const response = await fetch(`/api/users/${encodeURIComponent(username)}/preferences`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(toDataApiPreferences(preferences)),
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new Error(dataApiError(body, "Your preferences could not be saved."));
  }
}
