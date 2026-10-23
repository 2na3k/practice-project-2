"use client";

import { ArrowRight, Bookmark, ChefHat, LoaderCircle, ShoppingCart, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { RecipeLikeButtons, type RecipeRatingState } from "@/components/RecipeLikeButtons";
import { fetchSimilarToCatalogItem, nutrientRows, packSize, priceLabel, priceSource, type CatalogItem } from "@/lib/catalog";
import { formatPrice } from "@/lib/recommendations";

const SIMILAR_COUNT = 6;

type SimilarState = { itemId: number; items: CatalogItem[] | null; error: string };

type CatalogItemModalProps = {
  item: CatalogItem | null;
  loading: boolean;
  error: string;
  onClose: () => void;
  rating?: RecipeRatingState;
  onRate: (id: string, like: boolean) => void;
  saved: boolean;
  onSave: (item: CatalogItem) => void;
  // opens one of the similar catalog foods
  onOpenFood: (id: string) => void;
};

// A recommended recipe's details: what it is made of, what that costs at FairPrice, and its nutrients per 100 g.
export function CatalogItemModal({ item, loading, error, onClose, rating, onRate, saved, onSave, onOpenFood }: CatalogItemModalProps) {
  const modalRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  // the clusterer's neighbours of the open food; kept with its id so another food's never show
  const [similar, setSimilar] = useState<SimilarState | null>(null);
  useEffect(() => {
    if (!item) return;
    const controller = new AbortController();
    void fetchSimilarToCatalogItem(item, SIMILAR_COUNT, controller.signal)
      .then((items) => setSimilar({ itemId: item.fdc_id, items, error: "" }))
      .catch((reason) => { if (!controller.signal.aborted) setSimilar({ itemId: item.fdc_id, items: null, error: reason instanceof Error ? reason.message : "Similar recipes could not be loaded." }); });
    return () => controller.abort();
  }, [item]);
  const current = item && similar?.itemId === item.fdc_id ? similar : null;
  useEffect(() => {
    if (!item && !loading && !error) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    modalRef.current?.querySelector<HTMLButtonElement>(".detail-close")?.focus();
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } };
    document.addEventListener("keydown", handleKeyDown);
    return () => { document.removeEventListener("keydown", handleKeyDown); document.body.style.overflow = previousOverflow; previousFocusRef.current?.focus(); previousFocusRef.current = null; };
  }, [error, item, loading, onClose]);
  if (!item && !loading && !error) return null;
  const id = item ? String(item.fdc_id) : "";
  const descriptors = item ? [...new Set([...item.adj, ...item.verb])] : [];
  // total_price and basket_per_100 are 0 unless every ingredient is priced
  const fullyPriced = item ? item.unpriced_items.length === 0 : false;
  const pricedSum = item ? Object.values(item.item_prices).reduce((sum, price) => sum + price.price, 0) : 0;
  const nutrients = item ? nutrientRows(item) : [];
  return (
    <div className="detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={modalRef} className="meal-detail-modal recipe-detail-modal catalog-detail-modal" role="dialog" aria-modal="true" aria-labelledby="catalog-detail-title">
        <button className="detail-close" onClick={onClose} aria-label="Close recipe details"><X size={19} aria-hidden="true" /></button>
        {loading ? <div className="recipe-modal-state" role="status">Loading recipe…</div> : error ? <div className="recipe-modal-state is-error" role="alert"><h2>Recipe unavailable</h2><p>{error}</p></div> : item ? (
          <div className="detail-content">
            <div className="recipe-source-mark"><ChefHat size={17} aria-hidden="true" /> Picked for you</div>
            <div className="detail-title-row">
              <div><p className="eyebrow">PRICED AT FAIRPRICE</p><h2 id="catalog-detail-title">{item.description}</h2></div>
              <div className="for-you-actions">
                <RecipeLikeButtons title={item.description} rating={rating} onRate={(like) => onRate(id, like)} />
                <button className={`save-button ${saved ? "is-saved" : ""}`} onClick={() => onSave(item)} aria-pressed={saved} aria-label={saved ? `Remove ${item.description} from saved recipes` : `Save ${item.description}`} title={saved ? "Saved" : "Save for later"}>
                  <Bookmark size={17} fill={saved ? "currentColor" : "none"} aria-hidden="true" />
                </button>
              </div>
            </div>
            <div className="detail-stats">
              {fullyPriced ? <>
                <span>{formatPrice(item.total_price)} basket</span>
                <span>{formatPrice(item.basket_per_100)} per 100 g</span>
                <span>{item.product.length} {item.product.length === 1 ? "ingredient" : "ingredients"}</span>
              </> : <span>{formatPrice(pricedSum)} for {item.product.length - item.unpriced_items.length} of {item.product.length} ingredients priced</span>}
            </div>
            {descriptors.length > 0 && <div className="meal-card-tags catalog-descriptors">{descriptors.map((word) => <span key={word}>{word}</span>)}</div>}
            <div className="detail-section"><h3>What&apos;s in it</h3><ul className="ingredient-list catalog-ingredients">{item.product.map((span) => {
              const price = item.item_prices[span];
              const size = price ? packSize(price.quantity) : null;
              return <li key={span}><ShoppingCart size={15} aria-hidden="true" /><span><strong>{span}</strong>{price ? <small>{formatPrice(price.price)}{size ? ` · ${size} pack` : ""} · {priceSource(item.item_routes[span], item.matched_variations[span])}</small> : <small>Not found at FairPrice</small>}</span></li>;
            })}</ul></div>
            {nutrients.length > 0 && <div className="detail-section"><h3>Nutrients per 100 g</h3><dl className="catalog-nutrients">{nutrients.map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>}
            <div className="detail-section similar-section" aria-busy={!current}><h3>Similar recipes</h3>
              {!current ? <p className="similar-note" role="status"><LoaderCircle className="spin" size={14} aria-hidden="true" />Finding recipes in the same cluster…</p>
                : current.error ? <p className="similar-note">{current.error}</p>
                  : current.items && current.items.length === 0 ? <p className="similar-note">No similar recipes in the catalog.</p>
                    : <ul className="similar-list">{current.items?.map((near) => <li key={near.fdc_id}><button className="similar-item" onClick={() => onOpenFood(String(near.fdc_id))}><span className="similar-icon" aria-hidden="true"><ChefHat size={15} /></span><span className="similar-info"><strong>{near.description}</strong><small>{priceLabel(near)}{near.energy_kcal !== null ? ` · ${Math.round(near.energy_kcal)} kcal per 100 g` : ""}</small></span><ArrowRight size={15} aria-hidden="true" /></button></li>)}</ul>}
            </div>
            <p className="detail-disclaimer">Prices are SGD from FairPrice listings; nutrients come from USDA FoodData Central.</p>
            <button className="detail-action" onClick={onClose}>Back <ArrowRight size={17} aria-hidden="true" /></button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
