import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// The routes import through the "@/" alias, which this minimal Vitest setup does not resolve (see routes.test.ts).
// @ts-expect-error Vitest supports the virtual-module option at runtime.
vi.mock("@/lib/server/request-guards", async () => import("./request-guards"), { virtual: true });
// @ts-expect-error Vitest supports the virtual-module option at runtime.
vi.mock("@/lib/my-recipes", async () => import("../my-recipes"), { virtual: true });

const { POST: createRecipe } = await import("../../app/api/recipes/route");
const { POST: classify } = await import("../../app/api/classify/route");

const RECIPE = {
  title: "Grandma's chicken curry",
  ingredients: ["500 g chicken thighs", "1 onion, chopped"],
  instructions: ["Simmer for 30 minutes."],
  tags: [{ label: "hearty_filling", source: "classifier" }, { label: "family_favourite", source: "user" }],
  nutrients: { energy_kcal: 180 },
};

const post = (path: string, body: unknown, headers: Record<string, string> = {}) =>
  new NextRequest(`http://remymy.test${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });

describe("add recipe route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("looks up the user's id, then posts the recipe to the Data API", async () => {
    const stored = { id: "user:1", user_id: 7, created_at: "2026-10-20T09:00:00Z", ...RECIPE };
    const fetchMock = vi.fn(async (url: URL) =>
      url.pathname.startsWith("/api/users/")
        ? Response.json({ id: 7, username: "demo", preferences: {} })
        : Response.json(stored, { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await createRecipe(post("/api/recipes", { username: "demo", recipe: { ...RECIPE, title: " Grandma's chicken curry " } }));

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual(stored);
    const [lookup, create] = fetchMock.mock.calls as unknown as [URL, RequestInit?][];
    expect(lookup[0].href).toBe("http://localhost:8002/api/users/demo");
    expect(create[0].href).toBe("http://localhost:8002/api/recipes");
    expect(create[1]?.method).toBe("POST");
    expect(JSON.parse(String(create[1]?.body))).toEqual({ user_id: 7, ...RECIPE });
  });

  it("passes the Data API's error through when it cannot save", async () => {
    const detail = { detail: "Recipe data is unavailable." };
    vi.stubGlobal("fetch", vi.fn(async (url: URL) =>
      url.pathname.startsWith("/api/users/") ? Response.json({ id: 7 }) : Response.json(detail, { status: 503 })));

    const response = await createRecipe(post("/api/recipes", { username: "demo", recipe: RECIPE }));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual(detail);
  });

  it("passes an unknown user's 404 through without posting", async () => {
    const fetchMock = vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await createRecipe(post("/api/recipes", { username: "nobody", recipe: RECIPE }))).status).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects an invalid recipe, a missing username and a cross-site request before calling the Data API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const noIngredients = await createRecipe(post("/api/recipes", { username: "demo", recipe: { ...RECIPE, ingredients: [] } }));
    expect(noIngredients.status).toBe(400);
    expect((await noIngredients.json()).error).toContain("ingredients");
    expect((await createRecipe(post("/api/recipes", { recipe: RECIPE }))).status).toBe(400);
    expect((await createRecipe(post("/api/recipes", { username: "demo", recipe: RECIPE }, { origin: "https://evil.test" }))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 when the Data API cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));

    const response = await createRecipe(post("/api/recipes", { username: "demo", recipe: RECIPE }));

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Recipe service is unavailable." });
  });
});

describe("classify route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("forwards the dish name and nutrients to the Data API's classifier", async () => {
    const answer = { labels: ["hearty_filling"], scores: { hearty_filling: 0.8 }, model: "logistic regression (C=0.1)" };
    const fetchMock = vi.fn(async () => Response.json(answer));
    vi.stubGlobal("fetch", fetchMock);

    const response = await classify(post("/api/classify", { query: "  Chicken curry ", nutrients: { energy_kcal: 180 } }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(answer);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.href).toBe("http://localhost:8002/api/classify");
    expect(JSON.parse(String(init.body))).toEqual({ query: "Chicken curry", nutrients: { energy_kcal: 180 } });
  });

  it("passes the classifier's 503 through", async () => {
    const detail = { detail: "Classification is unavailable: the classifier is not loaded." };
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(detail, { status: 503 })));

    const response = await classify(post("/api/classify", { query: "Chicken curry" }));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual(detail);
  });

  it("rejects a missing dish name or non-numeric nutrients", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect((await classify(post("/api/classify", { query: " " }))).status).toBe(400);
    expect((await classify(post("/api/classify", { query: "curry", nutrients: { energy_kcal: "lots" } }))).status).toBe(400);
    expect((await classify(post("/api/classify", { query: "curry", nutrients: { vitamin_c_mg: 1 } }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
