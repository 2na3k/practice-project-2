"""Assign a food to a recipe cluster with the HDBSCAN model of recipe_clustering.ipynb.

Input: a food query (description, optionally nutrients per 100 g and a price). Output: the cluster (-1 = outlier), its
name (top product terms), how strongly the 15 nearest training recipes agree, and the k nearest training recipes inside
the assigned cluster (all of them if it has fewer than k; the nearest of any cluster for an outlier).

Pipeline, as in the notebook's predict_clusters: lemmatized product / adj+verb terms → Qwen3 embeddings mean-pooled per
block, log1p nutrients, log1p price interval → per-block PCA / scaler / weight → 10-d UMAP → majority vote of the 15
nearest training recipes (noise votes too) → outlier if the nearest clustered recipe is beyond the 99th percentile of
training nearest-neighbour distances.

Unknown inputs are imputed so they pull towards no cluster: nutrients with the training medians (as the notebook does)
and the price with the training mean of log price. Needs scikit-learn, umap-learn, sentence-transformers, gliner, stanza.
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

# api/weights/recipe_clustering: recipe_clustering.joblib as the training notebook writes it. Neighbours are described
# from the premade fdc_id lookup (api/weights/fdc_lookup.json); without it they show only their fdc_id.
DEFAULT_ARTIFACTS = Path(__file__).resolve().parents[3] / "weights" / "recipe_clustering"
DEFAULT_NEIGHBOURS = 5

nltk.download("wordnet", quiet=True)
nltk.download("omw-1.4", quiet=True)
_lemmatizer = WordNetLemmatizer()


def _lemmas(text, pos):
    return " ".join(_lemmatizer.lemmatize(w, pos) for w in re.findall(r"[a-z]+", text.lower()))


def _uniq(xs):
    return list(dict.fromkeys(x for x in xs if x))


class RecipeClusterer:
    """The fitted clustering bundle: token embedding cache, block transforms, UMAP and the training neighbours."""

    def __init__(self, artifact_dir=DEFAULT_ARTIFACTS, lookup=DEFAULT_LOOKUP):
        self.bundle = joblib.load(Path(artifact_dir) / "recipe_clustering.joblib")
        # training recipes in 10-d UMAP space (fit_transform output, the points nn_all was fit on)
        self.train_Z = self.bundle["umap10"].embedding_
        self.lookup = load_fdc_lookup(lookup)
        self.config = self.bundle["config"]
        assert self.config["NUTRIENTS"] == NUTRIENT_COLS
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
        """The 59-d weighted feature row of the notebook's `assemble`, plus what went into it."""
        product = _uniq(_lemmas(x, "n") for x in item["product"])
        desc = _uniq([_lemmas(a, "a") for a in item["adj"]] + [_lemmas(v, "v") for v in item["verb"]])
        unseen = self._encode_missing(product + desc)

        medians = self.bundle["nutr_medians"]
        nutrients = np.array([item["nutrients"][c] if item["nutrients"][c] is not None else medians[c]
                              for c in NUTRIENT_COLS], dtype=float)
        if item.get("price_interval"):  # a looked-up training food: its own ±3-per-unpriced-item interval
            price = np.array(item["price_interval"], dtype=float)
        elif item["price"] is not None:  # a known total: no unpriced items, so the interval is the price itself
            price = np.array([item["price"], item["price"]], dtype=float)
        else:
            price = self.default_price
        raw = {"product": self._pool(product)[None], "adj_verb": self._pool(desc)[None],
               "nutrients": np.log1p(nutrients)[None], "price": np.log1p(price)[None]}

        blocks = []
        for name, R in raw.items():
            pca, scaler = self.bundle["block_fits"][name]
            Z = scaler.transform(pca.transform(R) if pca else R)
            blocks.append(Z / np.sqrt(Z.shape[1]) * self.config["WEIGHTS"][name])
        return np.hstack(blocks), {"product_norm": product, "desc_norm": desc, "unseen_tokens": unseen,
                                   "nutrients_used": dict(zip(NUTRIENT_COLS, nutrients.round(3).tolist())),
                                   "price_interval": price.round(2).tolist()}

    def neighbours(self, z, cluster, k):
        """The k training recipes of `cluster` closest to `z` in UMAP space (any cluster when `cluster` is -1)."""
        labels = self.bundle["train_labels"]
        rows = np.arange(len(labels)) if cluster == -1 else np.flatnonzero(labels == cluster)
        dist = np.linalg.norm(self.train_Z[rows] - z, axis=1)
        nearest = []
        for i in np.argsort(dist)[:k]:
            fdc_id = int(self.bundle["train_fdc_id"][rows[i]])
            food = self.lookup.get(fdc_id, {})
            nutrients = food.get("nutrients", {})
            nearest.append({"fdc_id": fdc_id, "description": food.get("description"),
                            "cluster": int(labels[rows[i]]), "distance": float(dist[i]), "price": food.get("price"),
                            **{key: nutrients.get(key) for key in ("energy_kcal", "protein_g", "fat_g", "carb_g")}})
        return nearest, len(rows)

    def predict(self, item, k=DEFAULT_NEIGHBOURS):
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
        nearest, cluster_size = self.neighbours(Z[0], assigned, k)
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
        print(f"    {n['distance']:.3f}  {n['description'] or '?'}  ({where}fdc {n['fdc_id']}{'; ' + stats if stats else ''})")


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
