"use client";

import { ArrowRight, Check, ChefHat, Database, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchSimilarFoods, priceLabel, type CatalogItem } from "@/lib/catalog";
import type { RecipeDetail } from "@/lib/recipes";

const SIMILAR_COUNT = 6;

type RecipeDetailModalProps = {
  recipe: RecipeDetail | null;
  loading: boolean;
  error: string;
  onClose: () => void;
  // opens one of the similar catalog foods
  onOpenFood: (id: string) => void;
};

type SimilarState = { recipeId: string; items: CatalogItem[] | null; error: string };

export function RecipeDetailModal({ recipe, loading, error, onClose, onOpenFood }: RecipeDetailModalProps) {
  const modalRef = useRef<HTMLElement>(null);
  // the clusterer's neighbours of the open recipe; kept with its id so another recipe's never show
  const [similar, setSimilar] = useState<SimilarState | null>(null);
  useEffect(() => {
    if (!recipe) return;
    const controller = new AbortController();
    void fetchSimilarFoods(recipe, SIMILAR_COUNT, controller.signal)
      .then((items) => setSimilar({ recipeId: recipe.id, items, error: "" }))
      .catch((reason) => { if (!controller.signal.aborted) setSimilar({ recipeId: recipe.id, items: null, error: reason instanceof Error ? reason.message : "Similar recipes could not be loaded." }); });
    return () => controller.abort();
  }, [recipe]);
  const current = recipe && similar?.recipeId === recipe.id ? similar : null;
  const previousFocusRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!recipe && !loading && !error) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    modalRef.current?.querySelector<HTMLButtonElement>(".detail-close")?.focus();
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } };
    document.addEventListener("keydown", handleKeyDown);
    return () => { document.removeEventListener("keydown", handleKeyDown); document.body.style.overflow = previousOverflow; previousFocusRef.current?.focus(); previousFocusRef.current = null; };
  }, [error, loading, onClose, recipe]);
  if (!recipe && !loading && !error) return null;
  return (
    <div className="detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={modalRef} className="meal-detail-modal recipe-detail-modal" role="dialog" aria-modal="true" aria-labelledby="recipe-detail-title">
        <button className="detail-close" onClick={onClose} aria-label="Close recipe details"><X size={19} aria-hidden="true" /></button>
        {loading ? <div className="recipe-modal-state" role="status">Loading recipe…</div> : error ? <div className="recipe-modal-state is-error" role="alert"><h2>Recipe unavailable</h2><p>{error}</p></div> : recipe ? (
          <div className="detail-content">
            <div className="recipe-source-mark"><Database size={17} aria-hidden="true" /> Source-backed recipe</div>
            <div className="detail-title-row"><div><p className="eyebrow">OPEN RECIPES</p><h2 id="recipe-detail-title">{recipe.title}</h2></div></div>
            <div className="detail-stats"><span>{recipe.quality_status}</span><span>{recipe.ingredients.length} ingredients</span><span>{recipe.instructions.length} steps</span></div>
            <div className="detail-section"><h3>What you&apos;ll need</h3><ul className="ingredient-list">{recipe.ingredients.map((item, index) => <li key={`${item}-${index}`}><Check size={15} aria-hidden="true" />{item}</li>)}</ul></div>
            <div className="detail-section"><h3>How to make it</h3><ol className="step-list">{recipe.instructions.map((step, index) => <li key={`${step}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span>{step}</li>)}</ol></div>
            <div className="detail-section similar-section" aria-busy={!current}><h3>Similar recipes</h3>
              {!current ? <p className="similar-note" role="status"><LoaderCircle className="spin" size={14} aria-hidden="true" />Finding recipes in the same cluster…</p>
                : current.error ? <p className="similar-note">{current.error}</p>
                  : current.items && current.items.length === 0 ? <p className="similar-note">No similar recipes in the catalog.</p>
                    : <ul className="similar-list">{current.items?.map((item) => <li key={item.fdc_id}><button className="similar-item" onClick={() => onOpenFood(String(item.fdc_id))}><span className="similar-icon" aria-hidden="true"><ChefHat size={15} /></span><span className="similar-info"><strong>{item.description}</strong><small>{priceLabel(item)}{item.energy_kcal !== null ? ` · ${Math.round(item.energy_kcal)} kcal per 100 g` : ""}</small></span><ArrowRight size={15} aria-hidden="true" /></button></li>)}</ul>}
            </div>
            <p className="detail-disclaimer">No nutrition, timing, cuisine, image, or other metadata is inferred from this source record.</p>
            <button className="detail-action" onClick={onClose}>Back to recipes <ArrowRight size={17} aria-hidden="true" /></button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
