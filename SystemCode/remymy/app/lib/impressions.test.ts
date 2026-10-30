import { afterEach, describe, expect, it, vi } from "vitest";
import { logImpression } from "./impressions";

describe("logImpression", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the ordered recipe ids and trace id without blocking", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = logImpression("guest-123", "trace-456", ["foodcom:9", "foodcom:2"]);

    expect(result).toBeUndefined();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledWith("/api/impressions", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "content-type": "application/json" }),
      body: JSON.stringify({ recipe_ids: ["foodcom:9", "foodcom:2"], trace_id: "trace-456" }),
    }));
  });

  it("sends a null trace id and swallows fetch errors", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => { throw new TypeError("offline"); });
    vi.stubGlobal("fetch", fetchMock);

    expect(() => logImpression("guest-123", null, ["foodcom:9"])).not.toThrow();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      body: JSON.stringify({ recipe_ids: ["foodcom:9"], trace_id: null }),
    });
  });
});
