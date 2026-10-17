import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchHistory, formatHistoryDate, parseHistory } from "./history";

const item = { recipe_id: "170005", kind: "food", title: "Onions, spring or scallions", like: true, timestamp: "2026-10-12T15:16:45.161Z" };

describe("history from the database", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("accepts rows of either kind, a missing title included, and rejects other shapes", () => {
    expect(parseHistory([item, { ...item, recipe_id: "openrecipes:1", kind: "recipe", title: null, like: false }])).toHaveLength(2);
    expect(() => parseHistory([{ ...item, kind: "meal" }])).toThrow();
    expect(() => parseHistory([{ ...item, like: 1 }])).toThrow();
    expect(() => parseHistory({ items: [item] })).toThrow();
  });

  it("reads the newest 200 through the app's route and passes on the API's reason", async () => {
    const fetchMock = vi.fn(async () => Response.json([item]));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchHistory("demo")).resolves.toEqual([item]);
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/users/demo/history?limit=200");

    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "User not found." }, { status: 404 })));
    await expect(fetchHistory("nobody")).rejects.toThrow("User not found.");
  });

  it("shows a date it cannot read as it came", () => {
    expect(formatHistoryDate("not a date")).toBe("not a date");
    expect(formatHistoryDate(item.timestamp)).toMatch(/2026/);
  });
});
