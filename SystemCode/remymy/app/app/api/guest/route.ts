import { NextRequest, NextResponse } from "next/server";

const DEFAULT_DATA_API_URL = "http://localhost:8002";
const GUEST_COOKIE = "remymy-guest";
const GUEST_USERNAME = /^guest-[0-9a-f]{32}$/;
const ONE_YEAR = 60 * 60 * 24 * 365;

// This browser's guest user. The Data API creates one on the first visit, and the HTTP-only cookie keeps it after that.
export async function GET(request: NextRequest) {
  const base = process.env.DATA_API_URL ?? DEFAULT_DATA_API_URL;
  try {
    const existing = request.cookies.get(GUEST_COOKIE)?.value;
    if (existing && GUEST_USERNAME.test(existing)) {
      const check = await fetch(new URL(`/api/users/${existing}`, base), { cache: "no-store" });
      if (check.ok) return NextResponse.json({ username: existing });
    }
    const created = await fetch(new URL("/api/guests", base), { method: "POST", cache: "no-store" });
    if (!created.ok) return NextResponse.json({ error: "The guest user could not be created." }, { status: 502 });
    const user: unknown = await created.json();
    const username = user && typeof user === "object" && "username" in user ? (user as { username: unknown }).username : undefined;
    if (typeof username !== "string" || !GUEST_USERNAME.test(username)) {
      return NextResponse.json({ error: "The guest user returned an unexpected response." }, { status: 502 });
    }
    const response = NextResponse.json({ username });
    response.cookies.set(GUEST_COOKIE, username, {
      httpOnly: true,
      sameSite: "strict",
      path: "/",
      maxAge: ONE_YEAR,
      secure: request.nextUrl.protocol === "https:",
    });
    return response;
  } catch {
    return NextResponse.json({ error: "Guest service is unavailable." }, { status: 502 });
  }
}
