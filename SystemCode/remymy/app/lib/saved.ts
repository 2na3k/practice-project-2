// What the app keeps in this browser next to preferences and saved meals: saved recommended recipes, and the like or
// not of each meal and recipe. A recommended recipe's like also goes to the Data API; the demo meals are not in it, so
// theirs stay here.

export type SavedFood = {
  id: string;
  title: string;
};

export function parseSavedFoods(value: unknown): SavedFood[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is SavedFood => {
    if (!item || typeof item !== "object") return false;
    const food = item as Partial<SavedFood>;
    return typeof food.id === "string" && typeof food.title === "string";
  });
}

// id → true (liked) or false (not); anything else in the stored value is dropped.
export function parseLikes(value: unknown): Record<string, boolean> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([, like]) => typeof like === "boolean"));
}
