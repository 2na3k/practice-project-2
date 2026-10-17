import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { PUT } from "../../app/api/recipes/[id]/rating/route";

const rate = (id: string, body: unknown) =>
  PUT(
    new NextRequest(`http://remymy.test/api/recipes/${encodeURIComponent(id)}/rating`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
    { params: Promise.resolve({ id }) },
  );

describe("recipe rating route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("looks up the user's id, then saves the like with the Data API", async () => {
    const saved = { user_id: 7, recipe_id: "openrecipes:1", like: false, timestamp: "2026-10-12T09:00:00.000Z" };
    const fetchMock = vi.fn(async (url: URL) =>
      url.pathname.startsWith("/api/users/")
        ? Response.json({ id: 7, username: "demo", preferences: {} })
        : Response.json(saved));
    vi.stubGlobal("fetch", fetchMock);

    const response = await rate("openrecipes:1", { username: "demo", like: false });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(saved);
    const [lookup, put] = fetchMock.mock.calls.map(([url]) => url as URL);
    expect(lookup.href).toBe("http://localhost:8002/api/users/demo");
    expect(put.pathname).toBe("/api/recipes/openrecipes%3A1/rating");
    expect(Object.fromEntries(put.searchParams)).toEqual({ user_id: "7", like: "false" });
    expect((fetchMock.mock.calls[1] as unknown as [URL, RequestInit])[1].method).toBe("PUT");
  });

  it("passes an unknown user's 404 through without saving", async () => {
    const fetchMock = vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await rate("openrecipes:1", { username: "nobody", like: true });

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ detail: "User not found." });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects a body without a username and a true or false like", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect((await rate("openrecipes:1", { username: "demo", like: "yes" })).status).toBe(400);
    expect((await rate("openrecipes:1", { like: true })).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 when the Data API cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));

    const response = await rate("openrecipes:1", { username: "demo", like: true });

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Rating service is unavailable." });
  });
});
