"use client";

import { Check, RotateCcw } from "lucide-react";
import { useState } from "react";
import { ALLERGENS, normalizeAllergies, type AllergenCode } from "@/lib/allergens";
import { BUDGET_MAX, BUDGET_SLIDER_MAX, NO_BUDGET_LIMIT, normalizeBudget } from "@/lib/budget";
import { CUISINES, type Preferences } from "@/lib/preferences";

// no limit (0) shows as an empty box
const budgetText = (budget: number | undefined) => {
  const value = normalizeBudget(budget);
  return value === NO_BUDGET_LIMIT ? "" : String(value);
};

type PreferencePanelProps = {
  preferences: Preferences;
  onSave: (preferences: Preferences) => void;
};

export function PreferencePanel({ preferences, onSave }: PreferencePanelProps) {
  const [draft, setDraft] = useState(preferences);
  const [avoidText, setAvoidText] = useState("");
  // what is typed in the custom budget box, which may be mid-edit ("12.") while the draft keeps the number
  const [budgetInput, setBudgetInput] = useState(budgetText(preferences.budget));
  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const budget = normalizeBudget(draft.budget);
  const noLimit = budget === NO_BUDGET_LIMIT;
  const setBudget = (value: number) => {
    update("budget", normalizeBudget(value));
    setBudgetInput(budgetText(value));
  };
  const addAvoid = () => {
    const next = avoidText.trim().toLowerCase();
    if (!next || draft.exclude.includes(next)) return;
    update("exclude", [...draft.exclude, next]);
    setAvoidText("");
  };
  const allergies = normalizeAllergies(draft.allergies);
  const toggleAllergy = (code: AllergenCode) =>
    update("allergies", allergies.includes(code) ? allergies.filter((current) => current !== code) : normalizeAllergies([...allergies, code]));
  const cuisines = CUISINES;
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
            <option value="halal">Halal (vegan-tagged screen; not certified)</option>
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
        <div className="field-label budget-field">
          <span>
            Spend up to{" "}
            <span className="range-value">
              {noLimit ? "no limit" : `S$${budget.toFixed(2).replace(/\.00$/, "")} a meal`}
            </span>
          </span>
          <input
            className="range-control"
            type="range"
            min="1"
            max={BUDGET_SLIDER_MAX}
            step="1"
            // no limit, or a custom amount above the slider, sits at its end
            value={noLimit ? BUDGET_SLIDER_MAX : Math.min(budget, BUDGET_SLIDER_MAX)}
            onChange={(event) => setBudget(Number(event.target.value))}
            aria-label="Budget per meal, in SGD"
            aria-valuetext={noLimit ? "No limit" : `S$${budget} a meal`}
          />
          <div className="budget-entry">
            <span className="budget-currency" aria-hidden="true">S$</span>
            <input
              type="number"
              inputMode="decimal"
              min="0.5"
              max={BUDGET_MAX}
              step="0.5"
              value={budgetInput}
              placeholder="Any amount"
              aria-label={`Custom budget per meal, in SGD, up to ${BUDGET_MAX}`}
              onChange={(event) => {
                setBudgetInput(event.target.value);
                // an empty box, or 0, is no limit; anything else counts once it is a positive number
                const value = event.target.value.trim() === "" ? NO_BUDGET_LIMIT : Number(event.target.value);
                if (Number.isFinite(value) && value >= 0) update("budget", normalizeBudget(value));
              }}
              onBlur={() => setBudgetInput(budgetText(budget))}
            />
            <button type="button" className="budget-clear" onClick={() => setBudget(NO_BUDGET_LIMIT)} disabled={noLimit}>
              No limit
            </button>
          </div>
        </div>
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
        <div className="field-label">
          I&apos;m allergic to
          <div className="filter-chips" role="group" aria-label="Allergies">
            {ALLERGENS.map((allergen) => (
              <button
                type="button"
                key={allergen.code}
                className={`filter-chip ${allergies.includes(allergen.code) ? "is-active" : ""}`}
                aria-pressed={allergies.includes(allergen.code)}
                onClick={() => toggleAllergy(allergen.code)}
              >
                {allergen.label}
              </button>
            ))}
          </div>
        </div>
        <div className="preference-actions">
          <button
            className="text-button"
            onClick={() => {
              setDraft(preferences);
              setAvoidText("");
              setBudgetInput(budgetText(preferences.budget));
            }}
          >
            <RotateCcw size={15} aria-hidden="true" /> Reset changes
          </button>
          <button
            className="preference-save"
            onClick={() => {
              // the budget is always saved, 0 for no limit, and so are the allergies, none for no allergy
              const next =
                avoidText.trim().toLowerCase() &&
                !draft.exclude.includes(avoidText.trim().toLowerCase())
                  ? {
                      ...draft,
                      budget,
                      allergies,
                      exclude: [
                        ...draft.exclude,
                        avoidText.trim().toLowerCase(),
                      ],
                    }
                  : { ...draft, budget, allergies };
              setDraft(next);
              setBudgetInput(budgetText(budget));
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
