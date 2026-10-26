// The budget preference: the most to spend on one meal, in SGD. 0 is no limit, as in the Data API's preferences.

export const NO_BUDGET_LIMIT = 0;

// The budget slider goes up to BUDGET_SLIDER_MAX; the number input takes anything up to BUDGET_MAX.
export const BUDGET_SLIDER_MAX = 50;
export const BUDGET_MAX = 500;

// A budget as preferences keep it: a positive amount up to BUDGET_MAX, rounded to cents, or 0 for no limit.
export function normalizeBudget(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return NO_BUDGET_LIMIT;
  return Math.round(Math.min(value, BUDGET_MAX) * 100) / 100;
}

// 0 to BUDGET_MAX; preferences saved before there was a budget have none, or null from before 0 meant no limit.
export function validBudget(value: unknown): boolean {
  return value === undefined || value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= BUDGET_MAX);
}
