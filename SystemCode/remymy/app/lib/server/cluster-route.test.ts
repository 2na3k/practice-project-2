import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "../../app/api/cluster/route";

const RECIPE = { id: "openrecipes:5210", title: "Roast Chicken with Carrots", ingredients: ["1 chicken"], instructions: ["Roast."], quality_status: "ok" };

const cluster = (body: unknown, origin = "http://remymy.test") =>
  POST(new NextRequest("http://remymy.test/api/cluster", { method: "POST", headers: { "content-type": "application/json", origin }, body: JSON.stringify(body) }));

describe("cluster route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays the recipe and k to the Data API", async () => {
    const fetchMock = vi.fn(async () => Response.json({ near_items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await cluster({ item: RECIPE, k: 4 });

    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.href).toBe("http://localhost:8002/api/cluster?k=4");
    // a recipe sent without tags goes on with none
    expect(JSON.parse(init.body as string)).toEqual({ item: { ...RECIPE, tags: [] } });
  });

  it("rejects a bad recipe or k and other origins, and answers 502 without the Data API", async () => {
    const fetchMock = vi.fn(async () => { throw new TypeError("fetch failed"); });
    vi.stubGlobal("fetch", fetchMock);

    expect((await cluster({ item: { title: "Roast chicken" } })).status).toBe(400);
    expect((await cluster({ item: RECIPE, k: 0 })).status).toBe(400);
    expect((await cluster({ item: RECIPE, k: 13 })).status).toBe(400);
    expect((await cluster({ item: RECIPE }, "http://evil.test")).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await cluster({ item: RECIPE })).status).toBe(502);
  });
});
