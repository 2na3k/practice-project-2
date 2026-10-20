"use client";

import { Check, LoaderCircle, RotateCcw, Sparkles, X } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  CLASSIFIER_LABELS,
  LIMITS,
  NUTRIENT_FIELDS,
  type NutrientKey,
  type RecipeTag,
  type SavedRecipe,
  type TagSource,
  type TagSuggestion,
  normalizeTag,
  parseNutrients,
  saveMyRecipe,
  splitLines,
  suggestTags,
  tagText,
  validateNewRecipe,
} from "@/lib/my-recipes";

type AddRecipePanelProps = {
  username: string;
  onSaved: (recipe: SavedRecipe) => void;
};

const OFFERED = new Set<string>(CLASSIFIER_LABELS);

export function AddRecipePanel({ username, onSaved }: AddRecipePanelProps) {
  const [title, setTitle] = useState("");
  const [ingredientsText, setIngredientsText] = useState("");
  const [stepsText, setStepsText] = useState("");
  const [nutrientText, setNutrientText] = useState<Partial<Record<NutrientKey, string>>>({});
  // the chosen tags, by label: kept from the classifier's suggestion, or picked / typed by the user
  const [tags, setTags] = useState<Record<string, TagSource>>({});
  const [ownTag, setOwnTag] = useState("");
  const [suggestion, setSuggestion] = useState<TagSuggestion | null>(null);
  // the dish name and nutrients the suggestion was made for, to tell when it no longer matches the form
  const [suggestedFor, setSuggestedFor] = useState("");
  const [suggestBusy, setSuggestBusy] = useState(false);
  const [suggestError, setSuggestError] = useState("");
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saved, setSaved] = useState<SavedRecipe | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  const { nutrients, invalid } = parseNutrients(nutrientText);
  const classifyKey = JSON.stringify([title.trim(), nutrients]);
  const stale = suggestion !== null && suggestedFor !== classifyKey;
  const ingredientCount = splitLines(ingredientsText).length;
  const ownTags = Object.keys(tags).filter((label) => !OFFERED.has(label));

  const toggleTag = (label: string) =>
    setTags((current) => current[label]
      ? Object.fromEntries(Object.entries(current).filter(([chosen]) => chosen !== label))
      : { ...current, [label]: suggestion?.labels.includes(label) ? "classifier" : "user" });

  const addOwnTag = () => {
    const label = normalizeTag(ownTag);
    if (label) setTags((current) => current[label] ? current : { ...current, [label]: "user" });
    setOwnTag("");
  };

  const requestSuggestion = () => {
    if (!title.trim() || invalid.length) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    const key = classifyKey;
    setSuggestBusy(true);
    setSuggestError("");
    void suggestTags(title, nutrients, controller.signal)
      .then((next) => {
        setSuggestion(next);
        setSuggestedFor(key);
        // the new suggestion replaces the old one's tags; the user's own choices stay
        setTags((current) => ({
          ...Object.fromEntries(Object.entries(current).filter(([, source]) => source === "user")),
          ...Object.fromEntries(next.labels.filter((label) => current[label] !== "user").map((label) => [label, "classifier" as const])),
        }));
      })
      .catch((error) => { if (!controller.signal.aborted) setSuggestError(error instanceof Error ? error.message : "Tag suggestions are unavailable."); })
      .finally(() => { if (!controller.signal.aborted) { setSuggestBusy(false); requestRef.current = null; } });
  };

  const reset = () => {
    requestRef.current?.abort();
    setTitle("");
    setIngredientsText("");
    setStepsText("");
    setNutrientText({});
    setTags({});
    setOwnTag("");
    setSuggestion(null);
    setSuggestedFor("");
    setSuggestBusy(false);
    setSuggestError("");
    setSaveError("");
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (saveBusy) return;
    if (invalid.length) {
      setSaveError(`Check ${invalid.map((key) => NUTRIENT_FIELDS.find((field) => field.key === key)?.label).join(", ")}: nutrients are numbers of 0 or more.`);
      return;
    }
    const pending = normalizeTag(ownTag);
    const chosen: RecipeTag[] = Object.entries({ ...tags, ...(pending && !tags[pending] ? { [pending]: "user" as const } : {}) })
      .map(([label, source]) => ({ label, source }));
    const checked = validateNewRecipe({
      title, ingredients: splitLines(ingredientsText), instructions: splitLines(stepsText), tags: chosen, nutrients,
    });
    if ("error" in checked) {
      setSaveError(checked.error);
      return;
    }
    setSaveBusy(true);
    setSaveError("");
    setSaved(null);
    void saveMyRecipe(username, checked.recipe)
      .then((recipe) => {
        setSaved(recipe);
        reset();
        onSaved(recipe);
      })
      .catch((error) => setSaveError(error instanceof Error ? error.message : "Your recipe could not be saved."))
      .finally(() => setSaveBusy(false));
  };

  return (
    <section className="preferences-view add-recipe-view">
      <div className="preferences-intro">
        <p className="eyebrow">FROM YOUR OWN KITCHEN</p>
        <h1>Add your recipe.</h1>
        <p>Write down a dish you make, tag it, and keep it with the rest of your food corner.</p>
      </div>
      {saved && (
        <div className="recipe-saved" role="status">
          <Check size={16} aria-hidden="true" />
          <span><strong>{saved.title}</strong> is saved{saved.tags.length ? ` with ${saved.tags.length} tag${saved.tags.length === 1 ? "" : "s"}` : ""}.</span>
        </div>
      )}
      <form className="preference-form add-recipe-form" onSubmit={submit} noValidate>
        <label className="field-label field-wide">
          Recipe name
          <input
            className="text-control"
            value={title}
            maxLength={LIMITS.title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Grandma's chicken curry"
            required
          />
        </label>
        <label className="field-label">
          <span>Ingredients <span className="field-hint">one per line{ingredientCount ? ` · ${ingredientCount}` : ""}</span></span>
          <textarea
            className="text-control"
            value={ingredientsText}
            onChange={(event) => setIngredientsText(event.target.value)}
            placeholder={"500 g chicken thighs\n1 onion, chopped\n2 tbsp curry paste"}
            rows={8}
            required
          />
        </label>
        <label className="field-label">
          <span>Steps <span className="field-hint">one per line, optional</span></span>
          <textarea
            className="text-control"
            value={stepsText}
            onChange={(event) => setStepsText(event.target.value)}
            placeholder={"Brown the chicken.\nAdd the onion and paste.\nSimmer for 30 minutes."}
            rows={8}
          />
        </label>
        <details className="field-wide nutrient-details">
          <summary>Nutrients per 100 g <span className="field-hint">optional, also sharpens the tag suggestions</span></summary>
          <div className="nutrient-grid">
            {NUTRIENT_FIELDS.map(({ key, label, unit }) => (
              <label className="nutrient-field" key={key}>
                <span>{label} <small>{unit}</small></span>
                <input
                  className={`text-control ${invalid.includes(key) ? "is-invalid" : ""}`}
                  inputMode="decimal"
                  value={nutrientText[key] ?? ""}
                  onChange={(event) => setNutrientText((current) => ({ ...current, [key]: event.target.value }))}
                  aria-invalid={invalid.includes(key)}
                />
              </label>
            ))}
          </div>
        </details>
        <div className="field-label field-wide">
          Tags
          <div className="suggest-row">
            <button type="button" className="secondary-button" onClick={requestSuggestion} disabled={!title.trim() || invalid.length > 0 || suggestBusy}>
              {suggestBusy ? <LoaderCircle className="spin" size={15} aria-hidden="true" /> : <Sparkles size={15} aria-hidden="true" />}
              {suggestBusy ? "Asking the classifier…" : suggestion ? "Suggest again" : "Suggest tags"}
            </button>
            <span className="field-hint" aria-live="polite">
              {suggestError ? <span className="form-error">{suggestError}</span>
                : suggestBusy ? "The first suggestion can take about 20 seconds while the model loads."
                  : stale ? "The name or nutrients changed since these suggestions."
                    : suggestion ? `Suggested by ${suggestion.model}. Tap a tag to keep or drop it.`
                      : "Reads the recipe name and nutrients. Pick tags yourself too."}
            </span>
          </div>
          <div className="tag-choices" role="group" aria-label="Recipe tags">
            {CLASSIFIER_LABELS.map((label) => {
              const selected = Boolean(tags[label]);
              const suggested = suggestion?.labels.includes(label) ?? false;
              const score = suggestion?.scores[label];
              return (
                <button
                  type="button"
                  key={label}
                  className={`tag-choice ${selected ? "is-selected" : ""} ${suggested ? "is-suggested" : ""}`}
                  onClick={() => toggleTag(label)}
                  aria-pressed={selected}
                  title={score !== undefined ? `Classifier ${suggestion?.model.includes("ECC") ? "votes" : "probability"}: ${Math.round(score * 100)}%` : undefined}
                >
                  {suggested && <Sparkles size={12} aria-label="suggested" />}
                  {tagText(label)}
                  {suggested && score !== undefined && <span className="tag-score">{Math.round(score * 100)}%</span>}
                </button>
              );
            })}
          </div>
          <div className="tag-input">
            <div className="avoid-tags">
              {ownTags.map((label) => (
                <button type="button" key={label} className="avoid-tag" onClick={() => toggleTag(label)} aria-label={`Remove tag ${tagText(label)}`}>
                  {tagText(label)} <X size={11} aria-hidden="true" />
                </button>
              ))}
            </div>
            <div className="tag-entry">
              <input
                value={ownTag}
                maxLength={LIMITS.tag + 10}
                onChange={(event) => setOwnTag(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addOwnTag();
                  }
                }}
                placeholder="Your own tag, e.g. family favourite"
                aria-label="Add your own tag"
              />
              <button type="button" onClick={addOwnTag} disabled={!normalizeTag(ownTag)}>Add</button>
            </div>
          </div>
        </div>
        {saveError && <p className="form-error field-wide" role="alert">{saveError}</p>}
        <div className="preference-actions">
          <button type="button" className="text-button" onClick={() => { reset(); setSaved(null); }}>
            <RotateCcw size={15} aria-hidden="true" /> Clear the form
          </button>
          <button type="submit" className="preference-save" disabled={saveBusy || !title.trim() || ingredientCount === 0}>
            {saveBusy ? <LoaderCircle className="spin" size={16} aria-hidden="true" /> : <Check size={16} aria-hidden="true" />}
            {saveBusy ? "Saving…" : "Save recipe"}
          </button>
        </div>
      </form>
    </section>
  );
}
