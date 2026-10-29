import { describe, expect, it } from "vitest";
import { nutrientHighlights, parseRecommendations } from "./recommendations";

describe("recommendation API contract", () => {
  it("accepts a profile with scored foods", () => {
    const result = parseRecommendations({
      profile: { user_id: 1, avg_price: 4.2, avg_nutrient_values: { energy_kcal: 120, protein_g: 8.1 } },
      items: [{ id: "167516", title: "Waffles, buttermilk, frozen", score: 0.8 }],
    });
    expect(result.items[0].title).toBe("Waffles, buttermilk, frozen");
    expect(result.profile?.avg_price).toBe(4.2);
  });

  it("accepts a user with no history yet", () => {
    expect(parseRecommendations({ profile: null, items: [], source: "model" })).toEqual({ profile: null, items: [], source: "model" });
  });

  it("accepts random picks for a profile the model does not recommend for yet", () => {
    const result = parseRecommendations({ profile: null, items: [{ id: "175237", title: "Beans, black, cooked", score: 0 }], source: "random" });
    expect(result.source).toBe("random");
    expect(result.items[0].id).toBe("175237");
  });

  it("takes picks without a source for the model's", () => {
    expect(parseRecommendations({ profile: null, items: [] }).source).toBe("model");
  });

  it("rejects fields with the wrong shape", () => {
    expect(() => parseRecommendations({ profile: null, items: [{ id: 167516, title: "Waffles", score: 0.8 }] })).toThrow();
    expect(() => parseRecommendations({ profile: { user_id: 1, avg_price: "4.2", avg_nutrient_values: {} }, items: [] })).toThrow();
    expect(() => parseRecommendations({ items: [] })).toThrow();
    expect(() => parseRecommendations({ profile: null, items: [], source: "popular" })).toThrow();
  });
});

describe("buying profile display", () => {

  it("shows the nutrients it knows, in a fixed order, and skips the rest", () => {
    expect(nutrientHighlights({ sodium_mg: 400, fat_g: 3.25, energy_kcal: 120.4, protein_g: 8.06 }))
      .toEqual(["120 kcal", "8.1 g protein", "3.3 g fat"]);
  });
});
