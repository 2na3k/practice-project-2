// The rule-based reader of a chat turn: it turns what the person typed into the preferences the connected chat
// carries from one message to the next. Restrictive diet, time, and exclusion constraints accumulate by design.

import { normalizeAllergies, type AllergenCode } from "./allergens";
import { normalizeBudget } from "./budget";

export type Preferences = {
  diet: "any" | "vegetarian" | "vegan" | "halal";
  maxMinutes: number;
  exclude: string[];
  goal: "balanced" | "protein" | "light";
  cuisine: string;
  // most to spend on one meal, in SGD; 0 (or unset) is no limit, as in the Data API
  budget?: number;
  // the allergens never to recommend; preferences saved before there were allergies have none
  allergies?: AllergenCode[];
};

export const DEFAULT_PREFERENCES: Preferences = {
  diet: "any",
  maxMinutes: 30,
  exclude: [],
  goal: "balanced",
  cuisine: "any",
};

const DIETARY_EXCLUSIONS: Record<string, string[]> = {
  dairy: [
    "dairy",
    "milk",
    "cheese",
    "yogurt",
    "yoghurt",
    "butter",
    "cream",
    "parmesan",
    "mozzarella",
    "feta",
    "cheddar",
    "tzatziki",
    "whey",
  ],
  gluten: [
    "gluten",
    "wheat",
    "flour",
    "bread",
    "pita",
    "sourdough",
    "pasta",
    "spaghetti",
    "rigatoni",
    "couscous",
    "noodle",
    "noodles",
    "seitan",
  ],
  peanuts: ["peanut", "peanuts", "peanut butter"],
  nuts: [
    "nut",
    "nuts",
    "peanut",
    "almond",
    "cashew",
    "walnut",
    "pecan",
    "pistachio",
    "hazelnut",
  ],
  mushrooms: ["mushroom", "mushrooms"],
  eggs: ["egg", "eggs", "mayonnaise"],
  soy: ["soy", "tofu", "edamame", "miso", "tempeh"],
  fish: ["fish", "salmon", "tuna", "anchovy", "sardine", "cod"],
  seafood: [
    "fish",
    "salmon",
    "tuna",
    "shrimp",
    "prawn",
    "shellfish",
    "seafood",
    "anchovy",
    "sardine",
  ],
  shellfish: [
    "shrimp",
    "prawn",
    "shellfish",
    "crab",
    "lobster",
    "oyster",
    "mussel",
  ],
  chicken: ["chicken"],
  beef: ["beef", "steak"],
  pork: ["pork", "bacon", "ham", "prosciutto"],
  cilantro: ["cilantro", "coriander"],
  spicy: [
    "chili",
    "chilli",
    "jalapeño",
    "jalapeno",
    "gochujang",
    "pepper flakes",
  ],
  meat: ["meat", "meats", "red meat"],
};

const CUISINE_ALIASES: Record<string, string> = {
  asian: "Asian",
  chinese: "Chinese",
  japanese: "Japanese",
  korean: "Korean",
  thai: "Thai",
  indian: "Indian",
  italian: "Italian",
  pasta: "Italian",
  mediterranean: "Mediterranean",
  greek: "Greek",
  mexican: "Mexican",
  "middle eastern": "Middle Eastern",
  middleeastern: "Middle Eastern",
  american: "American",
};

// The cuisines a request can name, for Preferences to offer.
export const CUISINES = [...new Set(Object.values(CUISINE_ALIASES))];

const normalizeText = (value: string): string =>
  value
    .toLocaleLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[’']/g, "")
    .trim();

const canonicalExclude = (value: string): string => {
  const normalized = normalizeText(value).replace(/[-_]/g, " ");
  if (normalized.includes("dairy") || normalized.includes("lactose"))
    return "dairy";
  if (normalized.includes("gluten") || normalized.includes("wheat"))
    return "gluten";
  if (normalized.includes("peanut")) return "peanuts";
  if (
    normalized === "nut" ||
    normalized === "nuts" ||
    normalized.includes("tree nut")
  )
    return "nuts";
  if (normalized.includes("mushroom")) return "mushrooms";
  if (normalized.includes("shellfish")) return "shellfish";
  if (normalized.includes("seafood")) return "seafood";
  if (normalized === "egg" || normalized === "eggs") return "eggs";
  return normalized;
};

const addExclusion = (exclusions: string[], value: string): void => {
  const normalized = canonicalExclude(value);
  if (normalized && !exclusions.includes(normalized))
    exclusions.push(normalized);
};

/**
 * Interpret a natural language turn into an evolving preference state.
 * Restrictive diet, time, and exclusion constraints accumulate by design.
 * A clear request to eat meat explicitly relaxes a prior vegan/vegetarian diet.
 */
export function interpretRequest(
  text: string,
  current: Preferences = DEFAULT_PREFERENCES,
): Preferences {
  const source: Preferences = {
    diet: current.diet,
    maxMinutes: Number.isFinite(current.maxMinutes)
      ? Math.max(1, Math.round(current.maxMinutes))
      : DEFAULT_PREFERENCES.maxMinutes,
    exclude: [...(current.exclude ?? [])].map(canonicalExclude).filter(Boolean),
    goal: current.goal,
    cuisine: current.cuisine || DEFAULT_PREFERENCES.cuisine,
    // kept when the preferences have one, 0 included, so preferences without a budget keep their shape
    ...(current.budget !== undefined && { budget: normalizeBudget(current.budget) }),
    ...(current.allergies !== undefined && { allergies: normalizeAllergies(current.allergies) }),
  };
  const normalized = normalizeText(text);
  if (/\bhalal\b/.test(normalized)) addExclusion(source.exclude, "halal");

  const saysHalal = /\bhalal\b/.test(normalized);
  const saysVegan =
    /\bvegan\b|\bplant[- ]based\b|\bplant[- ]powered\b/.test(normalized) &&
    !/\b(?:not|no longer)\s+vegan\b/.test(normalized);
  const saysVegetarian =
    /\bvegetarian\b|\bveggie\b|\bveggies\b|\bno\s+meats?\b|\bmeat[\s\-\u2013\u2014]*free\b|\bmeatfree\b|\bwithout\s+(?:any\s+)?meats?\b|\b(?:avoid|avoiding|exclude|excluding|skip|skipping)\s+(?:any\s+)?meats?\b|\bdon(?:t|'t)\s+(?:eat|want|like)\s+meats?\b|\bno\s+animal(?:s|\s+protein|\s+products?)?\b/.test(
      normalized,
    ) && !/\b(?:not|no longer)\s+vegetarian\b/.test(normalized);
  const explicitlyAllowsMeat =
    /\b(?:not|no longer)\s+(?:vegan|vegetarian)\b|\b(?:lots? of|a lot of|more|extra)\s+(?:meat|beef|chicken|turkey|pork)\b|\b(?:want|craving|give me|get me|with)\s+(?:some\s+|a\s+)?(?:meat|beef|chicken|turkey|pork)\b/.test(
      normalized,
    );
  if (explicitlyAllowsMeat && current.diet !== "halal") {
    source.diet = "any";
    source.exclude = source.exclude.filter(
      (exclusion) => canonicalExclude(exclusion) !== "meat",
    );
  } else if (saysVegan) {
    source.diet = "vegan";
    if (saysHalal || current.diet === "halal") addExclusion(source.exclude, "halal");
  } else if (saysHalal && current.diet === "vegan") {
    addExclusion(source.exclude, "halal");
  } else if (saysHalal) source.diet = "halal";
  else if (saysVegetarian && source.diet === "any") source.diet = "vegetarian";

  const timePatterns = [
    /\b(?:under|within|less than|up to|at most|max(?:imum)?|no more than)\s+(\d{1,3})\s*(?:m|min|mins|minute|minutes)?\b/,
    /\b(\d{1,3})\s*(?:m|min|mins|minute|minutes)\s*(?:or less|maximum|max)?\b/,
    /\b(?:ready|done|finished|cooked)\s+in\s+(\d{1,3})\b/,
  ];
  for (const pattern of timePatterns) {
    const match = normalized.match(pattern);
    if (match) {
      const minutes = Number(match[1]);
      if (Number.isFinite(minutes) && minutes > 0)
        source.maxMinutes = Math.min(source.maxMinutes, minutes);
      break;
    }
  }
  if (
    /\b(?:quick(?:er|est)?|quickly|fast(?:er)?|speedy|in a hurry|short on time)\b/.test(
      normalized,
    )
  ) {
    source.maxMinutes = Math.min(source.maxMinutes, 20);
  }

  if (/\b(?:lots? of|a lot of|more|extra)\s+(?:meat|beef|chicken|turkey|pork)\b/.test(normalized))
    source.goal = "protein";
  else if (
    /\b(?:high|higher|more|lots of|protein[- ]rich)\s+protein\b|\bprotein[- ]packed\b|\bprotein\b/.test(
      normalized,
    )
  )
    source.goal = "protein";
  else if (
    /\b(?:light|lighter|low calorie|lower calorie|healthy and light)\b/.test(
      normalized,
    )
  )
    source.goal = "light";
  else if (/\bbalanced\b/.test(normalized)) source.goal = "balanced";

  for (const [key, aliases] of Object.entries(DIETARY_EXCLUSIONS)) {
    const escaped = aliases
      .map((alias) => alias.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|");
    const exclusionPattern = new RegExp(
      `(?:\\bno\\s+(?:any\\s+)?(?:${escaped})\\b|\\bwithout\\s+(?:any\\s+)?(?:${escaped})\\b|\\b(?:avoid|avoiding|allergic\\s+to|allergy\\s+to|cant\\s+have|cannot\\s+have|dont\\s+eat|do\\s+not\\s+eat|cant\\s+eat|cannot\\s+eat|dont\\s+want|do\\s+not\\s+want|dont\\s+like|do\\s+not\\s+like)\\s+(?:any\\s+)?(?:${escaped})\\b|\\b(?:${escaped})[- ]free\\b)`,
      "i",
    );
    if (exclusionPattern.test(normalized)) addExclusion(source.exclude, key);
  }

  // Also keep arbitrary ingredients such as avocado or tomatoes. Split a
  // single natural-language directive so "no dairy or peanuts" accumulates
  // both exclusions without treating the conjunction as part of an ingredient.
  const directExclusions = normalized.matchAll(
    /\b(?:no|without|avoid|avoiding)\s+(?:any\s+)?([^.!?;]+)/g,
  );
  for (const match of directExclusions) {
    const candidates = match[1].split(/\s*(?:,|\band\b|\bor\b)\s*/);
    for (const rawCandidate of candidates) {
      const candidate = rawCandidate
        .replace(/^(?:no|without|avoid|avoiding)\s+/, "")
        .replace(/\s+(?:please|today|tonight|thanks|thank you).*$/, "")
        .replace(/\s+(?:in|on|with|at|for)\s+.*$/, "")
        .trim();
      if (
        !candidate ||
        /\d/.test(candidate) ||
        candidate.split(/\s+/).length > 3
      )
        continue;
      if (
        /\b(?:more|less|than|minutes?|time|protein|calories|quick(?:er|est)?|fast(?:er)?|meal|food)\b/.test(
          candidate,
        )
      )
        continue;
      addExclusion(source.exclude, candidate);
    }
  }

  for (const [alias, cuisine] of Object.entries(CUISINE_ALIASES)) {
    if (
      new RegExp(`\\b${alias.replace(/\s+/g, "\\s+")}\\b`, "i").test(normalized)
    ) {
      source.cuisine = cuisine;
      break;
    }
  }

  if (
    source.diet === "any" &&
    source.exclude.some((exclusion) => canonicalExclude(exclusion) === "meat")
  ) {
    source.diet = "vegetarian";
  }

  return source;
}
