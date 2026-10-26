import { describe, expect, it } from "vitest";
import { normalizeAllergies, validAllergies } from "./allergens";
import { DEFAULT_PREFERENCES, interpretRequest } from "./preferences";
import { toDataApiPreferences } from "./user-preferences";

describe("allergies", () => {
  it("accepts known allergen codes, or none from before there were allergies", () => {
    expect(validAllergies(["peanut", "shellfish"])).toBe(true);
    expect(validAllergies(undefined)).toBe(true);
    expect(validAllergies(null)).toBe(true);
    expect(validAllergies(["nuts"])).toBe(false);
    expect(validAllergies("peanut")).toBe(false);
  });

  it("keeps each known code once, in the list's order", () => {
    expect(normalizeAllergies(["sesame", "nuts", "peanut", "sesame"])).toEqual(["peanut", "sesame"]);
    expect(normalizeAllergies(null)).toEqual([]);
  });

  it("are kept when a chat turn is interpreted", () => {
    expect(interpretRequest("something quick", { ...DEFAULT_PREFERENCES, allergies: ["egg"] }).allergies).toEqual(["egg"]);
    expect(interpretRequest("something quick", DEFAULT_PREFERENCES)).not.toHaveProperty("allergies");
  });
});

describe("the Data API's preferences", () => {
  it("map the app's diet, cuisine, goal, budget and allergies", () => {
    expect(toDataApiPreferences({
      ...DEFAULT_PREFERENCES, diet: "halal", cuisine: "Thai", goal: "protein", budget: 12.5, allergies: ["milk", "peanut"],
    })).toEqual({
      special_diet: "halal", cuisines: ["Thai"], preferred_nutrient: "protein", budget: 12.5, allergies: ["peanut", "milk"],
    });
  });

  it("send anything, any cuisine and no budget as none, and a vegan as vegetarian", () => {
    expect(toDataApiPreferences(DEFAULT_PREFERENCES)).toEqual({
      special_diet: null, cuisines: [], preferred_nutrient: null, budget: 0, allergies: [],
    });
    expect(toDataApiPreferences({ ...DEFAULT_PREFERENCES, diet: "vegan", goal: "light" })).toMatchObject({
      special_diet: "vegetarian", preferred_nutrient: null,
    });
  });
});
