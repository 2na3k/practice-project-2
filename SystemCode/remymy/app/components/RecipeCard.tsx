"use client";

import { ArrowRight, Check, Database } from "lucide-react";
import type { RecipeSummary } from "@/lib/recipes";

type RecipeCardProps = {
  recipe: RecipeSummary;
  onOpen: (recipe: RecipeSummary) => void;
};

export function RecipeCard({ recipe, onOpen }: RecipeCardProps) {
  return (
    <article className="meal-card recipe-card">
      <button className="recipe-card-top" onClick={() => onOpen(recipe)} aria-label={`Open ${recipe.title}`}>
        <Database size={28} aria-hidden="true" />
        <span>OPEN RECIPES</span>
      </button>
      <div className="meal-card-body">
        <div className="meal-card-meta">
          <span><Check size={14} aria-hidden="true" /> {recipe.ingredient_count} ingredients</span>
          <span>{recipe.quality_status}</span>
        </div>
        <button className="meal-card-title" onClick={() => onOpen(recipe)}>{recipe.title}</button>
        {recipe.ingredient_preview.length > 0 ? (
          <p className="meal-card-description">{recipe.ingredient_preview.join(" · ")}</p>
        ) : (
          <p className="meal-card-description">Ingredients are available in the recipe details.</p>
        )}
        <div className="meal-card-footer">
          <span className="rank-note">Source-backed recipe</span>
          <button className="recipe-open-button" onClick={() => onOpen(recipe)} aria-label={`View ${recipe.title}`}>
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </article>
  );
}
