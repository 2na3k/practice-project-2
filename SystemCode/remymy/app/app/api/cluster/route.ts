import { NextRequest, NextResponse } from "next/server";
import { parseRecipeDetail } from "../../../lib/recipes";
import { MAX_SIMILAR } from "../../../lib/catalog";
import { errorResponse, isRecord, isSameOriginMutation, readJsonBody } from "../../../lib/server/request-guards";

const DEFAULT_DATA_API_URL = "http://localhost:8002";

// The catalog foods nearest a recipe in its recipe cluster, from the Data API (POST /api/cluster): {item: the recipe, k}.
export async function POST(request: NextRequest) {
  if (!isSameOriginMutation(request)) return NextResponse.json({ error: "Cross-origin requests are not allowed." }, { status: 403 });
  let body: unknown;
  try {
    body = await readJsonBody(request);
  } catch (error) {
    return errorResponse(error, 400);
  }
  const { item, k = 6 } = isRecord(body) ? body : {};
  let recipe;
  try {
    recipe = parseRecipeDetail(item);
  } catch {
    recipe = null;
  }
  if (!recipe || typeof k !== "number" || !Number.isInteger(k) || k < 1 || k > MAX_SIMILAR) {
    return NextResponse.json({ error: `Send a recipe and k from 1 to ${MAX_SIMILAR}.` }, { status: 400 });
  }
  try {
    const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
    const url = new URL("/api/cluster", base);
    url.searchParams.set("k", String(k));
    const response = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ item: recipe }),
      cache: "no-store",
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "Clustering service is unavailable." }, { status: 502 });
  }
}
