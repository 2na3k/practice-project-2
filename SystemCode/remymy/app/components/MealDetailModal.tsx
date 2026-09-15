"use client";

import Image from "next/image";

import { ArrowRight, Bookmark, Check, Clock3, Flame, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Meal } from "@/lib/meals";

type MealDetailModalProps = {
  meal: Meal | null;
  saved: boolean;
  onClose: () => void;
  onSave: (meal: Meal) => void;
};

export function MealDetailModal({
  meal,
  saved,
  onClose,
  onSave,
}: MealDetailModalProps) {
  const modalRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!meal) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeButton =
      modalRef.current?.querySelector<HTMLButtonElement>(".detail-close");
    closeButton?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusable = Array.from(
        modalRef.current.querySelectorAll<HTMLElement>(
          "button, a, input, select, textarea, [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [meal, onClose]);
  if (!meal) return null;
  return (
    <div
      className="detail-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={modalRef}
        className="meal-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="meal-detail-title"
      >
        <button
          className="detail-close"
          onClick={onClose}
          aria-label="Close meal details"
        >
          <X size={19} aria-hidden="true" />
        </button>
        <div className="detail-header" style={{ background: meal.color }}>
          <span className="meal-card-cuisine">{meal.cuisine}</span>
          {meal.image && (
            <Image
              unoptimized
              width={800}
              height={520}
              className="detail-image"
              src={meal.image}
              alt={`Illustrative photo of ${meal.title}`}
              onError={(event) => {
                event.currentTarget.style.display = "none";
              }}
            />
          )}
          <div className="dish-orb detail-dish-orb" aria-hidden="true">
            <span className="dish-garnish dish-garnish-one" />
            <span className="dish-garnish dish-garnish-two" />
            <span className="dish-center" />
          </div>
        </div>
        <div className="detail-content">
          <div className="detail-title-row">
            <div>
              <p className="eyebrow">ON THE MENU</p>
              <h2 id="meal-detail-title">{meal.title}</h2>
            </div>
            <button
              className={`save-button ${saved ? "is-saved" : ""}`}
              onClick={() => onSave(meal)}
              aria-label={saved ? "Remove from saved meals" : "Save meal"}
              aria-pressed={saved}
            >
              <Bookmark
                size={18}
                fill={saved ? "currentColor" : "none"}
                aria-hidden="true"
              />
            </button>
          </div>
          <p className="detail-description">{meal.description}</p>
          <div className="detail-stats">
            <span>
              <Clock3 size={15} aria-hidden="true" />
              {meal.minutes} min
            </span>
            <span>
              <Flame size={15} aria-hidden="true" />
              {meal.calories} kcal
            </span>
            <span>{meal.protein}g protein</span>
          </div>
          <div className="detail-section">
            <h3>What you&apos;ll need</h3>
            <ul className="ingredient-list">
              {meal.ingredients.map((item) => (
                <li key={item}>
                  <Check size={15} aria-hidden="true" />
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <div className="detail-section">
            <h3>How to make it</h3>
            <ol className="step-list">
              {meal.steps.map((step, index) => (
                <li key={step}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  {step}
                </li>
              ))}
            </ol>
          </div>
          <p className="detail-disclaimer">
            Nutrition is an estimate per serving. Adjust portions to your needs.
          </p>
          <button className="detail-action" onClick={onClose}>
            Let&apos;s cook <ArrowRight size={17} aria-hidden="true" />
          </button>
        </div>
      </section>
    </div>
  );
}
