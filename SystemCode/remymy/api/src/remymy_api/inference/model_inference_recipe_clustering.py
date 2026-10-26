"""Assign a recipe to a recipe cluster with the HDBSCAN model of recipe_clustering_foodcom.ipynb (trained on the
Food.com recipes) or of recipe_clustering.ipynb (the FDC foods); the bundle tells which.

Input: a food query (description, optionally nutrients per 100 g, a price and tags). Output: the cluster (-1 =
outlier), its name (top product terms), how strongly the 15 nearest training recipes agree, and the k nearest training
recipes inside the assigned cluster (all of them if it has fewer than k; the nearest of any cluster for an outlier),
optionally only among the training rows a caller allows (the ones its catalog holds).

Pipeline, as in the notebook's predict_clusters: lemmatized product / adj+verb terms and (Food.com) tags → Qwen3
embeddings mean-pooled per block, log1p nutrients, log1p price interval → per-block PCA / scaler / weight → 10-d UMAP
→ majority vote of the 15 nearest training recipes (noise votes too) → outlier if the nearest clustered recipe is beyond
the 99th percentile of training nearest-neighbour distances.

Unknown inputs are imputed so they pull towards no cluster: nutrients with the training medians (as the notebook does),
the price with the training mean of log price, and no tags as a recipe without any. Needs scikit-learn, umap-learn,
sentence-transformers, gliner, stanza.
"""
import argparse
import json
from pathlib import Path
import re

import joblib
import nltk
from nltk.stem import WordNetLemmatizer
import numpy as np

try:
    from .food_query import (DEFAULT_LOOKUP, NUTRIENT_COLS, TermExtractor, add_query_arguments, item_from_args,
                             iter_queries, load_fdc_lookup)
except ImportError:  # run as a file
    from food_query import (DEFAULT_LOOKUP, NUTRIENT_COLS, TermExtractor, add_query_arguments, item_from_args,
                            iter_queries, load_fdc_lookup)

# weights/recipe_clustering_foodcom: recipe_clustering.joblib as recipe_clustering_foodcom.ipynb writes it, its training
# recipes Food.com recipe_ids. (An FDC bundle of recipe_clustering.ipynb, by fdc_id, loads too; its neighbours are
# described from the premade fdc_id lookup, weights/fdc_lookup.json.)
DEFAULT_ARTIFACTS = Path(__file__).resolve().parent / "weights" / "recipe_clustering_foodcom"
DEFAULT_NEIGHBOURS = 5

nltk.download("wordnet", quiet=True)
nltk.download("omw-1.4", quiet=True)
_lemmatizer = WordNetLemmatizer()


def _lemmas(text, pos):
    return " ".join(_lemmatizer.lemmatize(w, pos) for w in re.findall(r"[a-z]+", text.lower()))


def _uniq(xs):
    return list(dict.fromkeys(x for x in xs if x))


def _tag_terms(tag):
    """A tag as the notebook's norm_tag makes it, from a Food.com tag or a stored label alike: "60-minutes-or-less" and
    "60_minutes_or_less" -> "60 minute or less" (digits kept, so the time tags stay apart)."""
    return " ".join(_lemmatizer.lemmatize(w, "n") for w in re.findall(r"[a-z0-9]+", str(tag).lower()))


class RecipeClusterer:
    """The fitted clustering bundle: token embedding cache, block transforms, UMAP and the training neighbours."""

    def __init__(self, artifact_dir=DEFAULT_ARTIFACTS, lookup=DEFAULT_LOOKUP):
        self.bundle = joblib.load(Path(artifact_dir) / "recipe_clustering.joblib")
        # the fitted HDBSCAN is training's alone: a query takes its neighbours' labels (train_labels) instead
        self.bundle.pop("hdbscan", None)
        # training recipes in 10-d UMAP space (fit_transform output, the points nn_all was fit on)
        self.train_Z = self.bundle["umap10"].embedding_
        self.lookup = load_fdc_lookup(lookup)
        self.config = self.bundle["config"]
        # the nutrients the model reads, in its order: food_query's all, for the FDC model; all but fiber and
        # cholesterol, which Food.com does not give, for the Food.com one
        self.nutrients = list(self.config["NUTRIENTS"])
        assert set(self.nutrients) <= set(NUTRIENT_COLS)
        # the training recipes' ids: Food.com recipe_ids, or the FDC model's fdc_ids
        self.ids_are_foodcom = "train_recipe_id" in self.bundle
        self.train_ids = np.asarray(self.bundle["train_recipe_id" if self.ids_are_foodcom else "train_fdc_id"])
        # the Food.com model's tags block, without the category headers it was trained without
        self.uses_tags = "tags" in self.bundle["block_fits"]
        self.generic_tags = {_tag_terms(tag) for tag in self.bundle.get("generic_tags", ())}
        self.tok2vec = dict(self.bundle["tok2vec"])
        self.dim = len(next(iter(self.tok2vec.values())))
        self._encoder = None
        # price block scaler mean = training mean of log1p([price_lo, price_hi]); used when the price is unknown
        self.default_price = np.expm1(self.bundle["block_fits"]["price"][1].mean_)

    def load_encoder(self):
        """The encoder of terms the token cache does not hold, loaded (or downloaded) on the first call."""
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(self.config["ENCODER_NAME"], device="cpu")
        return self._encoder

    def _encode_missing(self, tokens):
        new = sorted({t for t in tokens if t not in self.tok2vec})
        if new:
            self.tok2vec.update(zip(new, self.load_encoder().encode(new, normalize_embeddings=True)))
        return len(new)

    def _pool(self, tokens):
        if not tokens:
            return np.zeros(self.dim, dtype=np.float32)
        v = np.mean([self.tok2vec[t] for t in tokens], axis=0)
        return v / (np.linalg.norm(v) + 1e-9)

    def features(self, item):
        """The weighted feature row of the notebook's `assemble`, plus what went into it. item["tags"] (labels or
        Food.com tags) feeds the tags block when the model has one; none is a recipe without tags."""
        product = _uniq(_lemmas(x, "n") for x in item["product"])
        desc = _uniq([_lemmas(a, "a") for a in item["adj"]] + [_lemmas(v, "v") for v in item["verb"]])
        tags = (_uniq(t for t in map(_tag_terms, item.get("tags") or ()) if t not in self.generic_tags)
                if self.uses_tags else [])
        unseen = self._encode_missing(product + desc + tags)

        medians = self.bundle["nutr_medians"]
        nutrients = np.array([item["nutrients"].get(c) if item["nutrients"].get(c) is not None else medians[c]
                              for c in self.nutrients], dtype=float)
        if item.get("price_interval"):  # a looked-up training food: its own ±3-per-unpriced-item interval
            price = np.array(item["price_interval"], dtype=float)
        elif item["price"] is not None:  # a known total: no unpriced items, so the interval is the price itself
            price = np.array([item["price"], item["price"]], dtype=float)
        else:
            price = self.default_price
        raw = {"product": self._pool(product)[None], "adj_verb": self._pool(desc)[None],
               "tags": self._pool(tags)[None], "nutrients": np.log1p(nutrients)[None], "price": np.log1p(price)[None]}

        # in the order the blocks were fitted, which is the order the notebook assembled them in
        blocks = []
        for name, (pca, scaler) in self.bundle["block_fits"].items():
            R = raw[name]
            Z = scaler.transform(pca.transform(R) if pca else R)
            blocks.append(Z / np.sqrt(Z.shape[1]) * self.config["WEIGHTS"][name])
        return np.hstack(blocks), {"product_norm": product, "desc_norm": desc, "tags_norm": tags,
                                   "unseen_tokens": unseen,
                                   "nutrients_used": dict(zip(self.nutrients, nutrients.round(3).tolist())),
                                   "price_interval": price.round(2).tolist()}

    def neighbours(self, z, cluster, k, allowed=None):
        """The k training recipes of `cluster` closest to `z` in UMAP space (any cluster when `cluster` is -1), only
        among the rows `allowed` (a bool mask over the training rows) holds when one is given. Each one's "id" is its
        training id: a Food.com recipe_id ("recipe_id") or an fdc_id ("fdc_id")."""
        labels = self.bundle["train_labels"]
        rows = np.arange(len(labels)) if cluster == -1 else np.flatnonzero(labels == cluster)
        if allowed is not None:
            rows = rows[allowed[rows]]
        dist = np.linalg.norm(self.train_Z[rows] - z, axis=1)
        nearest = []
        for i in np.argsort(dist)[:k]:
            train_id = int(self.train_ids[rows[i]])
            neighbour = {"id": train_id, "cluster": int(labels[rows[i]]), "distance": float(dist[i])}
            if self.ids_are_foodcom:
                neighbour["recipe_id"] = train_id
            else:
                food = self.lookup.get(train_id, {})
                nutrients = food.get("nutrients", {})
                neighbour.update({"fdc_id": train_id, "description": food.get("description"), "price": food.get("price"),
                                  **{key: nutrients.get(key) for key in ("energy_kcal", "protein_g", "fat_g", "carb_g")}})
            nearest.append(neighbour)
        return nearest, len(rows)

    def predict(self, item, k=DEFAULT_NEIGHBOURS, allowed=None):
        b = self.bundle
        X, used = self.features(item)
        Z = b["umap10"].transform(X)
        _, idx = b["nn_all"].kneighbors(Z)
        votes = b["train_labels"][idx[0]]
        counts = np.bincount(votes + 1)  # +1 shift so noise (-1) can vote
        cluster = int(counts.argmax() - 1)
        d_near = float(b["nn_clustered"].kneighbors(Z, n_neighbors=1)[0][0, 0])
        too_far = d_near > b["outlier_dist"]
        top = [(int(c - 1), int(n)) for c, n in sorted(enumerate(counts), key=lambda x: -x[1]) if n][:3]
        assigned = -1 if too_far else cluster
        nearest, cluster_size = self.neighbours(Z[0], assigned, k, allowed)
        return {
            "item": item, **used,
            "cluster": assigned,
            "cluster_size": None if assigned == -1 else cluster_size,
            "neighbours": nearest,
            "cluster_name": "outlier" if too_far or cluster == -1 else b["cluster_names"][cluster],
            "outlier_reason": ("farther than any clustered recipe" if too_far
                               else "most neighbours are noise" if cluster == -1 else None),
            "neighbour_votes": [{"cluster": c, "name": b["cluster_names"].get(c, "noise"), "votes": n,
                                 "share": n / len(votes)} for c, n in top],
            "nearest_clustered_distance": d_near, "outlier_distance": float(b["outlier_dist"]),
        }


def print_prediction(result):
    item = result["item"]
    print(f"\n{item['description']}" + (f"  (fdc {item['fdc_id']}, from the lookup)" if "fdc_id" in item else ""))
    print(f"  product {result['product_norm']}  adj/verb {result['desc_norm']}"
          + (f"  tags {result['tags_norm']}" if result["tags_norm"] else "")
          + (f"  ({result['unseen_tokens']} new tokens embedded)" if result["unseen_tokens"] else ""))
    given = {c: v for c, v in item["nutrients"].items() if v is not None}
    print(f"  nutrients given {given or 'none'} (others: training medians)")
    lo, hi = result["price_interval"]
    if item["price"] is None:
        print(f"  price unknown → ${lo:.2f}–${hi:.2f} (training mean)")
    else:
        print(f"  price ${item['price']:.2f}" + (f" (${lo:.2f}–${hi:.2f} with unpriced items)" if lo != hi else ""))
    label = f"cluster {result['cluster']}: {result['cluster_name']}" if result["cluster"] >= 0 else "outlier (-1)"
    print(f"  → {label}" + (f"  [{result['outlier_reason']}]" if result["outlier_reason"] else ""))
    if "training" in item:
        print(f"  training cluster {item['training']['cluster']}"
              + (" (same)" if item["training"]["cluster"] == result["cluster"] else " (differs)"))
    print("  neighbour votes: " + ", ".join(f"{v['cluster']} {v['name']} {v['share']:.0%}" for v in result["neighbour_votes"]))
    print(f"  nearest clustered recipe {result['nearest_clustered_distance']:.3f} "
          f"(outlier beyond {result['outlier_distance']:.3f})")
    shown = result["neighbours"]
    if result["cluster"] >= 0:
        print(f"  nearest in cluster {result['cluster']} ({len(shown)} of {result['cluster_size']}):")
    else:
        print(f"  nearest training recipes of any cluster ({len(shown)}):")
    for n in shown:
        stats = "  ".join(fmt.format(n[key]) for key, fmt in [("price", "${:g}"), ("energy_kcal", "{:g} kcal"),
                                                               ("protein_g", "P {:g}"), ("fat_g", "F {:g}"),
                                                               ("carb_g", "C {:g}")]
                          if n.get(key) is not None)
        where = f"cluster {n['cluster']}, " if result["cluster"] < 0 else ""
        which = f"recipe {n['recipe_id']}" if "recipe_id" in n else f"fdc {n['fdc_id']}"
        print(f"    {n['distance']:.3f}  {n.get('description') or '?'}  ({where}{which}{'; ' + stats if stats else ''})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS,
                        help="folder holding recipe_clustering.joblib")
    parser.add_argument("--price", type=float, help="total price in SGD (overrides a price in the query)")
    parser.add_argument("-k", "--neighbours", type=int, default=DEFAULT_NEIGHBOURS,
                        help="nearest training recipes to show from the assigned cluster (default %(default)s)")
    add_query_arguments(parser)
    args = parser.parse_args()

    clusterer = RecipeClusterer(args.artifacts, args.lookup)
    extractor = TermExtractor()
    for query in iter_queries(args):
        try:
            result = clusterer.predict(item_from_args(query, args, extractor), k=args.neighbours)
        except (ValueError, LookupError, OSError) as error:
            print(f"skipped: {error}")
            continue
        if args.json:
            print(json.dumps(result, default=float))
        else:
            print_prediction(result)


if __name__ == "__main__":
    main()
