import { afterEach, describe, expect, it, vi } from "vitest";
import { dataApiError, fetchRecipeLikes, parseRecipeDetail, parseRecipePage, parseRecipeRating, saveRecipeRating } from "./recipes";

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

describe("recipe ratings", () => {
  afterEach(() => vi.unstubAllGlobals());

  const saved = { user_id: 1, recipe_id: "openrecipes:1", like: true, timestamp: "2026-10-12T09:00:00.000Z" };

  it("accepts a saved like and rejects other shapes", () => {
    expect(parseRecipeRating(saved).like).toBe(true);
    expect(() => parseRecipeRating({ ...saved, like: "yes" })).toThrow();
    expect(() => parseRecipeRating({ ...saved, rating: 5, like: undefined })).toThrow();
  });

  it("reads the Data API's detail, then the app's error, then the fallback", () => {
    expect(dataApiError({ detail: "Recipe not found." }, "fallback")).toBe("Recipe not found.");
    expect(dataApiError({ error: "Invalid recipe id." }, "fallback")).toBe("Invalid recipe id.");
    expect(dataApiError(null, "fallback")).toBe("fallback");
  });

  it("sends the username and like to the app's rating route", async () => {
    const fetchMock = vi.fn(async () => Response.json(saved));
    vi.stubGlobal("fetch", fetchMock);

    await expect(saveRecipeRating("openrecipes:1", "demo", true)).resolves.toEqual(saved);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/recipes/openrecipes%3A1/rating");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ username: "demo", like: true });
  });

  it("throws the API's reason when the like is not saved", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 })));
    await expect(saveRecipeRating("openrecipes:1", "nobody", false)).rejects.toThrow("User not found.");
  });
});

describe("recipe likes from the database", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the user's likes through the app's route", async () => {
    const fetchMock = vi.fn(async () => Response.json({ "170138": true, "openrecipes:1": false }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchRecipeLikes("demo")).resolves.toEqual({ "170138": true, "openrecipes:1": false });
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/users/demo/likes");
  });

  it("rejects a response that is not id to true or false, and passes on the API's reason", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ "170138": "yes" })));
    await expect(fetchRecipeLikes("demo")).rejects.toThrow("unexpected response");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 })));
    await expect(fetchRecipeLikes("nobody")).rejects.toThrow("User not found.");
  });
});

