import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// One item of the priced catalog the recommendations come from, by its FDC id.
export async function GET(_request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^\d{1,12}$/.test(id ?? "")) {
    return NextResponse.json({ error: "Invalid catalog id." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL(`/api/catalog/${id}`, base), { cache: "no-store" });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Catalog service is unavailable." }, { status: 502 });
  }
}
