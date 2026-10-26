import { NextRequest, NextResponse } from "next/server";
import { LIMITS, validNutrients } from "@/lib/my-recipes";
import { errorResponse, isRecord, isSameOriginMutation, readJsonBody } from "@/lib/server/request-guards";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// Tags a dish with the Data API's recipe classifier (POST /api/classify): {query, nutrients per 100 g}.
export async function POST(request: NextRequest) {
  if (!isSameOriginMutation(request)) return NextResponse.json({ error: "Cross-origin requests are not allowed." }, { status: 403 });
  let body: unknown;
  try {
    body = await readJsonBody(request);
  } catch (error) {
    return errorResponse(error, 400);
  }
  const { query, nutrients = {} } = isRecord(body) ? body : {};
  if (typeof query !== "string" || !query.trim() || query.length > LIMITS.query || !validNutrients(nutrients)) {
    return NextResponse.json({ error: "Send a dish name and nutrients per 100 g as numbers." }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const response = await fetch(new URL("/api/classify", base), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: query.trim(), nutrients }),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Classification service is unavailable." }, { status: 502 });
  }
}
