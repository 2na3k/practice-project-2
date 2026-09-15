"use client";

import { Check, RotateCcw } from "lucide-react";
import { useState } from "react";
import { MEALS, type Preferences } from "@/lib/meals";

type PreferencePanelProps = {
  preferences: Preferences;
  onSave: (preferences: Preferences) => void;
};

export function PreferencePanel({ preferences, onSave }: PreferencePanelProps) {
  const [draft, setDraft] = useState(preferences);
  const [avoidText, setAvoidText] = useState("");
  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const addAvoid = () => {
    const next = avoidText.trim().toLowerCase();
    if (!next || draft.exclude.includes(next)) return;
    update("exclude", [...draft.exclude, next]);
    setAvoidText("");
  };
  const cuisines = Array.from(
    new Set(MEALS.map((meal) => meal.cuisine)),
  ).filter(Boolean);
  return (
    <section className="preferences-view">
      <div className="preferences-intro">
        <p className="eyebrow">MAKE IT YOURS</p>
        <h1>Your food, your rules.</h1>
        <p>
          Set a few defaults and Remymy will keep them in mind whenever you ask
          for an idea.
        </p>
      </div>
      <div className="preference-form">
        <label className="field-label">
          I usually eat
          <select
            className="select-control"
            value={draft.diet}
            onChange={(event) =>
              update("diet", event.target.value as Preferences["diet"])
            }
          >
            <option value="any">Anything</option>
            <option value="vegetarian">Vegetarian</option>
            <option value="vegan">Vegan</option>
          </select>
        </label>
        <label className="field-label">
          Keep it under{" "}
          <span className="range-value">{draft.maxMinutes} min</span>
          <input
            className="range-control"
            type="range"
            min="1"
            max="90"
            step="1"
            value={draft.maxMinutes}
            onChange={(event) =>
              update("maxMinutes", Number(event.target.value))
            }
          />
        </label>
        <label className="field-label">
          I&apos;m eating for
          <select
            className="select-control"
            value={draft.goal}
            onChange={(event) =>
              update("goal", event.target.value as Preferences["goal"])
            }
          >
            <option value="balanced">A balanced meal</option>
            <option value="protein">More protein</option>
            <option value="light">Something light</option>
          </select>
        </label>
        <label className="field-label">
          A cuisine I&apos;m craving
          <select
            className="select-control"
            value={draft.cuisine}
            onChange={(event) => update("cuisine", event.target.value)}
          >
            <option value="any">Any cuisine</option>
            {cuisines.map((cuisine) => (
              <option value={cuisine} key={cuisine}>
                {cuisine}
              </option>
            ))}
          </select>
        </label>
        <div className="field-label">
          I&apos;d rather skip
          <div className="tag-input">
            <div className="avoid-tags">
              {draft.exclude.map((item) => (
                <button
                  type="button"
                  key={item}
                  className="avoid-tag"
                  onClick={() =>
                    update(
                      "exclude",
                      draft.exclude.filter((current) => current !== item),
                    )
                  }
                >
                  {item} ×
                </button>
              ))}
            </div>
            <div className="tag-entry">
              <input
                value={avoidText}
                onChange={(event) => setAvoidText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addAvoid();
                  }
                }}
                placeholder="e.g. mushrooms"
                aria-label="Add an ingredient to avoid"
              />
              <button
                type="button"
                onClick={addAvoid}
                aria-label="Add ingredient to avoid"
              >
                Add
              </button>
            </div>
          </div>
        </div>
        <div className="preference-actions">
          <button
            className="text-button"
            onClick={() => {
              setDraft(preferences);
              setAvoidText("");
            }}
          >
            <RotateCcw size={15} aria-hidden="true" /> Reset changes
          </button>
          <button
            className="preference-save"
            onClick={() => {
              const next =
                avoidText.trim().toLowerCase() &&
                !draft.exclude.includes(avoidText.trim().toLowerCase())
                  ? {
                      ...draft,
                      exclude: [
                        ...draft.exclude,
                        avoidText.trim().toLowerCase(),
                      ],
                    }
                  : draft;
              setDraft(next);
              setAvoidText("");
              onSave(next);
            }}
          >
            <Check size={16} aria-hidden="true" /> Save preferences
          </button>
        </div>
      </div>
    </section>
  );
}
