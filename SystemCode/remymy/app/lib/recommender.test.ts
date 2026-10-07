import { describe, expect, it } from "vitest";
import { DEFAULT_PREFERENCES, MEALS, type Preferences } from "./meals";
import { alignRecommendations, interpretRequest, recommendMeals } from "./recommender";

const ids = (preferences: Preferences, query?: string): string[] =>
  recommendMeals(preferences, query).map(({ meal }) => meal.id);

describe("interpretRequest", () => {
  it("turns dietary, time, exclusion, goal, and cuisine language into preferences", () => {
    expect(
      interpretRequest(
        "vegan Korean food under 20 minutes, no peanuts, high protein",
      ),
    ).toEqual({
      diet: "vegan",
      maxMinutes: 20,
      exclude: ["peanuts"],
      goal: "protein",
      cuisine: "Korean",
    });
  });

  it("carries halal and combined vegan-halal constraints into MCP preferences", () => {
    const halal = interpretRequest("halal dinner");
    const later = interpretRequest("make it quick", halal);
    const veganHalal = interpretRequest("vegan halal dinner");

    expect(halal.diet).toBe("halal");
    expect(later.diet).toBe("halal");
    expect(veganHalal.diet).toBe("vegan");
    expect(veganHalal.exclude).toContain("halal");
  });

  it("carries restrictive choices into a later conversational turn", () => {
    const firstTurn = interpretRequest("vegetarian meals under 25 minutes");
    const secondTurn = interpretRequest(
      "also avoid mushrooms and make it high protein",
      firstTurn,
    );

    expect(secondTurn.diet).toBe("vegetarian");
    expect(secondTurn.maxMinutes).toBe(25);
    expect(secondTurn.goal).toBe("protein");
    expect(secondTurn.exclude).toContain("mushrooms");
  });

  it("keeps a tighter time bound when a later turn is looser", () => {
    const firstTurn = interpretRequest("quick vegan food under 15 minutes");
    const secondTurn = interpretRequest(
      "actually, I have up to 30 minutes",
      firstTurn,
    );

    expect(secondTurn.diet).toBe("vegan");
    expect(secondTurn.maxMinutes).toBe(15);
  });

  it("understands the short phrases used by the composer", () => {
    expect(interpretRequest("Ready in 20").maxMinutes).toBe(20);
    expect(interpretRequest("Plant-powered").diet).toBe("vegan");
    expect(interpretRequest("Protein please").goal).toBe("protein");
    expect(interpretRequest("Make it quicker").maxMinutes).toBe(20);
  });

  it("lets an explicit meat request override a persisted plant-only diet", () => {
    const vegan: Preferences = { ...DEFAULT_PREFERENCES, diet: "vegan", exclude: ["meat"] };
    const preferences = interpretRequest("get me a good meal with a lot of meat", vegan);
    const recommendations = recommendMeals(preferences, "a lot of meat");

    expect(preferences).toMatchObject({ diet: "any", goal: "protein" });
    expect(preferences.exclude).not.toContain("meat");
    expect(recommendations.length).toBeGreaterThan(0);
    expect(recommendations[0]?.meal.tags).not.toEqual(expect.arrayContaining(["vegan"]));
  });
});

describe("recommendMeals", () => {
  it("enforces vegan and ingredient exclusions as hard filters", () => {
    const preferences = interpretRequest("vegan, under 20 minutes, no soy");
    const recommendations = recommendMeals(preferences);

    expect(recommendations.length).toBeGreaterThan(0);
    expect(recommendations.every(({ meal }) => meal.minutes <= 20)).toBe(true);
    expect(
      recommendations.every(({ meal }) => meal.tags.includes("vegan")),
    ).toBe(true);
    expect(
      recommendations.every(
        ({ meal }) =>
          !meal.ingredients.some((ingredient) =>
            /soy|tofu|edamame/i.test(ingredient),
          ),
      ),
    ).toBe(true);
  });

  it("enforces vegetarian and dietary restrictions together", () => {
    const preferences = interpretRequest("vegetarian, no dairy, no gluten");
    const recommendations = recommendMeals(preferences);

    expect(recommendations.length).toBeGreaterThan(0);
    expect(
      recommendations.every(
        ({ meal }) =>
          meal.tags.includes("vegetarian") || meal.tags.includes("vegan"),
      ),
    ).toBe(true);
    expect(
      recommendations.every(
        ({ meal }) =>
          !meal.tags.some((tag) => /contains dairy|contains gluten/i.test(tag)),
      ),
    ).toBe(true);
    expect(
      recommendations.every(
        ({ meal }) =>
          !meal.ingredients.some((ingredient) =>
            /cream|cheese|yogurt|bread|pasta|pita|couscous|noodle/i.test(
              ingredient,
            ),
          ),
      ),
    ).toBe(true);
  });

  it("applies arbitrary ingredient exclusions from a UI prompt", () => {
    const prompt = "no avocado and no tomatoes";
    const preferences = interpretRequest(prompt, DEFAULT_PREFERENCES);
    const recommendations = recommendMeals(preferences, prompt);

    expect(preferences.exclude).toContain("avocado");
    expect(
      preferences.exclude.some((exclusion) => /tomat/i.test(exclusion)),
    ).toBe(true);
    expect(
      recommendations.every(
        ({ meal }) =>
          !meal.ingredients.some((ingredient) =>
            /avocado|tomato/i.test(ingredient),
          ),
      ),
    ).toBe(true);
  });

  it("handles compound exclusions and keeps plant milk available", () => {
    const prompt = "no dairy or peanuts, lentil curry";
    const preferences = interpretRequest(prompt, DEFAULT_PREFERENCES);
    const recommendations = recommendMeals(preferences, prompt);

    expect(preferences.exclude).toEqual(
      expect.arrayContaining(["dairy", "peanuts"]),
    );
    expect(
      recommendations.every(
        ({ meal }) => !meal.tags.some((tag) => /contains dairy/i.test(tag)),
      ),
    ).toBe(true);
    expect(
      recommendations.every(
        ({ meal }) =>
          !meal.ingredients.some((ingredient) =>
            /cream|cheese|yogurt|parmesan|mozzarella|feta|butter|tzatziki/i.test(
              ingredient,
            ),
          ),
      ),
    ).toBe(true);
    expect(
      recommendations.every(
        ({ meal }) =>
          !meal.ingredients.some((ingredient) => /peanut/i.test(ingredient)),
      ),
    ).toBe(true);
    expect(
      recommendations.some(({ meal }) => meal.id === "lentil-coconut-curry"),
    ).toBe(true);
  });

  it("uses the parsed composer preferences when ranking exact UI prompts", () => {
    const prompts = [
      "Ready in 20",
      "Plant-powered",
      "Protein please",
      "Make it quicker",
    ];

    for (const prompt of prompts) {
      const preferences = interpretRequest(prompt, DEFAULT_PREFERENCES);
      const recommendations = recommendMeals(preferences, prompt);

      expect(
        recommendations.every(
          ({ meal }) => meal.minutes <= preferences.maxMinutes,
        ),
      ).toBe(true);
    }
  });

  it("returns no meals when the hard time bound is impossible", () => {
    expect(recommendMeals({ ...DEFAULT_PREFERENCES, maxMinutes: 1 })).toEqual(
      [],
    );
  });

  it("explains and ranks a cuisine and goal match", () => {
    const preferences = interpretRequest("Japanese, high protein");
    const recommendations = recommendMeals(
      preferences,
      "Japanese high protein",
    );

    expect(recommendations.length).toBeGreaterThan(0);
    expect(recommendations[0].score).toBeGreaterThanOrEqual(
      recommendations[1]?.score ?? 0,
    );
    expect(recommendations[0].reasons.length).toBeGreaterThan(0);
    expect(
      recommendations[0].reasons.some((reason) => /protein/i.test(reason)),
    ).toBe(true);
    expect(
      recommendations[0].reasons.some((reason) => /Japanese/i.test(reason)),
    ).toBe(true);
  });

  it("falls back to the default shortlist for an unknown query", () => {
    expect(ids(DEFAULT_PREFERENCES, "surprise me")).toEqual(
      ids(DEFAULT_PREFERENCES),
    );
  });

  it("produces deterministic ordering and scores", () => {
    const preferences = interpretRequest(
      "vegan under 20 minutes, no mushrooms",
    );

    expect(recommendMeals(preferences, "something quick and fresh")).toEqual(
      recommendMeals(preferences, "something quick and fresh"),
    );
  });

  it("never returns more than the intended shortlist size", () => {
    expect(recommendMeals(DEFAULT_PREFERENCES).length).toBeLessThanOrEqual(6);
    expect(MEALS.length).toBeGreaterThan(6);
  });

  it("treats meat-free phrasing as vegetarian and keeps meat out of the cards", () => {
    const prompts = [
      "Better meat-free picks",
      "without meat",
      "I don't eat meat",
      "exclude meat",
      "meatfree dinners under 30 minutes",
    ];

    for (const prompt of prompts) {
      const preferences = interpretRequest(prompt, DEFAULT_PREFERENCES);
      expect(preferences.diet).toBe("vegetarian");
      const recommendations = recommendMeals(preferences, prompt);
      expect(recommendations.length).toBeGreaterThan(0);
      expect(
        recommendations.every(
          ({ meal }) =>
            meal.tags.includes("vegetarian") || meal.tags.includes("vegan"),
        ),
      ).toBe(true);
      expect(
        recommendations.every(
          ({ meal }) =>
            !/turkey|chicken|salmon|beef|tuna/i.test(meal.title),
        ),
      ).toBe(true);
    }
  });

  it("drops meat meals from a mixed shortlist when the request is meat-free", () => {
    const mixed = recommendMeals(DEFAULT_PREFERENCES);
    expect(mixed.some(({ meal }) => /turkey|chicken|salmon/i.test(meal.title))).toBe(
      true,
    );

    const preferences = interpretRequest("meat-free picks", DEFAULT_PREFERENCES);
    const aligned = alignRecommendations(mixed, preferences, "meat-free picks");

    expect(aligned.length).toBeGreaterThan(0);
    expect(
      aligned.every(
        ({ meal }) =>
          meal.tags.includes("vegetarian") || meal.tags.includes("vegan"),
      ),
    ).toBe(true);
    expect(aligned.some(({ meal }) => meal.id === "sesame-tofu-rice-bowl")).toBe(
      true,
    );
  });
});
