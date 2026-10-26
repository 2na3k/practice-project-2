import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../../app/api/catalog/[id]/route";

const item = (id: string) =>
  GET(new NextRequest(`http://remymy.test/api/catalog/${encodeURIComponent(id)}`), { params: Promise.resolve({ id }) });

describe("catalog item route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays one item from the Data API", async () => {
    const fetchMock = vi.fn(async () => Response.json({ fdc_id: 170005 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await item("170005");

    expect(response.status).toBe(200);
    expect(((fetchMock.mock.calls[0] as unknown as [URL])[0]).href).toBe("http://localhost:8002/api/recipe/170005");
  });

  it("accepts only FDC ids, and answers 502 without the Data API", async () => {
    const fetchMock = vi.fn(async () => { throw new TypeError("fetch failed"); });
    vi.stubGlobal("fetch", fetchMock);

    expect((await item("openrecipes:1")).status).toBe(400);
    expect((await item("../health")).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await item("170005")).status).toBe(502);
  });
});
