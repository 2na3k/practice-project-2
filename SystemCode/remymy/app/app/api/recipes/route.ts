import { NextRequest, NextResponse } from "next/server";
import { validateNewRecipe } from "@/lib/my-recipes";
import { errorResponse, isRecord, isSameOriginMutation, readJsonBody } from "@/lib/server/request-guards";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

function upstreamUrl(request: NextRequest): URL {
  const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
  const url = new URL("/api/recipes", base);
  const query = request.nextUrl.searchParams;
  for (const key of ["q", "limit", "offset"]) {
    const value = query.get(key);
    if (value !== null) url.searchParams.set(key, value);
  }
  return url;
}

export async function GET(request: NextRequest) {
  try {
    const response = await fetch(upstreamUrl(request), { cache: "no-store" });
    const body = await response.text();
    return new NextResponse(body, {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Recipe service is unavailable." }, { status: 502 });
  }
}

async function relay(response: Response): Promise<NextResponse> {
  return new NextResponse(await response.text(), {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

// Saves a user's own recipe. The app knows the user by username; the Data API's POST /api/recipes takes the user_id,
// so this looks it up first, as the rating route does. Its answer, the stored recipe or an error, is passed through.
export async function POST(request: NextRequest) {
  if (!isSameOriginMutation(request)) return NextResponse.json({ error: "Cross-origin requests are not allowed." }, { status: 403 });
  let body: unknown;
  try {
    body = await readJsonBody(request);
  } catch (error) {
    return errorResponse(error, 400);
  }
  const { username, recipe } = isRecord(body) ? body : {};
  if (typeof username !== "string" || !username || username.length > 200 || username.includes("/")) {
    return NextResponse.json({ error: "Send a username and a recipe." }, { status: 400 });
  }
  const checked = validateNewRecipe(recipe);
  if ("error" in checked) return NextResponse.json({ error: checked.error }, { status: 400 });
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const user = await fetch(new URL(`/api/users/${encodeURIComponent(username)}`, base), { cache: "no-store" });
    if (!user.ok) return relay(user);
    const { id: userId } = (await user.json()) as { id: number };
    return relay(await fetch(new URL("/api/recipes", base), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ user_id: userId, ...checked.recipe }),
      cache: "no-store",
    }));
  } catch {
    return NextResponse.json({ error: "Recipe service is unavailable." }, { status: 502 });
  }
}
