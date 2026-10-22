// The allergies preference: the allergens a user cannot eat, by the codes the Data API's preferences take.

export const ALLERGENS = [
  { code: "peanut", label: "Peanuts" },
  { code: "milk", label: "Milk & dairy" },
  { code: "egg", label: "Eggs" },
  { code: "wheat", label: "Wheat & gluten" },
  { code: "soy", label: "Soy" },
  { code: "fish", label: "Fish" },
  { code: "shellfish", label: "Shellfish" },
  { code: "sesame", label: "Sesame" },
  { code: "mustard", label: "Mustard" },
  { code: "celery", label: "Celery" },
  { code: "lupin", label: "Lupin" },
  { code: "sulphites", label: "Sulphites" },
] as const;

export type AllergenCode = (typeof ALLERGENS)[number]["code"];

const CODES: ReadonlySet<string> = new Set(ALLERGENS.map((allergen) => allergen.code));

export const isAllergenCode = (value: unknown): value is AllergenCode => typeof value === "string" && CODES.has(value);

// Known allergen codes only; preferences saved before there were allergies have none, or null.
export function validAllergies(value: unknown): boolean {
  return value === undefined || value === null ||
    (Array.isArray(value) && value.length <= ALLERGENS.length && value.every(isAllergenCode));
}

// The allergies as preferences keep them: known codes, each once, in the order of ALLERGENS.
export function normalizeAllergies(value: unknown): AllergenCode[] {
  const chosen = new Set(Array.isArray(value) ? value.filter(isAllergenCode) : []);
  return ALLERGENS.map((allergen) => allergen.code).filter((code) => chosen.has(code));
}
