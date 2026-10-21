import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCatalogItem, nutrientRows, packSize, parseCatalogItem, priceLabel, priceSource, type CatalogItem } from "./catalog";

const onions = {
  fdc_id: 170005, description: "Onions, spring or scallions (includes tops and bulb), raw", product: ["onions"],
  item_prices: { onions: { price: 1.88, quantity: 100.0 } }, item_unit_prices: { onions: 1.88 },
  item_routes: { onions: "variation" }, matched_variations: { onions: "pasar spring thailand" }, unpriced_items: [],
  priced_items: 1, total_items: 1, priced_share: 1.0, items_without_unit_price: 0, adj: ["raw"], verb: ["includes"],
  total_price: 1.88, basket_per_100: 1.88, protein_g: 1.83, fat_g: 0.19, carb_g: 7.34, energy_kcal: 32.0, fiber_g: 2.6,
  sodium_mg: 16.0, cholesterol_mg: 0.0, satfat_g: 0.032, sugars_g: null,
};

describe("catalog item details", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("accepts the Data API's item and rejects other shapes", () => {
    expect(parseCatalogItem(onions).description).toContain("Onions");
    expect(() => parseCatalogItem({ ...onions, item_prices: { onions: { price: "1.88", quantity: 100 } } })).toThrow();
    expect(() => parseCatalogItem({ ...onions, energy_kcal: "32" })).toThrow();
    expect(() => parseCatalogItem({ ...onions, product: "onions" })).toThrow();
  });

  it("describes pack sizes, price sources and the nutrients FDC has", () => {
    expect(packSize(100)).toBe("100 g/ml");
    expect(packSize([1000, 160])).toBe("160–1000 g/ml");
    expect(packSize(null)).toBeNull();
    expect(priceSource("variation", "pasar spring thailand")).toBe('FairPrice "pasar spring thailand"');
    expect(priceSource("average", undefined)).toBe("average of FairPrice listings");
    const rows = nutrientRows(parseCatalogItem(onions));
    expect(rows[0]).toEqual({ label: "Energy", value: "32 kcal" });
    expect(rows.map((row) => row.label)).not.toContain("Sugars");
  });

  it("reads an item through the app's route", async () => {
    const fetchMock = vi.fn(async () => Response.json(onions));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchCatalogItem("170005")).resolves.toMatchObject({ fdc_id: 170005 });
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/catalog/170005");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Catalog item not found." }, { status: 404 })));
    await expect(fetchCatalogItem("99")).rejects.toThrow("Catalog item not found.");
  });
});

describe("priceLabel", () => {
  const item = (total_price: number, prices: number[]) => ({
    total_price,
    item_prices: Object.fromEntries(prices.map((price, index) => [`item ${index}`, { price, quantity: 100 }])),
  }) as unknown as CatalogItem;

  it("shows the basket price when every ingredient is priced, a floor when some are, and says when none are", () => {
    expect(priceLabel(item(4.5, [2, 2.5]))).toBe("S$4.50");
    expect(priceLabel(item(0, [2.98]))).toBe("from S$2.98");
    expect(priceLabel(item(0, []))).toBe("price unknown");
  });
});
