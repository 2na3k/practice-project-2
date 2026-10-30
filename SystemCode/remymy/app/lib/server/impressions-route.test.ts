import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "../../app/api/impressions/route";

const post = (body: unknown, cookie?: string) => {
  const request = new NextRequest("http://remymy.test/api/impressions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (cookie) request.cookies.set("remymy-guest", cookie);
  return POST(request);
};

describe("impressions route", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("proxies the ordered ids and trace id for the guest cookie", async () => {
    const fetchMock = vi.fn(async () => Response.json({ event_id: "event-1" }, { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await post({ recipe_ids: ["foodcom:9", "foodcom:2"], trace_id: "trace-456" }, "guest-1234567890abcdef1234567890abcdef");

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ event_id: "event-1" });
    expect(fetchMock).toHaveBeenCalledWith(
      new URL("http://localhost:8002/api/users/guest-1234567890abcdef1234567890abcdef/impressions"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ recipe_ids: ["foodcom:9", "foodcom:2"], trace_id: "trace-456" }),
      }),
    );
  });
});
