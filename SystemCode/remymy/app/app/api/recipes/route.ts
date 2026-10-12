import { NextRequest, NextResponse } from "next/server";

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
