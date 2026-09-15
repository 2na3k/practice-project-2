import { describe, expect, it } from "vitest";
import {
  errorResponse,
  isSameOriginMutation,
  MAX_JSON_BODY_BYTES,
  readJsonBody,
  RequestGuardError,
} from "./request-guards";

const request = (body?: BodyInit, headers?: HeadersInit): Request =>
  new Request("https://remymy.test/api/chat", {
    method: "POST",
    body,
    headers: { "content-type": "application/json", ...headers },
  });

describe("request guards", () => {
  it("accepts bounded JSON and rejects malformed or oversized bodies", async () => {
    await expect(readJsonBody(request(JSON.stringify({ ok: true })))).resolves.toEqual({
      ok: true,
    });
    await expect(readJsonBody(request("not-json"))).rejects.toMatchObject({
      status: 400,
      message: "Request body must be valid JSON.",
    });
    await expect(readJsonBody(request("   "))).rejects.toMatchObject({
      status: 400,
      message: "Request body must be JSON.",
    });

    const tooLarge = "x".repeat(MAX_JSON_BODY_BYTES);
    await expect(readJsonBody(request(JSON.stringify({ tooLarge })))).rejects.toMatchObject({
      status: 413,
    });
  });

  it("rejects cross-origin mutation headers while allowing same-origin requests", () => {
    expect(
      isSameOriginMutation(request("{}", { origin: "https://remymy.test" }) as never),
    ).toBe(true);
    expect(
      isSameOriginMutation(request("{}", { origin: "https://evil.test" }) as never),
    ).toBe(false);
    expect(
      isSameOriginMutation(
        request("{}", { referer: "https://evil.test/page" }) as never,
      ),
    ).toBe(false);
  });

  it("allows localhost when Next builds the request URL from 0.0.0.0", () => {
    const localRequest = new Request("http://0.0.0.0:3100/api/connection", {
      method: "POST",
      headers: {
        origin: "http://localhost:3100",
        referer: "http://localhost:3100/",
        host: "localhost:3100",
      },
    });

    expect(isSameOriginMutation(localRequest as never)).toBe(true);
  });

  it("turns guard errors into safe, compact responses", async () => {
    const response = errorResponse(new RequestGuardError("bad payload", 422));
    expect(response.status).toBe(422);
    await expect(response.json()).resolves.toEqual({ error: "bad payload" });

    const fallback = errorResponse(new Error("secret provider failure"));
    expect(fallback.status).toBe(500);
    await expect(fallback.json()).resolves.toEqual({
      error: "The request could not be completed.",
    });
  });
});
