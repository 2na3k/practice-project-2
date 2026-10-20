// The budget preference: the most to spend on one meal, in SGD.

// The budget slider goes up to BUDGET_SLIDER_MAX; the number input takes anything up to BUDGET_MAX.
export const BUDGET_SLIDER_MAX = 50;
export const BUDGET_MAX = 500;

// A budget as preferences keep it: a positive amount up to BUDGET_MAX, rounded to cents, or null for no limit.
export function normalizeBudget(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return Math.round(Math.min(value, BUDGET_MAX) * 100) / 100;
}

export function validBudget(value: unknown): boolean {
  return value === undefined || value === null || (typeof value === "number" && Number.isFinite(value) && value > 0 && value <= BUDGET_MAX);
}
