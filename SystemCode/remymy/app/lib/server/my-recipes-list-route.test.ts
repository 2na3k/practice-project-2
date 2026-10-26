import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../../app/api/users/[username]/recipes/route";
import { fetchMyRecipes } from "../my-recipes";

const STORED = {
  id: "user-recipe:1", user_id: 1, title: "Grandma's chicken curry", ingredients: ["500 g chicken thighs"],
  instructions: ["Brown the chicken."], tags: [{ label: "hearty_filling", source: "classifier" }],
  nutrients: { energy_kcal: 180 }, created_at: "2026-10-26T04:00:00.000Z",
};

const recipes = (username: string) =>
  GET(new NextRequest(`http://remymy.test/api/users/${encodeURIComponent(username)}/recipes`), {
    params: Promise.resolve({ username }),
  });

describe("user recipes route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays the Data API's recipes of the user", async () => {
    const fetchMock = vi.fn(async () => Response.json([STORED]));
    vi.stubGlobal("fetch", fetchMock);

    const response = await recipes("demo");

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([STORED]);
    expect(((fetchMock.mock.calls[0] as unknown as [URL])[0]).href).toBe("http://localhost:8002/api/users/demo/recipes");
  });

  it("rejects a username with a slash, passes a 404 through and answers 502 without the Data API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 })));
    expect((await recipes("a/b")).status).toBe(400);
    expect((await recipes("nobody")).status).toBe(404);

    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    const response = await recipes("demo");
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Recipe service is unavailable." });
  });

  it("reads them in the browser, and refuses a malformed answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([STORED])));
    expect(await fetchMyRecipes("demo")).toEqual([STORED]);

    vi.stubGlobal("fetch", vi.fn(async () => Response.json([{ ...STORED, ingredients: [] }])));
    await expect(fetchMyRecipes("demo")).rejects.toThrow("unexpected response");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 })));
    await expect(fetchMyRecipes("nobody")).rejects.toThrow("User not found.");
  });
});
