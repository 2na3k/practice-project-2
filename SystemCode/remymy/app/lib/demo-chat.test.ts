import { describe, expect, it } from "vitest";
import { DEFAULT_PREFERENCES } from "./meals";
import { demoReply } from "./demo-chat";

describe("demoReply", () => {
  it("opens with a natural welcome and does not invent meal results", () => {
    const result = demoReply("hello", DEFAULT_PREFERENCES);

    expect(result.recommendations).toEqual([]);
    expect(result.text).toMatch(/what sounds good|craving|ingredient|time/i);
    expect(result.preferences).toEqual(DEFAULT_PREFERENCES);
  });

  it("turns a conversational constraint into bounded recommendations", () => {
    const result = demoReply(
      "vegan under 20 minutes, no peanuts and high protein",
      DEFAULT_PREFERENCES,
    );

    expect(result.preferences).toMatchObject({
      diet: "vegan",
      maxMinutes: 20,
      goal: "protein",
    });
    expect(result.preferences.exclude).toContain("peanuts");
    expect(result.recommendations.length).toBeGreaterThan(0);
    expect(
      result.recommendations.every(({ meal }) => meal.minutes <= 20),
    ).toBe(true);
    expect(
      result.recommendations.every(({ meal }) => meal.tags.includes("vegan")),
    ).toBe(true);
    expect(
      result.recommendations.every(({ meal }) =>
        meal.ingredients.every((ingredient) => !/peanut/i.test(ingredient)),
      ),
    ).toBe(true);
    expect(result.text).toMatch(/protein|shortlist|plate/i);
  });

  it("keeps the conversation honest when constraints produce no match", () => {
    const result = demoReply("under 1 minute, no peanuts", DEFAULT_PREFERENCES);

    expect(result.recommendations).toEqual([]);
    expect(result.text).toMatch(/couldn't find a match/i);
    expect(result.text).toMatch(/1-minute limit/i);
    expect(result.preferences.maxMinutes).toBe(1);
  });
});
