import { describe, expect, it } from "vitest";
import { macroAverage, ndcgAt, pairwiseAccuracy, precisionAt } from "./ranking-metrics";

describe("ranking metrics", () => {
  it("nDCG@3 is 1 for the ideal order and below 1 otherwise", () => {
    expect(ndcgAt([3, 2, 1])).toBeCloseTo(1, 6);
    // gains [1, 7, 3]: DCG 6.9163, IDCG 9.3928
    expect(ndcgAt([1, 3, 2])).toBeCloseTo(0.7363, 3);
  });

  it("Precision@3 counts grades >= 2 in the top 3 divided by 3", () => {
    expect(precisionAt([3, 1, 2])).toBeCloseTo(2 / 3, 6);
    expect(precisionAt([0, 0, 0])).toBe(0);
  });

  it("pairwise accuracy counts unequal-grade pairs ordered correctly", () => {
    // pairs: (1,3) wrong, (1,2) wrong, (3,2) right => 1/3
    expect(pairwiseAccuracy([1, 3, 2])).toBeCloseTo(1 / 3, 6);
    expect(pairwiseAccuracy([2, 2, 2])).toBeNaN();
  });

  it("macro-average skips NaN queries", () => {
    expect(macroAverage([1, Number.NaN, 0])).toBeCloseTo(0.5, 6);
  });
});
