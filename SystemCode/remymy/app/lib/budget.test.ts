import { describe, expect, it } from "vitest";
import { BUDGET_MAX, normalizeBudget, validBudget } from "./budget";
import { DEFAULT_PREFERENCES } from "./meals";
import { interpretRequest } from "./recommender";

describe("budget preference", () => {
  it("keeps a positive amount, rounded to cents and capped, and treats anything else as no limit", () => {
    expect(normalizeBudget(12)).toBe(12);
    expect(normalizeBudget(7.456)).toBe(7.46);
    expect(normalizeBudget(BUDGET_MAX + 100)).toBe(BUDGET_MAX);
    for (const value of [0, -3, Number.NaN, undefined, null, "12"]) expect(normalizeBudget(value)).toBeNull();
  });

  it("accepts preferences saved before there was a budget", () => {
    expect(validBudget(undefined)).toBe(true);
    expect(validBudget(null)).toBe(true);
    expect(validBudget(15)).toBe(true);
    expect(validBudget(0)).toBe(false);
    expect(validBudget(BUDGET_MAX + 1)).toBe(false);
  });

  it("survives interpreting a chat request", () => {
    expect(interpretRequest("something quick", { ...DEFAULT_PREFERENCES, budget: 12 }).budget).toBe(12);
    expect(interpretRequest("something quick", DEFAULT_PREFERENCES)).not.toHaveProperty("budget");
  });
});
