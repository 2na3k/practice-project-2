import { describe, expect, it } from "vitest";
import { parseRecipeDetail, parseRecipePage } from "./recipes";

describe("recipe API contracts", () => {
  it("accepts the narrow list response", () => {
    expect(parseRecipePage({
      items: [{ id: "openrecipes:1", title: "Soup", ingredient_count: 2, ingredient_preview: ["water", "salt"], quality_status: "usable" }],
      total: 1, limit: 20, offset: 0,
    }).items[0].title).toBe("Soup");
  });

  it("rejects fields with the wrong shape", () => {
    expect(() => parseRecipePage({ items: [{ id: "1" }], total: 1, limit: 20, offset: 0 })).toThrow();
  });

  it("accepts recipe detail without inventing metadata", () => {
    expect(parseRecipeDetail({ id: "1", title: "Soup", ingredients: ["water"], instructions: ["Boil."], quality_status: "usable" }).instructions).toEqual(["Boil."]);
  });
});
