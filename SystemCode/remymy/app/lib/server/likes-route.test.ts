import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "../../app/api/users/[username]/likes/route";

const likes = (username: string) =>
  GET(new NextRequest(`http://remymy.test/api/users/${encodeURIComponent(username)}/likes`), {
    params: Promise.resolve({ username }),
  });

describe("user likes route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("relays the Data API's likes for the user", async () => {
    const fetchMock = vi.fn(async () => Response.json({ "170138": true }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await likes("demo");

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ "170138": true });
    expect(((fetchMock.mock.calls[0] as unknown as [URL])[0]).href).toBe("http://localhost:8002/api/users/demo/likes");
  });

  it("rejects a username with a slash and answers 502 without the Data API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));

    expect((await likes("a/b")).status).toBe(400);
    const response = await likes("demo");
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Likes service is unavailable." });
  });
});
