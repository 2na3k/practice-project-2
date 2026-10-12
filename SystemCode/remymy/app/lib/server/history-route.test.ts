import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../../app/api/users/[username]/history/route";

const history = (username: string, query = "") =>
  GET(new NextRequest(`http://remymy.test/api/users/${encodeURIComponent(username)}/history${query}`), {
    params: Promise.resolve({ username }),
  });

describe("user history route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays the Data API's history and its limit", async () => {
    const rows = [{ recipe_id: "170005", kind: "food", title: "Onions", like: true, timestamp: "2026-10-12T15:16:45Z" }];
    const fetchMock = vi.fn(async () => Response.json(rows));
    vi.stubGlobal("fetch", fetchMock);

    const response = await history("demo", "?limit=200");

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(rows);
    expect(((fetchMock.mock.calls[0] as unknown as [URL])[0]).href).toBe("http://localhost:8002/api/users/demo/history?limit=200");
  });

  it("rejects a username with a slash and answers 502 without the Data API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));

    expect((await history("a/b")).status).toBe(400);
    const response = await history("demo");
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "History service is unavailable." });
  });
});
