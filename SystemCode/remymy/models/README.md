# Remymy hybrid recommendation service

This service implements the interaction-aware extension to the proposal's
content ranker. It combines:

- a pairwise BPR user–recipe latent-factor score trained from Food.com ratings;
- a `content_score` supplied by the existing query/content retriever; and
- a popularity/content fallback for unknown users or recipes without training
  interactions.

Hard dietary, allergy, ingredient-exclusion, and time constraints must be
applied before ranking. The model never acts as a safety filter.

## Dataset and token map

The source is the public Kaggle dataset
`shuyangli94/food-com-recipes-and-user-interactions`, version 2. The downloaded
archive used for this experiment has SHA-256:

```text
e7a43e623c616799f78a0479b550483450fd7ede4e0c40979db3458a82b5b913
```

`PP_recipes.csv` uses the legacy OpenAI-GPT BPE vocabulary (40,478 entries)
plus five control tokens:

| ID | Token | Meaning |
| ---: | --- | --- |
| 40478 | `</s>` | end of instruction step |
| 40479 | `</p>` | padding |
| 40480 | `<R>` | start of recipe/document |
| 40481 | `</R>` | end of recipe/document |
| 40482 | `<s>` | start of instruction step |

The `/v1/token-map` endpoint and `TokenMapper` decode these IDs. A complete
decode also needs the OpenAI-GPT `vocab.json`, configured with
`TOKEN_VOCAB_PATH`.

## Why the supplied splits are not used for CF evaluation

The Kaggle validation and test files have zero recipe overlap with the training
file. That is appropriate for the paper's held-out recipe-generation task, but
a collaborative item factor cannot be learned for a recipe absent from train.
`prepare_cf_splits.py` therefore rebuilds a per-user chronological split from
`RAW_interactions.csv`: latest interaction for test, second-latest for
validation, and earlier interactions for training.

The resulting warm-item coverage is 91.0% for validation and 90.5% for test.
Cold items are retained for product routing but excluded from collaborative
metric calculations.

## Reproduce the experiment

From `SystemCode/remymy/models`:

```bash
python prepare_cf_splits.py \
  --data-dir ../data/raw/food-com \
  --output-dir ../data/processed/food-com-cf

python train.py \
  --data-dir ../data/processed/food-com-cf \
  --output ../data/models/hybrid-foodcom-bpr.npz \
  --objective bpr --factors 32 --epochs 20 \
  --learning-rate 0.015 --decay 0.95 --regularization 0.01

python evaluate_hybrid.py \
  --data-dir ../data/processed/food-com-cf \
  --raw-dir ../data/raw/food-com \
  --artifact ../data/models/hybrid-foodcom-bpr.npz \
  --samples 6000 --output ../data/analysis/hybrid-evaluation.json
```

On the fixed 6,000-query sampled evaluation (99 unseen warm-item negatives per
positive), the tested scores were:

| Model | HR@10 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: |
| Popularity | 0.6393 | 0.4006 | 0.4573 |
| BPR only | 0.6375 | 0.3990 | 0.4557 |
| 75% BPR + 25% ingredient content | **0.6547** | **0.4085** | **0.4670** |

These are directional sampled-negative metrics, not full-catalog production
claims. The executed analysis is in `foodcom_collaborative_analysis.ipynb`.

## Run the service

```bash
pip install -r requirements.txt
MODEL_ARTIFACT_PATH=../data/models/hybrid-foodcom-bpr.npz \
TOKEN_VOCAB_PATH=../data/raw/food-com/openai-gpt-vocab.json \
python serve.py
```

Endpoints:

- `GET /health`
- `POST /v1/rank`
- `POST /v1/token-map`

Example rank body:

```json
{
  "user_id": 2046,
  "candidates": [
    {"recipe_id": 4684, "content_score": 0.82, "eligible": true},
    {"recipe_id": 517, "content_score": 0.61, "eligible": true}
  ],
  "limit": 6,
  "collaborative_weight": 0.75,
  "filter_seen": true
}
```
