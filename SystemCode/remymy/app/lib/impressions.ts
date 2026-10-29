/** Record the ordered recommendations shown in a successful chat, without delaying its response. */
export function logImpression(username: string | null, traceId: string | null | undefined, recipeIds: string[]): void {
  if (!username || recipeIds.length === 0) return;
  void fetch("/api/impressions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ recipe_ids: recipeIds, trace_id: traceId ?? null }),
  }).catch(() => undefined);
}
