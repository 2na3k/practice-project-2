import {
  DEFAULT_PREFERENCES,
  MEALS,
  type Meal,
  type Preferences,
} from "./meals";

export { DEFAULT_PREFERENCES } from "./meals";
export type { Preferences } from "./meals";

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

const exclusionVariants = (value: string): string[] => {
  const normalized = canonicalExclude(value);
  const variants = [normalized];
  if (normalized.endsWith("ies")) variants.push(`${normalized.slice(0, -3)}y`);
  else if (normalized.endsWith("oes")) variants.push(normalized.slice(0, -2));
  else if (normalized.endsWith("s")) variants.push(normalized.slice(0, -1));
  return Array.from(new Set(variants.filter(Boolean)));
};

/**
 * Interpret a natural language turn into an evolving preference state.
 * Restrictive diet, time, and exclusion constraints accumulate by design.
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
  };
  const normalized = normalizeText(text);

  const saysVegan =
    /\bvegan\b|\bplant[- ]based\b|\bplant[- ]powered\b/.test(normalized) &&
    !/\b(?:not|no longer)\s+vegan\b/.test(normalized);
  const saysVegetarian =
    /\bvegetarian\b|\bveggie\b|\bno\s+meat\b|\bmeat[- ]free\b/.test(
      normalized,
    ) && !/\b(?:not|no longer)\s+vegetarian\b/.test(normalized);
  if (saysVegan) source.diet = "vegan";
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

  if (
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

  return source;
}

const mealSearchText = (meal: Meal): string =>
  normalizeText(
    [
      meal.title,
      meal.description,
      meal.cuisine,
      ...meal.tags,
      ...meal.ingredients,
    ].join(" "),
  );

const mealIngredientText = (meal: Meal): string =>
  normalizeText([meal.title, meal.description, ...meal.ingredients].join(" "));

const escapeRegExp = (value: string): string =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const containsExcluded = (meal: Meal, exclusion: string): boolean => {
  // Tags such as "gluten-free" describe an allowed meal and must not cause a
  // gluten exclusion to match. Ingredients are the source of truth here.
  const text = mealIngredientText(meal);
  const canonical = canonicalExclude(exclusion);
  const terms = DIETARY_EXCLUSIONS[canonical] ?? exclusionVariants(canonical);
  const searchableText =
    canonical === "dairy" ? text.replace(/\bcoconut\s+milk\b/g, "") : text;
  return terms
    .flatMap(exclusionVariants)
    .some((term) =>
      new RegExp(
        `\\b${escapeRegExp(normalizeText(term)).replace(/\\s+/g, "\\s+")}\\b`,
        "i",
      ).test(searchableText),
    );
};

const isVegetarian = (meal: Meal): boolean =>
  meal.tags.some(
    (tag) =>
      normalizeText(tag) === "vegetarian" || normalizeText(tag) === "vegan",
  );
const isVegan = (meal: Meal): boolean =>
  meal.tags.some((tag) => normalizeText(tag) === "vegan");

const cuisineMatches = (mealCuisine: string, preference: string): boolean => {
  const wanted = normalizeText(preference);
  if (!wanted || wanted === "any" || wanted === "all") return false;
  const actual = normalizeText(mealCuisine);
  return (
    actual === wanted || actual.includes(wanted) || wanted.includes(actual)
  );
};

const queryTokens = (query: string): string[] =>
  normalizeText(query)
    .split(/[^a-z0-9]+/)
    .filter(
      (token) =>
        token.length > 2 &&
        !["the", "and", "with", "for", "something", "meal", "please"].includes(
          token,
        ),
    );

export type MealRecommendation = {
  meal: Meal;
  score: number;
  reasons: string[];
};

/** Return a hard-filtered and explainable shortlist for the current state. */
export function recommendMeals(
  preferences: Preferences = DEFAULT_PREFERENCES,
  query?: string,
  limit = 6,
): MealRecommendation[] {
  const effective = query
    ? interpretRequest(query, preferences)
    : interpretRequest("", preferences);
  const tokens = query ? queryTokens(query) : [];
  const eligible = MEALS.filter((meal) => {
    if (meal.minutes > effective.maxMinutes) return false;
    if (effective.diet === "vegan" && !isVegan(meal)) return false;
    if (effective.diet === "vegetarian" && !isVegetarian(meal)) return false;
    return !effective.exclude.some((exclusion) =>
      containsExcluded(meal, exclusion),
    );
  });

  const ranked = eligible.map((meal, index) => {
    const searchText = mealSearchText(meal);
    let score = 50;
    const reasons: string[] = [];
    if (effective.diet === "vegan") reasons.push("Fits your vegan preference.");
    else if (effective.diet === "vegetarian")
      reasons.push("Fits your vegetarian preference.");
    reasons.push(
      `${meal.minutes} min, within your ${effective.maxMinutes}-minute limit.`,
    );

    for (const exclusion of effective.exclude) reasons.push(`No ${exclusion}.`);
    if (effective.goal === "protein") {
      score += Math.round(Math.min(meal.protein, 45) * 0.9);
      reasons.push(`${meal.protein}g protein supports your high-protein goal.`);
    } else if (effective.goal === "light") {
      score += Math.round(Math.max(0, 700 - meal.calories) / 12);
      reasons.push(`${meal.calories} calories keeps it on the lighter side.`);
    } else {
      score += Math.max(0, 12 - Math.abs(meal.calories - 520) / 45);
      score += Math.min(meal.protein, 30) / 5;
      reasons.push(`A balanced option with ${meal.protein}g protein.`);
    }
    if (cuisineMatches(meal.cuisine, effective.cuisine)) {
      score += 18;
      reasons.push(`Matches your ${effective.cuisine} preference.`);
    }
    for (const token of tokens) {
      if (searchText.includes(token))
        score += meal.title.toLowerCase().includes(token) ? 8 : 3;
    }
    if (tokens.length > 0 && tokens.some((token) => searchText.includes(token)))
      reasons.push("Matches details from your latest request.");
    score += Math.max(0, 2 - index / 100); // stable, tiny catalog-order tie breaker
    return {
      meal,
      score: Math.round(score * 10) / 10,
      reasons: Array.from(new Set(reasons)),
    };
  });

  ranked.sort(
    (a, b) => b.score - a.score || a.meal.title.localeCompare(b.meal.title),
  );
  const boundedLimit = Number.isFinite(limit) ? Math.max(0, Math.min(MEALS.length, Math.floor(limit))) : 6;
  return ranked.slice(0, boundedLimit);
}
