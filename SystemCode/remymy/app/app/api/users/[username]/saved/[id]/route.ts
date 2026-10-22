import { NextRequest, NextResponse } from "next/server";
import { isSameOriginMutation } from "../../../../../../lib/server/request-guards";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

type Context = { params: Promise<{ username: string; id: string }> };

function invalidSegment(value: string): boolean {
  return !value || value.length > 200 || value.includes("/");
}

// Save a recipe for the user (PUT) or take it out of their saved ones (DELETE), in the Data API's database: a Food.com
// recipe, a catalog food by its fdc_id, or one of their own.
async function relay(request: NextRequest, context: Context, method: "PUT" | "DELETE"): Promise<NextResponse> {
  if (!isSameOriginMutation(request)) return NextResponse.json({ error: "Cross-origin requests are not allowed." }, { status: 403 });
  const { username, id } = await context.params;
  if (invalidSegment(username)) return NextResponse.json({ error: "Invalid username." }, { status: 400 });
  if (invalidSegment(id)) return NextResponse.json({ error: "Invalid recipe id." }, { status: 400 });
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const url = new URL(`/api/users/${encodeURIComponent(username)}/saved/${encodeURIComponent(id)}`, base);
    const response = await fetch(url, { method, cache: "no-store" });
    // a 204 has no body to pass on
    if (response.status === 204) return new NextResponse(null, { status: 204 });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Saved recipes service is unavailable." }, { status: 502 });
  }
}

export const PUT = (request: NextRequest, context: Context) => relay(request, context, "PUT");
export const DELETE = (request: NextRequest, context: Context) => relay(request, context, "DELETE");
