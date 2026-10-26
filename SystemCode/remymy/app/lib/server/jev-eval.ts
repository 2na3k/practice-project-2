import { evaluateWithJev, type JevAnswer, type JevQuestion, type JevState } from "./jev-client";

/** Metric questions keyed by Langfuse score name. Filled from the metrics report. */
export const JEV_METRICS: Record<string, JevQuestion> = {};

const langfuseAuth = (): string =>
  `Basic ${Buffer.from(`${process.env.LANGFUSE_PUBLIC_KEY}:${process.env.LANGFUSE_SECRET_KEY}`).toString("base64")}`;

/** One numeric value per answer: noul probability, score position, or the chosen option's probability. */
const scoreValue = (answer: JevAnswer): number =>
  answer.type === "noul"
    ? answer.noul
    : answer.type === "score"
      ? answer.score
      : answer.probabilities[answer.choice] ?? 0;

async function postScore(traceId: string, name: string, value: number): Promise<void> {
  const response = await fetch(`${process.env.LANGFUSE_BASE_URL}/api/public/scores`, {
    method: "POST",
    headers: { Authorization: langfuseAuth(), "Content-Type": "application/json" },
    body: JSON.stringify({ traceId, name, value, dataType: "NUMERIC", source: "API" }),
  });
  if (!response.ok) throw new Error(`Langfuse score ${name} failed with HTTP ${response.status}.`);
}

/**
 * Runs JEV metrics for one finished request and posts the results as Langfuse scores.
 * Fire-and-forget: the caller does not await it, so it adds no latency to the response.
 */
export function scheduleJevEval(state: JevState, traceId: string | undefined): void {
  if (!traceId || Object.keys(JEV_METRICS).length === 0) return;
  void (async () => {
    const response = await evaluateWithJev(state, JEV_METRICS);
    await Promise.all(
      Object.entries(response.answers).map(([name, answer]) => postScore(traceId, name, scoreValue(answer))),
    );
  })().catch((error: unknown) => {
    console.error("JEV eval failed", error instanceof Error ? error.message : "unknown error");
  });
}
