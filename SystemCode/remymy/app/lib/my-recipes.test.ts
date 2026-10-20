import { describe, expect, it } from "vitest";
import { normalizeTag, parseNutrients, parseTagSuggestion, splitLines, tagText, validateNewRecipe } from "./my-recipes";

const RECIPE = {
  title: "Grandma's chicken curry",
  ingredients: ["500 g chicken thighs", "1 onion, chopped"],
  instructions: ["Brown the chicken.", "Simmer for 30 minutes."],
  tags: [{ label: "hearty_filling", source: "classifier" }, { label: "family_favourite", source: "user" }],
  nutrients: { energy_kcal: 180 },
};

describe("my recipes", () => {
  it("turns typed tags into the classifier's snake_case form", () => {
    expect(normalizeTag("  Family Favourite! ")).toBe("family_favourite");
    expect(normalizeTag("Grandma's  curry")).toBe("grandma_s_curry");
    expect(normalizeTag("!!!")).toBeNull();
    expect(normalizeTag("x".repeat(39) + " y")).toBe("x".repeat(39));
    expect(tagText("dessert_sweet_treat")).toBe("Dessert sweet treat");
  });

  it("keeps one trimmed line per ingredient or step", () => {
    expect(splitLines(" 2 eggs \r\n\n  1 cup flour\n   ")).toEqual(["2 eggs", "1 cup flour"]);
  });

  it("reads the nutrients filled in and names the ones that are not numbers", () => {
    expect(parseNutrients({ energy_kcal: "273", protein_g: " 6.5 ", fat_g: "", sugars_g: "lots", sodium_mg: "-1" }))
      .toEqual({ nutrients: { energy_kcal: 273, protein_g: 6.5 }, invalid: ["sugars_g", "sodium_mg"] });
  });

  it("accepts a recipe the Data API accepts, with the title trimmed", () => {
    const checked = validateNewRecipe({ ...RECIPE, title: "  Curry  " });
    expect(checked).toEqual({ recipe: { ...RECIPE, title: "Curry" } });
    expect(validateNewRecipe({ title: "Toast", ingredients: ["bread"] }))
      .toEqual({ recipe: { title: "Toast", ingredients: ["bread"], instructions: [], tags: [], nutrients: {} } });
  });

  it.each([
    [{ title: " " }, "title"],
    [{ ingredients: [] }, "ingredients"],
    [{ ingredients: ["  "] }, "ingredients"],
    [{ instructions: ["ok", 3] }, "steps"],
    [{ tags: [{ label: "Hearty Filling", source: "user" }] }, "tags"],
    [{ tags: [{ label: "easy", source: "chef" }] }, "tags"],
    [{ tags: [{ label: "easy", source: "user" }, { label: "easy", source: "classifier" }] }, "tag once"],
    [{ nutrients: { vitamin_c_mg: 2 } }, "Nutrients"],
    [{ nutrients: { energy_kcal: "180" } }, "Nutrients"],
  ])("rejects %j", (change, word) => {
    const checked = validateNewRecipe({ ...RECIPE, ...change });
    expect("error" in checked && checked.error).toContain(word);
  });

  it("reads the classifier's labels, scores and model", () => {
    const body = { labels: ["easy"], scores: { easy: 0.9, generic: 0.1 }, model: "logistic regression (C=0.1)", item: {} };
    expect(parseTagSuggestion(body)).toEqual({ labels: ["easy"], scores: { easy: 0.9, generic: 0.1 }, model: "logistic regression (C=0.1)" });
    expect(() => parseTagSuggestion({ labels: "easy" })).toThrow("unexpected response");
  });
});
