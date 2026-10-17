import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

function invalidSegment(value: unknown): boolean {
  return typeof value !== "string" || !value || value.length > 200 || value.includes("/");
}

async function relay(response: Response): Promise<NextResponse> {
  return new NextResponse(await response.text(), {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

// The app knows the user by username; the Data API's rating route takes the user_id, so this looks it up first.
export async function PUT(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (invalidSegment(id)) {
    return NextResponse.json({ error: "Invalid recipe id." }, { status: 400 });
  }
  const body: unknown = await request.json().catch(() => null);
  const { username, like } = (body && typeof body === "object" ? body : {}) as { username?: unknown; like?: unknown };
  if (invalidSegment(username) || typeof like !== "boolean") {
    return NextResponse.json({ error: "Send a username and like: true or false." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const user = await fetch(new URL(`/api/users/${encodeURIComponent(username as string)}`, base), { cache: "no-store" });
    if (!user.ok) return relay(user);
    const { id: userId } = (await user.json()) as { id: number };
    const url = new URL(`/api/recipes/${encodeURIComponent(id)}/rating`, base);
    url.searchParams.set("user_id", String(userId));
    url.searchParams.set("like", String(like));
    return relay(await fetch(url, { method: "PUT", cache: "no-store" }));
  } catch {
    return NextResponse.json({ error: "Rating service is unavailable." }, { status: 502 });
  }
}
