import { dataApiError, type RecipeDetail } from "./recipes";

// One item of the priced catalog, as the Data API's GET /api/recipe/{fdc_id} gives it (the fields shown here).
// Prices are SGD from FairPrice; nutrients are per 100 g, null where FDC has no value.
export type CatalogItem = {
  fdc_id: number;
  description: string;
  // the item's ingredients, each priced by a FairPrice pack: its price and its size(s) in g or ml
  product: string[];
  item_prices: Record<string, { price: number; quantity: number | number[] | null }>;
  // how each ingredient was priced: "variation" (one product, in matched_variations), "average" or "term crawl"
  item_routes: Record<string, string>;
  matched_variations: Record<string, string>;
  unpriced_items: string[];
  adj: string[];
  verb: string[];
  total_price: number;
  basket_per_100: number;
  energy_kcal: number | null;
  protein_g: number | null;
  fat_g: number | null;
  satfat_g: number | null;
  carb_g: number | null;
  sugars_g: number | null;
  fiber_g: number | null;
  sodium_mg: number | null;
  cholesterol_mg: number | null;
  // its tags' labels: a Food.com recipe's, e.g. "60_minutes_or_less"; none for an FDC food
  tags: string[];
};

const NUTRIENTS = [
  { key: "energy_kcal", label: "Energy", unit: "kcal" },
  { key: "protein_g", label: "Protein", unit: "g" },
  { key: "fat_g", label: "Fat", unit: "g" },
  { key: "satfat_g", label: "Saturated fat", unit: "g" },
  { key: "carb_g", label: "Carbohydrates", unit: "g" },
  { key: "sugars_g", label: "Sugars", unit: "g" },
  { key: "fiber_g", label: "Fibre", unit: "g" },
  { key: "sodium_mg", label: "Sodium", unit: "mg" },
  { key: "cholesterol_mg", label: "Cholesterol", unit: "mg" },
] as const;

const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const isStringMap = (value: unknown): value is Record<string, string> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.values(value as object).every((item) => typeof item === "string");

export function parseCatalogItem(value: unknown): CatalogItem {
  const fail = () => { throw new Error("Catalog service returned an unexpected response."); };
  if (!value || typeof value !== "object") return fail();
  const item = value as Partial<CatalogItem>;
  const prices = item.item_prices;
  const pricesOk = Boolean(prices) && typeof prices === "object" && Object.values(prices as object).every((entry) =>
    entry && typeof entry === "object" && isNumber(entry.price) &&
    (entry.quantity === null || isNumber(entry.quantity) || (Array.isArray(entry.quantity) && entry.quantity.every(isNumber))));
  const nutrientsOk = NUTRIENTS.every(({ key }) => item[key] === null || isNumber(item[key]));
  if (!isNumber(item.fdc_id) || typeof item.description !== "string" || !isStrings(item.product) || !pricesOk ||
    !isStringMap(item.item_routes) || !isStringMap(item.matched_variations) || !isStrings(item.unpriced_items) ||
    !isStrings(item.adj) || !isStrings(item.verb) || !isNumber(item.total_price) || !isNumber(item.basket_per_100) || !nutrientsOk ||
    (item.tags !== undefined && !isStrings(item.tags))) {
    return fail();
  }
  return { ...item, tags: item.tags ?? [] } as CatalogItem;
}

export async function fetchCatalogItem(id: string, signal?: AbortSignal): Promise<CatalogItem> {
  const response = await fetch(`/api/catalog/${encodeURIComponent(id)}`, { cache: "no-store", signal });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "This recipe could not be loaded."));
  return parseCatalogItem(body);
}

// A catalog item's price in a list: total_price is 0 unless every ingredient is priced, so a partly priced item shows
// what its priced ingredients add up to, marked as a floor, and one with none priced says so.
export function priceLabel(item: CatalogItem): string {
  if (item.total_price > 0) return `S$${item.total_price.toFixed(2)}`;
  const priced = Object.values(item.item_prices).reduce((sum, entry) => sum + entry.price, 0);
  return priced > 0 ? `from S$${priced.toFixed(2)}` : "price unknown";
}

export const MAX_SIMILAR = 12;

// The catalog foods nearest a recipe in its recipe cluster, nearest first.
export async function fetchSimilarFoods(recipe: RecipeDetail, k: number, signal?: AbortSignal): Promise<CatalogItem[]> {
  const response = await fetch("/api/cluster", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ item: recipe, k }),
    cache: "no-store",
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(dataApiError(body, "Similar recipes could not be loaded."));
  const items = body && typeof body === "object" ? (body as { near_items?: unknown }).near_items : undefined;
  if (!Array.isArray(items)) throw new Error("Clustering service returned an unexpected response.");
  return items.map(parseCatalogItem);
}

// The catalog foods nearest a catalog food: the clusterer reads only a recipe's title, so the food goes in as a recipe
// titled by its description; the food itself is left out of its neighbours.
export async function fetchSimilarToCatalogItem(item: CatalogItem, k: number, signal?: AbortSignal): Promise<CatalogItem[]> {
  const recipe: RecipeDetail = { id: String(item.fdc_id), title: item.description, ingredients: item.product, instructions: [], quality_status: "catalog", tags: item.tags };
  const items = await fetchSimilarFoods(recipe, Math.min(k + 1, MAX_SIMILAR), signal);
  return items.filter((near) => near.fdc_id !== item.fdc_id).slice(0, k);
}

// "100 g/ml", or "160–1000 g/ml" when the price is an average over packs of several sizes.
export function packSize(quantity: number | number[] | null): string | null {
  const sizes = quantity === null ? [] : Array.isArray(quantity) ? quantity : [quantity];
  if (sizes.length === 0) return null;
  const low = Math.min(...sizes), high = Math.max(...sizes);
  const amount = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(1));
  return low === high ? `${amount(low)} g/ml` : `${amount(low)}–${amount(high)} g/ml`;
}

// How an ingredient's price was found, for a reader.
export function priceSource(route: string | undefined, variation: string | undefined): string {
  if (route === "variation" && variation) return `FairPrice "${variation}"`;
  if (route === "average") return "average of FairPrice listings";
  if (route === "term crawl") return "FairPrice search";
  return "FairPrice";
}

// The nutrients FDC has a value for, in a fixed order, per 100 g.
export function nutrientRows(item: CatalogItem): { label: string; value: string }[] {
  return NUTRIENTS.flatMap(({ key, label, unit }) => {
    const value = item[key];
    if (value === null) return [];
    return [{ label, value: `${value >= 10 ? Math.round(value) : Number(value.toFixed(2))} ${unit}` }];
  });
}

export function formatPrice(value: number): string {
  return `S$${value.toFixed(2)}`;
}
