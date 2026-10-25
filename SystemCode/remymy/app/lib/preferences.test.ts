import { describe, expect, it } from "vitest";
import { DEFAULT_PREFERENCES, type Preferences, interpretRequest } from "./preferences";

describe("interpretRequest", () => {
  it("turns dietary, time, exclusion, goal, and cuisine language into preferences", () => {
    expect(
      interpretRequest(
        "vegan Korean food under 20 minutes, no peanuts, high protein",
      ),
    ).toEqual({
      diet: "vegan",
      maxMinutes: 20,
      exclude: ["peanuts"],
      goal: "protein",
      cuisine: "Korean",
    });
  });

  it("carries halal and combined vegan-halal constraints into MCP preferences", () => {
    const halal = interpretRequest("halal dinner");
    const later = interpretRequest("make it quick", halal);
    const veganHalal = interpretRequest("vegan halal dinner");

    expect(halal.diet).toBe("halal");
    expect(later.diet).toBe("halal");
    expect(veganHalal.diet).toBe("vegan");
    expect(veganHalal.exclude).toContain("halal");
  });

  it("carries restrictive choices into a later conversational turn", () => {
    const firstTurn = interpretRequest("vegetarian meals under 25 minutes");
    const secondTurn = interpretRequest(
      "also avoid mushrooms and make it high protein",
      firstTurn,
    );

    expect(secondTurn.diet).toBe("vegetarian");
    expect(secondTurn.maxMinutes).toBe(25);
    expect(secondTurn.goal).toBe("protein");
    expect(secondTurn.exclude).toContain("mushrooms");
  });

  it("keeps a tighter time bound when a later turn is looser", () => {
    const firstTurn = interpretRequest("quick vegan food under 15 minutes");
    const secondTurn = interpretRequest(
      "actually, I have up to 30 minutes",
      firstTurn,
    );

    expect(secondTurn.diet).toBe("vegan");
    expect(secondTurn.maxMinutes).toBe(15);
  });

  it("understands the short phrases used by the composer", () => {
    expect(interpretRequest("Ready in 20").maxMinutes).toBe(20);
    expect(interpretRequest("Plant-powered").diet).toBe("vegan");
    expect(interpretRequest("Protein please").goal).toBe("protein");
    expect(interpretRequest("Make it quicker").maxMinutes).toBe(20);
  });

  it("lets an explicit meat request override a persisted plant-only diet", () => {
    const vegan: Preferences = { ...DEFAULT_PREFERENCES, diet: "vegan", exclude: ["meat"] };
    const preferences = interpretRequest("get me a good meal with a lot of meat", vegan);

    expect(preferences).toMatchObject({ diet: "any", goal: "protein" });
    expect(preferences.exclude).not.toContain("meat");
  });
});
