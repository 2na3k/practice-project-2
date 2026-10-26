import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSaved, parseSavedFoods, saveItem, unsaveItem } from "./saved";

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

  it("keeps whether a saved one is a recipe or a food, and drops any other kind", () => {
    expect(parseSavedFoods([
      { id: "openrecipes:5210", title: "Roast Chicken with Carrots", kind: "recipe" },
      { id: "170138", title: "Tomatoes, red, ripe, canned", kind: "food" },
      { id: "175206", title: "Onions", kind: "meal" },
    ])).toEqual([
      { id: "openrecipes:5210", title: "Roast Chicken with Carrots", kind: "recipe" },
      { id: "170138", title: "Tomatoes, red, ripe, canned", kind: "food" },
    ]);
  });

});

describe("saved recipes in the Data API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists them with their kind, showing the id of one without a title", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([
      { recipe_id: "170138", kind: "food", title: "Tomatoes, red, ripe, canned", saved_at: "2026-10-22T00:00:00Z" },
      { recipe_id: "openrecipes:5210", kind: "recipe", title: null, saved_at: "2026-10-21T00:00:00Z" },
      { recipe_id: "x", kind: "meal", title: "Not one", saved_at: "2026-10-20T00:00:00Z" },
    ])));

    expect(await fetchSaved("demo")).toEqual([
      { id: "170138", title: "Tomatoes, red, ripe, canned", kind: "food" },
      { id: "openrecipes:5210", title: "openrecipes:5210", kind: "recipe" },
    ]);
  });

  it("saves one, answering null for a recipe the database does not know", async () => {
    const fetchMock = vi.fn(async (url: string) => url.endsWith("openrecipes%3A999")
      ? Response.json({ detail: "Recipe not found." }, { status: 404 })
      : Response.json({ recipe_id: "openrecipes:1", kind: "recipe", title: "Tomato Soup", saved_at: "2026-10-22T00:00:00Z" }));
    vi.stubGlobal("fetch", fetchMock);

    expect(await saveItem("demo", "openrecipes:1")).toEqual({ id: "openrecipes:1", title: "Tomato Soup", kind: "recipe" });
    expect(await saveItem("demo", "openrecipes:999")).toBeNull();
    expect(fetchMock.mock.calls[0]).toEqual(["/api/users/demo/saved/openrecipes%3A1", expect.objectContaining({ method: "PUT" })]);
  });

  it("says why a save or an unsave failed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 })));

    await expect(saveItem("nobody", "openrecipes:1")).rejects.toThrow("User not found.");
    await expect(unsaveItem("nobody", "openrecipes:1")).rejects.toThrow("User not found.");
    await expect(fetchSaved("nobody")).rejects.toThrow("User not found.");
  });
});
