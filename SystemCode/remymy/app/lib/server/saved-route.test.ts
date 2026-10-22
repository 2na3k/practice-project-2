import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../../app/api/users/[username]/saved/route";
import { DELETE, PUT } from "../../app/api/users/[username]/saved/[id]/route";

const SAVED = [{ recipe_id: "openrecipes:5210", kind: "recipe", title: "Roast Chicken with Carrots", saved_at: "2026-10-22T00:00:00Z" }];

const list = (username: string) =>
  GET(new NextRequest(`http://remymy.test/api/users/${encodeURIComponent(username)}/saved`), {
    params: Promise.resolve({ username }),
  });

const change = (method: "PUT" | "DELETE", username: string, id: string, origin?: string) => {
  const request = new NextRequest(`http://remymy.test/api/users/${encodeURIComponent(username)}/saved/${encodeURIComponent(id)}`, {
    method,
    headers: origin ? { origin } : {},
  });
  const context = { params: Promise.resolve({ username, id }) };
  return method === "PUT" ? PUT(request, context) : DELETE(request, context);
};

describe("saved recipes routes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays the Data API's saved recipes for the user", async () => {
    const fetchMock = vi.fn(async () => Response.json(SAVED));
    vi.stubGlobal("fetch", fetchMock);

    const response = await list("demo");

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(SAVED);
    expect(((fetchMock.mock.calls[0] as unknown as [URL])[0]).href).toBe("http://localhost:8002/api/users/demo/saved");
  });

  it("saves and unsaves a recipe by its id", async () => {
    const fetchMock = vi.fn(async (_url: URL, init?: RequestInit) =>
      init?.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json(SAVED[0]));
    vi.stubGlobal("fetch", fetchMock);

    const saved = await change("PUT", "demo", "openrecipes:5210");
    const removed = await change("DELETE", "demo", "openrecipes:5210");

    expect(saved.status).toBe(200);
    expect(await saved.json()).toEqual(SAVED[0]);
    expect(removed.status).toBe(204);
    const calls = fetchMock.mock.calls as unknown as [URL, RequestInit][];
    expect(calls.map(([url, init]) => [init.method, url.href])).toEqual([
      ["PUT", "http://localhost:8002/api/users/demo/saved/openrecipes%3A5210"],
      ["DELETE", "http://localhost:8002/api/users/demo/saved/openrecipes%3A5210"],
    ]);
  });

  it("passes on the Data API's 404 for a recipe it does not know", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Recipe not found." }, { status: 404 })));

    const response = await change("PUT", "demo", "openrecipes:999");

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ detail: "Recipe not found." });
  });

  it("rejects another site, a bad username or id, and answers 502 without the Data API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));

    expect((await change("PUT", "demo", "openrecipes:1", "https://elsewhere.test")).status).toBe(403);
    expect((await change("PUT", "a/b", "openrecipes:1")).status).toBe(400);
    expect((await change("DELETE", "demo", "a/b")).status).toBe(400);
    expect((await list("a/b")).status).toBe(400);
    expect((await list("demo")).status).toBe(502);
    const response = await change("PUT", "demo", "openrecipes:1");
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Saved recipes service is unavailable." });
  });
});
