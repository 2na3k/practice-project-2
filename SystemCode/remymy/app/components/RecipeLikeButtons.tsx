"use client";

import { Check, LoaderCircle, X } from "lucide-react";

// What the user said about a recipe this session: the like last saved, whether one is being saved, the last error.
export type RecipeRatingState = {
  like?: boolean;
  saving?: boolean;
  error?: string;
};

type RecipeLikeButtonsProps = {
  title: string;
  rating?: RecipeRatingState;
  onRate: (like: boolean) => void;
};

export function RecipeLikeButtons({ title, rating, onRate }: RecipeLikeButtonsProps) {
  const like = rating?.like;
  const saving = Boolean(rating?.saving);
  // the choice already saved is not sent again
  const choose = (next: boolean) => { if (!saving && like !== next) onRate(next); };
  return (
    <div className="like-buttons" role="group" aria-label={`Did you like ${title}?`} aria-busy={saving}>
      <button
        className={`like-button is-like${like === true ? " is-active" : ""}`}
        aria-pressed={like === true}
        aria-label={`Liked ${title}`}
        title="Liked it"
        disabled={saving}
        onClick={() => choose(true)}
      >
        <Check size={15} aria-hidden="true" />
      </button>
      <button
        className={`like-button is-dislike${like === false ? " is-active" : ""}`}
        aria-pressed={like === false}
        aria-label={`Did not like ${title}`}
        title="Not for me"
        disabled={saving}
        onClick={() => choose(false)}
      >
        <X size={15} aria-hidden="true" />
      </button>
      {saving && <LoaderCircle className="spin like-saving" size={13} aria-label="Saving" />}
      {rating?.error && <span className="like-error" role="alert">{rating.error}</span>}
    </div>
  );
}
