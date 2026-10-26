"""Tag a food with recipe classes using the gated classifier chain of recipe_classification.ipynb.

Input: a food query (description, optionally nutrients per 100 g). Output: its labels among the 16 classes
(`generic` is exclusive: a generic food gets no other label) and each label's probability.

Pipeline, as in the notebook: product / adj / verb terms → frozen MiniLM vectors → trained self-attention encoder →
64-d item embedding; log1p nutrients → train medians for missing ones → scaler → 6-d UMAP (one row at a time); concat →
feature scaler → generic gate → classifier chain over the 15 specific labels (at least one label if not generic).

Works with every folder the notebook saves:
- model_artifacts/recipe_classifier (main logistic-regression model, the default),
- model_artifacts/recipe_classifier_best_precision / _best_recall (models.joblib),
- model_artifacts/recipe_classifier_ecc (50 members, majority vote; shows vote shares instead of probabilities).
Needs torch, scikit-learn, umap-learn, sentence-transformers, gliner, stanza.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import torch
from torch import nn

try:
    from .food_query import TermExtractor, add_query_arguments, item_from_args, iter_queries
except ImportError:  # run as a file
    from food_query import TermExtractor, add_query_arguments, item_from_args, iter_queries

# api/src/remymy_api/inference/weights/recipe_classifier: recipe_classifier.pt, preprocessing.joblib and config.json as the notebook writes them.
DEFAULT_ARTIFACTS = Path(__file__).resolve().parent / "weights" / "recipe_classifier"


class ItemAttentionEncoder(nn.Module):
    """Self-attention over one item's product/adj/verb terms, masked-mean pooled (same layers as the notebook)."""

    def __init__(self, term_vectors, n_labels, emb_dim, heads, n_types):
        super().__init__()
        self.term_emb = nn.Embedding.from_pretrained(torch.tensor(term_vectors), freeze=True, padding_idx=0)
        self.proj = nn.Linear(term_vectors.shape[1], emb_dim)
        self.type_emb = nn.Embedding(n_types, emb_dim)
        self.attn = nn.MultiheadAttention(emb_dim, heads, dropout=0.1, batch_first=True)
        self.norm1 = nn.LayerNorm(emb_dim)
        self.ff = nn.Sequential(nn.Linear(emb_dim, 2 * emb_dim), nn.GELU(), nn.Linear(2 * emb_dim, emb_dim))
        self.norm2 = nn.LayerNorm(emb_dim)
        self.head = nn.Linear(emb_dim, n_labels)

    def embed_vectors(self, vectors, type_ids):
        """`embed` from term vectors instead of vocabulary ids, so terms unseen in training can be used.
        One unpadded item: (1, n_terms, 384) vectors, (1, n_terms) type ids."""
        x = self.proj(vectors) + self.type_emb(type_ids)
        a, _ = self.attn(x, x, x)
        x = self.norm1(x + a)
        x = self.norm2(x + self.ff(x))
        return x.mean(1)


def _probability_of_one(model, X):
    return model.predict_proba(X)[:, list(model.classes_).index(1)]


class RecipeClassifier:
    def __init__(self, artifact_dir=DEFAULT_ARTIFACTS):
        artifact_dir = Path(artifact_dir)
        self.config = json.loads((artifact_dir / "config.json").read_text())
        self.label_cols = self.config["label_cols"]
        self.generic = self.label_cols.index(self.config["gate_label"])
        self.chain_cols = [self.label_cols.index(c) for c in self.config["chain_labels"]]

        saved = torch.load(artifact_dir / "recipe_classifier.pt", map_location="cpu", weights_only=False)
        cfg = saved["encoder_config"]
        self.token_types = cfg["token_types"]
        self.encoder = ItemAttentionEncoder(saved["term_vectors"], cfg["n_labels"], cfg["emb_dim"],
                                            cfg["attention_heads"], len(self.token_types))
        self.encoder.load_state_dict(saved["state_dict"])
        self.encoder.eval()
        self.term_vectors = saved["term_vectors"]
        self.term_index = {t: i + 1 for i, t in enumerate(saved["vocab"])}  # 0 = padding
        self._term_model = None

        self.prep = joblib.load(artifact_dir / "preprocessing.joblib")
        if (artifact_dir / "members").is_dir():  # ECC: majority vote of gated chains
            self.members = [joblib.load(p) for p in sorted((artifact_dir / "members").glob("*.joblib"))]
            self.kind = f"ECC of {len(self.members)} chains ({self.config['model']})"
        elif (artifact_dir / "models.joblib").exists():  # one gate, one or more chains averaged
            models = joblib.load(artifact_dir / "models.joblib")
            self.members = [{"generic_gate": models["generic_gate"], "chains": models["chains"]}]
            self.kind = self.config.get("name", self.config.get("model", "?"))
        else:  # main model: gate and chain live in preprocessing.joblib
            self.members = [{"generic_gate": self.prep["generic_gate"], "chains": [self.prep["chain"]]}]
            self.kind = f"logistic regression (C={self.config.get('lr_C')})"

    def load_term_model(self):
        """The encoder of terms the vocabulary does not hold, loaded (or downloaded) on the first call."""
        if self._term_model is None:
            from sentence_transformers import SentenceTransformer
            self._term_model = SentenceTransformer(self.config["term_model"], revision=self.config["term_revision"],
                                                   device="cpu")
        return self._term_model

    def _vector(self, term):
        if term in self.term_index:
            return self.term_vectors[self.term_index[term]], True
        return self.load_term_model().encode([term], normalize_embeddings=True)[0], False

    def features(self, item):
        terms = [(str(t).strip().lower(), k) for k, field in enumerate(self.token_types)
                 for t in item[field] if str(t).strip()]
        vectors, known = zip(*(self._vector(t) for t, _ in terms))
        with torch.no_grad():
            emb = self.encoder.embed_vectors(torch.tensor(np.stack(vectors))[None],
                                             torch.tensor([[k for _, k in terms]])).numpy()

        cols = self.prep["nutrient_cols"]
        raw = [item["nutrients"][c] for c in cols]
        nutrients = np.array([np.log1p(max(v, 0)) if v is not None else self.prep["nutrient_medians"][c]
                              for c, v in zip(cols, raw)])
        nut_umap = self.prep["nutrient_umap"].transform(self.prep["nutrient_scaler"].transform(nutrients[None]))
        X = self.prep["feature_scaler"].transform(np.hstack([emb, nut_umap]))
        return X, {"terms": [t for t, _ in terms], "known_terms": int(sum(known))}

    def _member_predict(self, member, X):
        """0/1 labels and probabilities of one gated pipeline (predict_labels / pipeline_proba of the notebook)."""
        gate = member["generic_gate"]
        chains = member.get("chains") or [member["chain"]]
        chain_proba = np.mean([c.predict_proba(X) for c in chains], axis=0)[0]
        p_generic = _probability_of_one(gate, X)[0]
        proba = np.zeros(len(self.label_cols))
        proba[self.generic] = p_generic
        proba[self.chain_cols] = (1 - p_generic) * chain_proba

        labels = np.zeros(len(self.label_cols), dtype=int)
        if gate.predict(X)[0]:
            labels[self.generic] = 1
        else:
            pred = chains[0].predict(X)[0].astype(int) if len(chains) == 1 else (chain_proba >= 0.5).astype(int)
            if not pred.any():
                pred[chain_proba.argmax()] = 1
            labels[self.chain_cols] = pred
        return labels, proba

    def predict(self, item):
        X, used = self.features(item)
        outputs = [self._member_predict(m, X) for m in self.members]
        if len(outputs) == 1:
            labels, scores = outputs[0]
        else:  # ecc_majority_vote of the notebook; scores are the vote shares
            scores = np.mean([labels for labels, _ in outputs], axis=0)
            labels = (scores > 0.5).astype(int)
            if labels[self.generic]:
                labels[:] = 0
                labels[self.generic] = 1
            elif not labels[self.chain_cols].any():
                labels[self.chain_cols[int(scores[self.chain_cols].argmax())]] = 1
        return {"item": item, **used, "model": self.kind,
                "labels": [c for c, on in zip(self.label_cols, labels) if on],
                "score_kind": "vote share" if len(outputs) > 1 else "probability",
                "scores": dict(sorted(zip(self.label_cols, scores.round(4).tolist()), key=lambda x: -x[1]))}


def print_prediction(result, top=6):
    item = result["item"]
    print(f"\n{item['description']}" + (f"  (fdc {item['fdc_id']}, from the lookup)" if "fdc_id" in item else ""))
    print(f"  terms {result['terms']} ({result['known_terms']}/{len(result['terms'])} seen in training)")
    given = {c: v for c, v in item["nutrients"].items() if v is not None}
    print(f"  nutrients given {given or 'none'} (others: training medians)")
    print(f"  → {', '.join(result['labels'])}")
    if "training" in item:
        true = item["training"]["labels"]
        print(f"  training labels {', '.join(true)}" + (" (same)" if set(true) == set(result["labels"]) else " (differs)"))
    print(f"  top {result['score_kind']}: "
          + ", ".join(f"{label} {score:.2f}" for label, score in list(result["scores"].items())[:top]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS,
                        help="folder holding recipe_classifier.pt, preprocessing.joblib and config.json")
    add_query_arguments(parser)
    args = parser.parse_args()

    classifier = RecipeClassifier(args.artifacts)
    extractor = TermExtractor()
    if not args.json:
        print(f"model: {classifier.kind}")
    for query in iter_queries(args):
        try:
            result = classifier.predict(item_from_args(query, args, extractor))
        except (ValueError, LookupError, OSError) as error:
            print(f"skipped: {error}")
            continue
        if args.json:
            print(json.dumps(result))
        else:
            print_prediction(result)


if __name__ == "__main__":
    main()
