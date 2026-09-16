import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://api:8002";

export async function GET(_request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!id || id.length > 200 || id.includes("/")) {
    return NextResponse.json({ error: "Invalid recipe id." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL(`/api/recipes/${encodeURIComponent(id)}`, base), { cache: "no-store" });
    const body = await response.text();
    return new NextResponse(body, {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Recipe service is unavailable." }, { status: 502 });
  }
}
