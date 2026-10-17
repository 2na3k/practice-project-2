import { describe, expect, it } from "vitest";
import { parseLikes, parseSavedFoods } from "./saved";

describe("browser-stored choices", () => {
  it("keeps the saved recipes that have an id and a title", () => {
    expect(parseSavedFoods([
      { id: "170138", title: "Tomatoes, red, ripe, canned" },
      { id: 170007, title: "Onions, welsh, raw" },
      { id: "175206" },
      null,
    ])).toEqual([{ id: "170138", title: "Tomatoes, red, ripe, canned" }]);
    expect(parseSavedFoods({ id: "170138" })).toEqual([]);
  });

  it("keeps only likes that are true or false", () => {
    expect(parseLikes({ "sesame-tofu-rice-bowl": true, "170138": false, broken: "yes" }))
      .toEqual({ "sesame-tofu-rice-bowl": true, "170138": false });
    expect(parseLikes(["sesame-tofu-rice-bowl"])).toEqual({});
    expect(parseLikes(null)).toEqual({});
  });
});
