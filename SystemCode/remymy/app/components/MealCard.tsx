"use client";

import Image from "next/image";

import { Bookmark, Clock3, Flame, Leaf, Sparkles } from "lucide-react";
import { MEAL_DATA_NOTE, type Meal } from "@/lib/meals";

export type MealRecommendation = {
  meal: Meal;
  score: number;
  reasons: string[];
};

type MealCardProps = {
  recommendation: MealRecommendation;
  saved: boolean;
  onSave: (meal: Meal) => void;
  onOpen: (meal: Meal) => void;
};

export function MealCard({
  recommendation,
  saved,
  onSave,
  onOpen,
}: MealCardProps) {
  const { meal, reasons } = recommendation;
  return (
    <article className="meal-card">
      <button
        className="meal-card-media"
        style={{ background: meal.color }}
        onClick={() => onOpen(meal)}
        aria-label={`Open ${meal.title}`}
      >
        {meal.image && (
          <Image
            unoptimized
            width={800}
            height={520}
            className="meal-card-image"
            src={meal.image}
            alt={`Illustrative photo of ${meal.title}`}
            onError={(event) => {
              event.currentTarget.style.display = "none";
            }}
          />
        )}
        <div className="dish-orb" aria-hidden="true">
          <span className="dish-garnish dish-garnish-one" />
          <span className="dish-garnish dish-garnish-two" />
          <span className="dish-center" />
        </div>
        <span className="meal-card-cuisine">{meal.cuisine}</span>
      </button>
      <div className="meal-card-body">
        <div className="meal-card-meta">
          <span>
            <Clock3 size={14} aria-hidden="true" /> {meal.minutes} min
          </span>
          <span>
            <Flame size={14} aria-hidden="true" /> {meal.calories} kcal
          </span>
        </div>
        <p className="meal-card-disclaimer" title={MEAL_DATA_NOTE}>
          Source recipe timing and nutrition may vary by portion.
        </p>
        <button className="meal-card-title" onClick={() => onOpen(meal)}>
          {meal.title}
        </button>
        <p className="meal-card-description">{meal.description}</p>
        <div className="meal-card-tags">
          {meal.tags.slice(0, 3).map((tag) => (
            <span key={tag}>
              <Leaf size={12} aria-hidden="true" />
              {tag}
            </span>
          ))}
        </div>
        <div className="meal-card-footer">
          <span className="rank-note">
            <Sparkles size={14} aria-hidden="true" />{" "}
            {reasons[0] ?? "A good fit today"}
          </span>
          <button
            className={`save-button ${saved ? "is-saved" : ""}`}
            onClick={() => onSave(meal)}
            aria-label={
              saved
                ? `Remove ${meal.title} from saved meals`
                : `Save ${meal.title}`
            }
            aria-pressed={saved}
          >
            <Bookmark
              size={17}
              fill={saved ? "currentColor" : "none"}
              aria-hidden="true"
            />
          </button>
        </div>
      </div>
    </article>
  );
}
