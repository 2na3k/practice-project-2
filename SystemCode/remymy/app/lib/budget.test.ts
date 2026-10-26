import { describe, expect, it } from "vitest";
import { BUDGET_MAX, NO_BUDGET_LIMIT, normalizeBudget, validBudget } from "./budget";
import { DEFAULT_PREFERENCES, interpretRequest } from "./preferences";

describe("budget preference", () => {
  it("keeps a positive amount, rounded to cents and capped, and treats anything else as 0, no limit", () => {
    expect(NO_BUDGET_LIMIT).toBe(0);
    expect(normalizeBudget(12)).toBe(12);
    expect(normalizeBudget(7.456)).toBe(7.46);
    expect(normalizeBudget(BUDGET_MAX + 100)).toBe(BUDGET_MAX);
    for (const value of [0, -3, Number.NaN, undefined, null, "12"]) expect(normalizeBudget(value)).toBe(0);
  });

  it("accepts 0 for no limit, and preferences saved before there was a budget", () => {
    expect(validBudget(0)).toBe(true);
    expect(validBudget(15)).toBe(true);
    expect(validBudget(undefined)).toBe(true);
    expect(validBudget(null)).toBe(true);
    expect(validBudget(-1)).toBe(false);
    expect(validBudget(BUDGET_MAX + 1)).toBe(false);
  });

  it("survives interpreting a chat request, 0 included", () => {
    expect(interpretRequest("something quick", { ...DEFAULT_PREFERENCES, budget: 12 }).budget).toBe(12);
    expect(interpretRequest("something quick", { ...DEFAULT_PREFERENCES, budget: 0 }).budget).toBe(0);
    expect(interpretRequest("something quick", DEFAULT_PREFERENCES)).not.toHaveProperty("budget");
  });
});
