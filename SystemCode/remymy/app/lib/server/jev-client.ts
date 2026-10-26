/** Minimal client for TypeSafe JEV (`POST /v1/systemone`). Shapes follow docs.typesafe.ai/api. */

const JEV_URL = process.env.JEV_URL ?? "https://api.typesafe.ai/v1/systemone";
const JEV_MODEL = process.env.JEV_MODEL ?? "jev-latest";
const JEV_TIMEOUT_MS = 15_000;

export type JevState = string | Record<string, unknown> | unknown[];
export type JevText = string | Record<string, unknown> | unknown[] | null;

export type JevQuestion =
  | { type: "noul"; instructions?: JevText; criteria?: { true?: JevText; false?: JevText } | null }
  | { type: "choice"; instructions?: JevText; criteria: Record<string, JevText> }
  | { type: "score"; instructions: JevText; criteria: JevText[] };

export type JevAnswer =
  | { type: "noul"; noul: number }
  | { type: "choice"; choice: string; confidence: number; probabilities: Record<string, number> }
  | { type: "score"; score: number; confidence: number; legend: Record<string, string>; probabilities: Record<string, number> };

export type JevResponse = {
  model: string;
  answers: Record<string, JevAnswer>;
  usage: { input_tokens: number; output_tokens: number };
};

export class JevError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "JevError";
  }
}

/** Run one JEV evaluation. Throws JevError on HTTP failure or missing key. */
export async function evaluateWithJev(
  state: JevState,
  questions: Record<string, JevQuestion>,
  signal?: AbortSignal,
): Promise<JevResponse> {
  const apiKey = process.env.TYPESAFE_API_KEY || process.env.JEV_API_KEY;
  if (!apiKey) throw new JevError(0, "JEV API key is not set (TYPESAFE_API_KEY or JEV_API_KEY).");
  const timeout = AbortSignal.timeout(JEV_TIMEOUT_MS);
  const response = await fetch(JEV_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model: JEV_MODEL, state, questions }),
    redirect: "error",
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok) {
    // Only the status is surfaced: bodies can echo request content.
    throw new JevError(response.status, `JEV request failed with HTTP ${response.status}.`);
  }
  return (await response.json()) as JevResponse;
}
