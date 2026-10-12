"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, RotateCcw, ShoppingBasket } from "lucide-react";
import { RecipeLikeButtons, type RecipeRatingState } from "@/components/RecipeLikeButtons";
import { saveRecipeRating } from "@/lib/recipes";
import {
  RECOMMENDATION_USERNAME,
  fetchRecommendations,
  formatPrice,
  nutrientHighlights,
  type Recommendations,
} from "@/lib/recommendations";

const TOP_K = 6;

// onItemCount hears how many foods each successful load returned; pass a stable function, such as a state setter.
export function RecommendationPanel({ onItemCount }: { onItemCount?: (count: number) => void } = {}) {
  const [result, setResult] = useState<Recommendations | null>(null);
  const [error, setError] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);
  // what the user said about each pick this session, by its id
  const [ratings, setRatings] = useState<Record<string, RecipeRatingState>>({});

  useEffect(() => {
    const controller = new AbortController();
    fetchRecommendations(RECOMMENDATION_USERNAME, TOP_K, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setResult(next); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Recommendations could not be loaded."); });
    return () => controller.abort();
  }, [retryNonce]);

  useEffect(() => {
    if (result) onItemCount?.(result.items.length);
  }, [result, onItemCount]);

  const retry = () => {
    setError("");
    setResult(null);
    setRetryNonce((current) => current + 1);
  };

  // A pick is a catalog food, and the Data API's rating route takes its fdc_id as the recipe_id.
  const rate = (id: string, like: boolean) => {
    const update = (next: RecipeRatingState) => setRatings((current) => ({ ...current, [id]: { ...current[id], ...next } }));
    update({ saving: true, error: undefined });
    saveRecipeRating(id, RECOMMENDATION_USERNAME, like)
      .then((saved) => update({ like: saved.like, saving: false }))
      .catch((reason) => update({ saving: false, error: reason instanceof Error ? reason.message : "Your rating could not be saved." }));
  };

  const loading = !result && !error;
  const profile = result?.profile ?? null;

  return (
    <section className="for-you" aria-labelledby="for-you-title" aria-busy={loading}>
      <div className="for-you-header">
        <div>
          <p className="eyebrow">PICKED FOR YOU</p>
          <h2 className="for-you-title" id="for-you-title"><ShoppingBasket size={18} aria-hidden="true" />Groceries you&apos;ll probably like</h2>
          <p className="for-you-caption">Based on the recipes the demo profile has rated.</p>
        </div>
        {profile && (
          <div className="for-you-profile" aria-label="Buying profile: averages of the foods in those recipes, nutrients per 100 g">
            <span>{formatPrice(profile.avg_price)} avg. ingredient</span>
            {nutrientHighlights(profile.avg_nutrient_values).map((highlight) => <span key={highlight}>{highlight}</span>)}
          </div>
        )}
      </div>
      {error ? (
        <p className="for-you-note is-error" role="alert">{error}<button onClick={retry}><RotateCcw size={12} aria-hidden="true" />Try again</button></p>
      ) : loading ? (
        <p className="for-you-note" role="status"><LoaderCircle className="spin" size={14} aria-hidden="true" />Finding foods to your taste. The first time can take up to a minute.</p>
      ) : result && result.items.length > 0 ? (
        <ol className="for-you-list">
          {result.items.map((item) => (
            <li className="for-you-item" key={item.id}>
              <span className="for-you-name">{item.title}</span>
              <div className="for-you-item-footer">
                <span className="for-you-score" title="Predicted chance you'd like it">{Math.round(item.score * 100)}% match</span>
                <RecipeLikeButtons title={item.title} rating={ratings[item.id]} onRate={(like) => rate(item.id, like)} />
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="for-you-note">No picks yet. They appear once the profile has rated recipes.</p>
      )}
    </section>
  );
}
