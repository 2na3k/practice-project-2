// What the app keeps in this browser: the recommended recipes the person saved for later.

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
