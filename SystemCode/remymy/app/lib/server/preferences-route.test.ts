import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { PUT } from "../../app/api/users/[username]/preferences/route";

const save = (username: string, body: string) =>
  PUT(new NextRequest(`http://remymy.test/api/users/${encodeURIComponent(username)}/preferences`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body,
  }), { params: Promise.resolve({ username }) });

const PREFERENCES = { special_diet: null, cuisines: [], preferred_nutrient: null, budget: 0, allergies: ["peanut"] };

describe("user preferences route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays the preferences to the Data API's set-preferences", async () => {
    const fetchMock = vi.fn(async () => Response.json({ id: 1, username: "demo", preferences: PREFERENCES }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await save("demo", JSON.stringify(PREFERENCES));

    expect(response.status).toBe(200);
    expect((await response.json()).preferences).toEqual(PREFERENCES);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.href).toBe("http://localhost:8002/api/users/demo/set-preferences");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual(PREFERENCES);
  });

  it("passes on the Data API's rejection of unknown allergens", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: [{ msg: "Input should be 'peanut'" }] }, { status: 422 })));

    expect((await save("demo", JSON.stringify({ ...PREFERENCES, allergies: ["nuts"] }))).status).toBe(422);
  });

  it("rejects a bad username or body, and answers 502 without the Data API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));

    expect((await save("a/b", JSON.stringify(PREFERENCES))).status).toBe(400);
    expect((await save("demo", "[1]")).status).toBe(400);
    expect((await save("demo", "not json")).status).toBe(400);
    const response = await save("demo", JSON.stringify(PREFERENCES));
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Preferences service is unavailable." });
  });
});
