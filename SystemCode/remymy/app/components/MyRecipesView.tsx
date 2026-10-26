"use client";

import { ArrowRight, BookOpen, LoaderCircle, NotebookPen, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { formatHistoryDate } from "@/lib/history";
import { NUTRIENT_FIELDS, fetchMyRecipes, tagText, type SavedRecipe } from "@/lib/my-recipes";

type MyRecipesViewProps = {
  username: string;
  // opens a recipe's details, as the Recipes page does
  onOpen: (recipe: { id: string }) => void;
  onAdd: () => void;
};

// "12" or "4.5": a nutrient amount as the user would have typed it.
function amount(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

// The recipes the user wrote themselves, newest first, each with its tags and the nutrients they gave it. Read again
// each time the view opens, so a recipe just added is there.
export function MyRecipesView({ username, onOpen, onAdd }: MyRecipesViewProps) {
  const [recipes, setRecipes] = useState<SavedRecipe[] | null>(null);
  const [error, setError] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetchMyRecipes(username, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setRecipes(next); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Your recipes could not be loaded."); });
    return () => controller.abort();
  }, [username, retryNonce]);

  const retry = () => {
    setError("");
    setRecipes(null);
    setRetryNonce((current) => current + 1);
  };

  return (
    <section className="browse-view my-recipes-view">
      <div className="view-heading"><p className="eyebrow">YOUR OWN COOKBOOK</p><h1>My recipes.</h1><p>The recipes you wrote, newest first, with the tags and nutrients you gave them.</p></div>
      {error ? <div className="empty-state recipe-state" role="alert"><h3>Your recipes are unavailable.</h3><p>{error}</p><button className="detail-action" onClick={retry}>Try again <RotateCcw size={17} /></button></div>
        : recipes === null ? <div className="empty-state recipe-state" role="status"><LoaderCircle className="spin" size={22} /><h3>Loading your recipes…</h3></div>
          : recipes.length === 0 ? <div className="empty-state"><div className="empty-state-icon"><BookOpen size={22} /></div><h3>No recipes of your own yet.</h3><p>Write one down and it&apos;ll be kept here.</p><button className="detail-action" onClick={onAdd}>Add a recipe <ArrowRight size={17} /></button></div>
            : <div className="shortlist-grid">{recipes.map((recipe) => <MyRecipeCard key={recipe.id} recipe={recipe} onOpen={onOpen} />)}</div>}
    </section>
  );
}

function MyRecipeCard({ recipe, onOpen }: { recipe: SavedRecipe; onOpen: (recipe: { id: string }) => void }) {
  const nutrients = NUTRIENT_FIELDS.flatMap(({ key, label, unit }) => {
    const value = recipe.nutrients[key];
    return value === undefined ? [] : [`${label} ${amount(value)} ${unit}`];
  });
  return (
    <article className="meal-card recipe-card my-recipe-card">
      <button className="recipe-card-top" onClick={() => onOpen(recipe)} aria-label={`Open ${recipe.title}`}>
        <NotebookPen size={28} aria-hidden="true" />
        <span>YOUR RECIPE</span>
      </button>
      <div className="meal-card-body">
        <div className="meal-card-meta">
          <span>{recipe.ingredients.length} {recipe.ingredients.length === 1 ? "ingredient" : "ingredients"}</span>
          <span>{recipe.instructions.length} {recipe.instructions.length === 1 ? "step" : "steps"}</span>
        </div>
        <button className="meal-card-title" onClick={() => onOpen(recipe)}>{recipe.title}</button>
        <p className="meal-card-description">{recipe.ingredients.slice(0, 3).join(" · ")}</p>
        {recipe.tags.length > 0 && <div className="meal-card-tags">{recipe.tags.map((tag) => <span key={tag.label}>{tagText(tag.label)}</span>)}</div>}
        {nutrients.length > 0 && <p className="my-recipe-nutrients">{nutrients.join(" · ")} per 100 g</p>}
        <div className="meal-card-footer">
          <span className="rank-note">Added {formatHistoryDate(recipe.created_at)}</span>
          <button className="recipe-open-button" onClick={() => onOpen(recipe)} aria-label={`View ${recipe.title}`}>
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </article>
  );
}
