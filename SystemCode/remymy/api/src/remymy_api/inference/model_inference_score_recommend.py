"""Recommend catalog recipes to a user: HNSW retrieval over item embeddings, then the window Transformer + cross-attention
reranker of model_training_reranker_avgemb.ipynb.

1. Index (once per model and catalog): every recipe of the catalog, the foods RecipeRepository reads from the
   catalog database, is embedded by the reranker's ItemEncoder, i.e. its product / adj / verb tokens concatenated with
   its standardized [price, nutrients], and the embeddings go into an HNSW index with cosine distance.
2. Query: the user's representative embedding is the average of their history recipes' embeddings.
3. Retrieval: HNSW returns the recipes closest to it by cosine similarity, the history's own recipes excluded, sorted
   by that similarity, most similar first. Under the user's constraints (diet, budget), the search
   is routed first (recommender.MD): the foods are clustered by their tags into macro-clusters, each a Roaring
   bitmap of its rows; the constraints are matched against the clusters' centroids for the rows they allow, and by
   how large a share of the catalog those are, HNSW searches them through a bitset mask or they are scanned exactly.
4. Near-duplicates of the history: the candidates go into a dictionary of buckets by that similarity ([0.9, 1],
   [0.8, 0.9), ...). Each history recipe gives a range of it, the similarities a recipe at cosine MAX_SIMILARITY or
   more to that history recipe can have; the buckets the range overlaps are looked up, a window slides over their
   candidates inside the range, and the ones at cosine MAX_SIMILARITY or more to the history recipe are dropped. (The
   catalog has many near-identical recipes under different fdc_ids, e.g. the same food as patties and as crumbles.)
5. User state: the history is cut into windows wherever two ratings are window_gap_days (about 6 months) or more
   apart, the last max_windows windows with their last t_max ratings each. One Transformer runs over the ratings of
   all of them together, so each rating attends within its own window and across the others, and gives one state per
   rating. The user profile is the history's mean [price, nutrients], each nutrient mixed with the user's preferred
   value of it (the rating-weighted mean, as sampling_preference.ipynb computes it).
6. Reranking: each candidate attends over [user profile; one state per history rating] and gets P(like); candidates
   are sorted by it.
7. Diversity: maximal marginal relevance (MMR) picks the top_k from the reranked candidates, each pick maximizing
   lambda * P(like) - (1 - lambda) * its highest cosine similarity to an earlier pick, so near-identical recipes do
   not fill the list. A candidate whose cosine to a pick is MAX_SIMILARITY or more is never picked, so the list can
   be shorter than top_k.

Usage, from api/src:
    python -m remymy_api.inference.model_inference_score_recommend build --catalog path/to/remymy-catalog.db
    python -m remymy_api.inference.model_inference_score_recommend recommend --catalog path/to/remymy-catalog.db \\
        --user path/to/retrieval_user.json
"""
import argparse
import hashlib
import heapq
import json
import logging
import math
import re
import tempfile
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import hnswlib
import numpy as np
import pandas as pd
import torch
from pyroaring import BitMap
from torch import nn
from torch.nn import functional as F
from torch.nn.utils.rnn import pack_padded_sequence

from .model_inference_reranker_avgemb import THRESHOLD, ItemEncoder, Reranker as BaseReranker, UserEncoder

logger = logging.getLogger(__name__)

# api/src/remymy_api/inference/weights/window_transformer_reranker: model.pt and config.json as model_training_reranker_avgemb.ipynb saves its
# window Transformer reranker (WINDOW_ARTIFACT_DIR there). The older windowed GRU in weights/gru_xattn_reranker
# still loads: Reranker tells the two apart by their checkpoints.
DEFAULT_ARTIFACTS = Path(__file__).resolve().parent / "weights" / "window_transformer_reranker"

HERE = Path(__file__).resolve().parent
DEFAULT_INDEX_DIR = Path(tempfile.gettempdir()) / "remymy-retrieval-index"
DEFAULT_USER = HERE / "examples" / "retrieval_user.json"
TEXT_COLUMNS = ("product", "adj", "verb")
# Reranker nutrient input key (NUTRIENT_INPUTS) → catalog column.
CATALOG_NUTRIENTS = {"calories_kcal": "energy_kcal", "fat_g": "fat_g", "sugar_g": "sugars_g",
                     "sodium_mg": "sodium_mg", "protein_g": "protein_g", "saturated_fat_g": "satfat_g",
                     "carbohydrates_g": "carb_g"}
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_SEARCH = 200
# P(like) weight in MMR, 1 - MMR_LAMBDA the diversity weight. Candidates are all near the user embedding (cosine 0.8 to
# 1 among them), and without the cutoff below, at 0.6 or more exact duplicates of the catalog still reach the example
# users' top 10.
MMR_LAMBDA = 0.5
# Near-duplicate cutoff (cosine): a candidate this close to a history recipe or to a pick is never recommended,
# whatever its P(like).
MAX_SIMILARITY = 0.97
# Candidates are bucketed by cosine to the user embedding: [0.9, 1], [0.8, 0.9), ...
BUCKET_WIDTH = 0.1
# Widens the history ranges past float32 rounding in HNSW's and torch's cosines, so none is missed at a range's edge.
RANGE_SLACK = 1e-4
SEED = 42

# The preference conditions the inverted bitmap index holds, one bitmap each over the catalog rows. A diet is a screen
# of a food's name and product terms, not a certification. Cuisines have none: the catalog has no cuisine data.
MEAT_TERMS = ("beef", "bison", "veal", "pork", "bacon", "ham", "prosciutto", "lard", "tallow", "chicken", "turkey",
              "duck", "lamb", "mutton", "venison", "sausage", "meat", "goat")
SEAFOOD_TERMS = ("fish", "salmon", "tuna", "anchovy", "sardine", "cod", "shrimp", "prawn", "shellfish", "crab",
                 "lobster", "oyster", "mussel", "clam", "scallop", "squid", "octopus", "seafood")
PORK_TERMS = ("pork", "bacon", "ham", "prosciutto", "lard", "pancetta", "salami", "pepperoni", "chorizo")
ALCOHOL_TERMS = ("wine", "beer", "rum", "vodka", "whiskey", "brandy", "liqueur", "sake", "alcohol")
BEEF_TERMS = ("beef", "veal", "steak", "tallow", "brisket")
# The food data's column for each special_diet: True when the food is fit for that diet. A food without a value
# there is screened by its words instead (DIET_EXCLUDES).
DIET_COLUMNS = {"vegetarian": "vegetarian", "halal": "halal", "Hinduism": "hinduism"}
DIET_EXCLUDES = {
    "vegetarian": MEAT_TERMS + SEAFOOD_TERMS,
    "halal": PORK_TERMS + ALCOHOL_TERMS,
    "Hinduism": BEEF_TERMS,
}
# A food is rich in a nutrient when it has at least this quantile of the catalog's values (per 100 g).
RICH_QUANTILE = 0.75
# Cluster-first routing of a search under the user's constraints (recommender.MD). The inverted bitmap index holds
# one tag per special_diet a user can set (vegetarian, halal, Hinduism): each food carries the ones whose screen it
# passes. A budget is a number, not a category, so it is checked against each food's price instead; the foods of a
# tag-set group are stored cheapest first, so a budget keeps one contiguous stretch at the start of each group.
# The foods' tag sets are clustered agglomeratively (average linkage, Jaccard distance) and cut here. On the catalog:
# the 3 diet tags make 7 distinct tag sets, cut into 6 macro-clusters. (With price bands and rich nutrients as tags,
# routed rows broke into 20x more runs: price bands left any budget between two bands scattered.)
TAG_CLUSTER_THRESHOLD = 0.4
# Index selection by the share of the catalog a query's constraints match: from BROAD_MATCH_SHARE up, HNSW over the
# matching rows through a bitset mask; under it, an exact scan of them. Under 10%, masked HNSW needs ef raised to about
# k / share to reach k matches: on the catalog it took 3-3.4 ms at recall 0.999 for 1-10% matches, against 0.4-0.8 ms
# for the exact scan. The scan costs about m (the matching rows), HNSW about k * N / m, so they cross near
# m = sqrt(k * N): 9.7% of the catalog for k = 100.
BROAD_MATCH_SHARE = 0.10
# The exact scan's distance calculation runs in buckets of SCAN_BUCKET rows on a pool of SCAN_THREADS threads (1 runs
# it inline): each thread owns a contiguous group of buckets (thread_groups) and scores them in order, a main thread
# redirects buckets to any that runs out (ThreadExecutor), and the priority queue then takes the k nearest. Measured whole scans (64 dimensions, k = 100), 4 threads against inline:
#   catalog, 839 rows:                                0.20 ms against 0.14 ms: one or two buckets are too little to share
#   2M-row synthetic, 200,000 rows in contiguous groups:  6.6 ms against 9.2 ms (a task per bucket took 9.2 ms)
#   2M-row synthetic, 1,000,000 rows in contiguous groups: 27 ms against 35 ms (a task per bucket took 48 ms)
SCAN_BUCKET = 1000
SCAN_THREADS = 4
# A run of at least this many matching rows stored side by side is scanned where it lies; shorter ones are gathered
# into one block first, as a small gather costs less than a bucket per short run.
SCAN_MIN_RUN = 256


# ---------------------------------------------------------------------------------------------------------------
# History windows: the same functions as model_training_reranker_avgemb.ipynb, so serving cuts the history
# exactly as training did.
# ---------------------------------------------------------------------------------------------------------------
def window_starts(user_codes, days, gap_days):
    """Index of the first rating of each rating's window. Rows are sorted by user, then day. A window ends where
    the user changes or where the next rating comes gap_days or more after the previous one."""
    new_window = np.ones(len(days), dtype=bool)
    new_window[1:] = (user_codes[1:] != user_codes[:-1]) | (np.diff(days) >= gap_days)
    return np.maximum.accumulate(np.where(new_window, np.arange(len(days)), 0))


def history_windows(prior_end, user_start, mu_before, first_of_window, recipe_idx, ratings, max_windows, t_max):
    """Each row's history as windows, most recent window first.

    A row's history is ratings[user_start:prior_end]. Returns recipe indices (B, max_windows, t_max), -1 at padding;
    ratings centered on mu_before (B, max_windows, t_max), 0 at padding; window lengths (B, max_windows), 0 for
    missing windows. Each window keeps its last t_max ratings, in date order."""
    steps = np.arange(t_max)
    history_idx = np.full((len(prior_end), max_windows, t_max), -1, dtype=np.int64)
    history_rating = np.zeros((len(prior_end), max_windows, t_max), dtype=np.float32)
    window_len = np.zeros((len(prior_end), max_windows), dtype=np.int64)
    ends = np.asarray(prior_end, dtype=np.int64).copy()
    for k in range(max_windows):
        alive = ends > user_start
        if not alive.any():
            break
        # The window of the last rating before `ends`; it never reaches before user_start (a user change starts one).
        starts = np.where(alive, first_of_window[np.maximum(ends - 1, 0)], ends)
        length = np.minimum(ends - starts, t_max)
        positions = np.clip(ends[:, None] - length[:, None] + steps, 0, len(recipe_idx) - 1)
        real = steps < length[:, None]
        history_idx[:, k] = np.where(real, recipe_idx[positions], -1)
        history_rating[:, k] = np.where(real, ratings[positions] - mu_before[:, None], 0.0)
        window_len[:, k] = length
        ends = starts
    return history_idx, history_rating, window_len


def history_day(value):
    """Day number (days since 1970-01-01) of a history date; a missing date is day 0."""
    if value is None or value == "":
        return 0
    return int(np.datetime64(pd.Timestamp(value).date(), "D").astype(np.int64))


# ---------------------------------------------------------------------------------------------------------------
# The windowed model: the HistoryEncoder and GRUCrossAttentionReranker of the notebook (same parameter names).
# ---------------------------------------------------------------------------------------------------------------
class WindowHistoryEncoder(nn.Module):
    """History windows → the final GRU state of each window; the GRU restarts from zero in every window."""

    def __init__(self, config):
        super().__init__()
        self.rating_projection = nn.Linear(1, config["d"])
        self.gru = nn.GRU(config["d"], config["d"], batch_first=True)

    def forward(self, history_emb, history_rating, window_len):
        """Window states (B, K, d) and their padding mask (B, K), True = missing window."""
        batch, windows, steps, d = history_emb.shape
        steps_in = (history_emb + self.rating_projection(history_rating.unsqueeze(-1))).reshape(-1, steps, d)
        lengths = window_len.reshape(-1).cpu()
        real = lengths > 0
        packed = pack_padded_sequence(steps_in[real.to(steps_in.device)], lengths[real], batch_first=True,
                                      enforce_sorted=False)
        _, last = self.gru(packed)  # (1, windows, d): each window's state after its last real step.
        states = steps_in.new_zeros(batch * windows, d)
        states[real.to(states.device)] = last[0]
        return states.view(batch, windows, d), (window_len == 0).to(history_emb.device)


class WindowGRUCrossAttentionReranker(nn.Module):
    """Candidate item_emb cross-attends over [U_profile; w_1 … w_K], w_1 the most recent window's state."""

    def __init__(self, vocab_size, n_numeric, n_profile, n_fields, config):
        super().__init__()
        d = config["d"]
        self.item_encoder = ItemEncoder(vocab_size, n_numeric, n_fields, config)
        self.user_encoder = UserEncoder(n_profile, config)
        self.history_encoder = WindowHistoryEncoder(config)
        self.key_type = nn.Embedding(2, d)  # 0: profile token, 1: window state.
        self.window_position = nn.Embedding(config["max_windows"], d)  # 0: most recent window.
        self.cross_attention = nn.MultiheadAttention(d, config["n_heads"], dropout=config["dropout"],
                                                     batch_first=True)
        self.head = nn.Sequential(nn.Linear(2 * d, d), nn.ReLU(), nn.Dropout(config["dropout"]), nn.Linear(d, 1))

    def user_keys(self, history_emb, history_rating, window_len, profile):
        """Keys and values [U_profile; w_1 … w_K] (B, 1+K, d) and their padding mask (True = padded)."""
        states, padded = self.history_encoder(history_emb, history_rating, window_len)
        states = states + self.window_position.weight[:states.size(1)] + self.key_type.weight[1]
        profile_token = self.user_encoder(profile).unsqueeze(1) + self.key_type.weight[0]
        keys = torch.cat([profile_token, states], dim=1)
        return keys, torch.cat([padded.new_zeros(len(padded), 1), padded], dim=1)

    def score_candidates(self, candidate_emb, keys, key_padding_mask):
        """Logits (B, C) for candidates (B, C, d)."""
        attended, _ = self.cross_attention(candidate_emb, keys, keys, key_padding_mask=key_padding_mask,
                                           need_weights=False)
        return self.head(torch.cat([attended, candidate_emb], dim=-1)).squeeze(-1)


# ---------------------------------------------------------------------------------------------------------------
# The window Transformer: UserEncoder, WindowTransformerHistoryEncoder and WindowTransformerReranker of
# model_training_reranker_avgemb.ipynb (same parameter names, so its model.pt loads as it is). The notebook's
# reranker also holds its catalog as buffers; recipes here are the API's catalog, so items are encoded with
# item_encoder and the index caches them.
# ---------------------------------------------------------------------------------------------------------------
class PreferenceUserEncoder(nn.Module):
    """Standardized [mean log price, mean log nutrients] of the history, each nutrient a learned weighted average with
    the user's preferred value of it → U_profile (the notebook's UserEncoder)."""

    def __init__(self, n_profile, config):
        super().__init__()
        self.log_weights = nn.Parameter(torch.zeros(2))  # log [x, y]: history average, preferred value.
        self.mlp = nn.Sequential(nn.Linear(n_profile, config["d"]), nn.ReLU(), nn.Linear(config["d"], config["d"]))

    def forward(self, profile, preferences_nutrient):
        """profile (B, n_profile) is [price, *nutrients]; preferences_nutrient (B, n) holds the preferred values of its
        last n columns, on the same scale. Each of those columns becomes (x * profile + y * preferred) / (x + y)."""
        n = preferences_nutrient.size(1)
        x, y = self.log_weights.exp()
        nutrients = (x * profile[:, -n:] + y * preferences_nutrient) / (x + y)
        return self.mlp(torch.cat([profile[:, :-n], nutrients], dim=1))


class WindowTransformerHistoryEncoder(nn.Module):
    """History windows → one self-attention state per history rating (the notebook's WindowTransformerHistoryEncoder)."""

    def __init__(self, config):
        super().__init__()
        d = config["d"]
        self.rating_projection = nn.Linear(1, d)
        self.window_position = nn.Embedding(config["max_windows"], d)  # 0: the most recent window.
        self.sub_position = nn.Embedding(config["t_max"], d)  # Inside a window, 0: its most recent rating.
        layer = nn.TransformerEncoderLayer(d, config["n_heads"], dim_feedforward=2 * d, dropout=config["dropout"],
                                           batch_first=True, norm_first=True)
        self.self_attention = nn.TransformerEncoder(layer, config["history_layers"], norm=nn.LayerNorm(d),
                                                    enable_nested_tensor=False)

    def forward(self, history_emb, history_rating, window_len):
        """States (B, L, d) and their padding mask (B, L), True = padded: row b's ratings fill its first
        window_len[b].sum() slots, in (window, step) order, window 0 the most recent and each window in date order.

        history_emb (B, K, T, d) and history_rating (B, K, T) hold window k's ratings, left-aligned; window_len (B, K)
        counts them, 0 for a window the user does not have."""
        window_len = window_len.to(history_emb.device)
        windows, steps = history_emb.shape[1:3]
        steps_back = window_len.unsqueeze(-1) - 1 - torch.arange(steps, device=history_emb.device)  # (B, K, T)
        real = steps_back >= 0
        tokens = (history_emb + self.rating_projection(history_rating.unsqueeze(-1))
                  + self.window_position.weight[:windows, None] + self.sub_position(steps_back.clamp(min=0)))
        # Each row's real tokens move to its first slots, so self-attention skips the padding of short windows.
        count = window_len.sum(1)
        padded = torch.arange(int(count.max()), device=history_emb.device) >= count.unsqueeze(1)
        packed = tokens.new_zeros(*padded.shape, tokens.size(-1))
        packed[~padded] = tokens[real]
        return self.self_attention(packed, src_key_padding_mask=padded), padded


class WindowTransformerReranker(nn.Module):
    """Candidate item_emb cross-attends over [U_profile; one state per history rating]."""

    def __init__(self, vocab_size, n_numeric, n_profile, n_fields, config):
        super().__init__()
        d = config["d"]
        self.item_encoder = ItemEncoder(vocab_size, n_numeric, n_fields, config)
        self.user_encoder = PreferenceUserEncoder(n_profile, config)
        self.history_encoder = WindowTransformerHistoryEncoder(config)
        self.key_type = nn.Embedding(2, d)  # 0: profile token, 1: history state.
        self.cross_attention = nn.MultiheadAttention(d, config["n_heads"], dropout=config["dropout"],
                                                     batch_first=True)
        self.head = nn.Sequential(nn.Linear(2 * d, d), nn.ReLU(), nn.Dropout(config["dropout"]), nn.Linear(d, 1))

    def user_keys(self, history_emb, history_rating, window_len, profile, preferences_nutrient):
        """Keys and values [U_profile; h_1 … h_L] (B, 1+L, d) and their padding mask (True = padded)."""
        states, padded = self.history_encoder(history_emb, history_rating, window_len)
        profile_token = self.user_encoder(profile, preferences_nutrient).unsqueeze(1)
        keys = torch.cat([profile_token + self.key_type.weight[0], states + self.key_type.weight[1]], dim=1)
        return keys, torch.cat([padded.new_zeros(len(padded), 1), padded], dim=1)

    def score_candidates(self, candidate_emb, keys, key_padding_mask):
        """Logits (B, C) for candidates (B, C, d). Each candidate attends to its own user's keys only."""
        attended, _ = self.cross_attention(candidate_emb, keys, keys, key_padding_mask=key_padding_mask,
                                           need_weights=False)
        return self.head(torch.cat([attended, candidate_emb], dim=-1)).squeeze(-1)


class Reranker(BaseReranker):
    """The reranker, scoring candidates from item embeddings. Which model it is comes from the artifacts:

    - window_transformer: the window Transformer of model_training_reranker_avgemb.ipynb (its WINDOW_ARTIFACT_DIR).
      History cut into windows at gaps of window_gap_days or more, ratings centered on the history's mean and mapped
      to [0, 1], and a profile with the user's preferred nutrients.
    - window_gru: the older windowed GRU (weights/gru_xattn_reranker): the same windows, centered ratings.
    - gru: the oldest model, of model_inference_reranker_avgemb_approach.py: the last t_max recipes in one sequence."""

    def __init__(self, artifact_dir=DEFAULT_ARTIFACTS, tagger=None, device=None):
        artifact_dir = Path(artifact_dir)
        saved = json.loads((artifact_dir / "config.json").read_text())
        self.windowed = "max_windows" in saved["config"]
        if not self.windowed:
            self.kind = "gru"
            super().__init__(artifact_dir, tagger, device)
            # Models trained with [0, 1] history ratings save offset 5 and scale 10; older ones only a scale.
            self.rating_offset = saved.get("history_rating_offset", 0.0)
            return
        # A windowed model: the base class's attributes, another model class.
        self.config = saved["config"]
        self.text_fields = saved["text_fields"]
        self.nutrient_columns = saved["nutrient_columns"]
        self.token_lookup = {token: index for index, token in enumerate(saved["vocabulary"])}
        self.item_mean, self.item_std = np.array(saved["item_log1p_mean"]), np.array(saved["item_log1p_std"])
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        state = torch.load(artifact_dir / "model.pt", map_location="cpu", weights_only=True)
        self.n_numeric = state["item_encoder.numeric_projection.weight"].shape[1]
        n_profile = state["user_encoder.mlp.0.weight"].shape[1]
        self.use_rating = True
        if "history_encoder.sub_position.weight" in state:
            self.kind = "window_transformer"
            # Price is z-scored and the nutrients min-max scaled (the notebook's profile_shift / profile_scale); the
            # preferred nutrients are put on the nutrients' scale, columns [1:].
            self.profile_shift, self.profile_scale = np.array(saved["profile_shift"]), np.array(saved["profile_scale"])
            self.rating_offset, self.rating_scale = saved["history_rating_offset"], saved["history_rating_scale"]
            self.model = WindowTransformerReranker(len(self.token_lookup), self.n_numeric, n_profile,
                                                   len(self.text_fields), self.config)
        else:
            self.kind = "window_gru"
            self.profile_shift, self.profile_scale = np.array(saved["profile_mean"]), np.array(saved["profile_std"])
            self.rating_offset, self.rating_scale = 0.0, 1.0  # Centered, not scaled.
            self.model = WindowGRUCrossAttentionReranker(len(self.token_lookup), self.n_numeric, n_profile,
                                                         len(self.text_fields), self.config)
        self.model.load_state_dict(state)
        self.model.to(self.device).eval()
        self.tagger = tagger
        logger.info("Loaded the %s reranker from %s on %s (vocabulary %d tokens).", self.kind.replace("_", " "),
                    artifact_dir, self.device, len(self.token_lookup))

    @property
    def history_model(self) -> str:
        """What reads the history, for the pipeline's log."""
        return "the Transformer" if self.kind == "window_transformer" else "the GRU"

    def centered_ratings(self, history):
        """The history's ratings centered on their mean, as in training: the mean before t is the mean of the whole
        history here. A recipe without a rating counts as the user's mean, i.e. centered 0."""
        ratings = np.array([np.nan if recipe.get("rating") is None else recipe["rating"] for recipe in history],
                           dtype=np.float64)
        return np.zeros(len(history)) if np.isnan(ratings).all() else np.nan_to_num(ratings - np.nanmean(ratings))

    def normalized_ratings(self, history):
        """History ratings as the model reads them: (centered + offset) / scale. The window Transformer maps them to
        [0, 1] (offset 5, scale 10); the windowed GRU uses them centered only (offset 0, scale 1)."""
        if not self.use_rating:
            return np.zeros(len(history), dtype=np.float32)  # The model has no rating input.
        return ((self.centered_ratings(history) + self.rating_offset) / self.rating_scale).astype(np.float32)

    def windows(self, history):
        """The windows of history (date order) that the model sees, most recent first: positions into history
        (K, T), -1 at padding; ratings as the model reads them (K, T), 0 at padding; lengths (K,)."""
        centered = self.centered_ratings(history)
        days = np.array([history_day(recipe.get("date")) for recipe in history], dtype=np.int64)
        count = len(history)
        positions, ratings, lengths = history_windows(
            np.array([count]), np.array([0]), np.array([0.0]),
            window_starts(np.zeros(count, dtype=np.int64), days, self.config["window_gap_days"]), np.arange(count),
            centered, self.config["max_windows"], self.config["t_max"])
        windows, steps = int((lengths[0] > 0).sum()), int(lengths[0].max())
        positions, ratings = positions[0, :windows, :steps], ratings[0, :windows, :steps]
        # The notebook's window_batch: (centered + offset) / scale at real steps, 0 at padding.
        ratings = np.where(positions >= 0, (ratings + self.rating_offset) / self.rating_scale, 0.0).astype(np.float32)
        return positions, ratings, lengths[0, :windows]

    def _profile(self, history):
        """The standardized mean log1p [price, nutrients] of the history (1, n_profile)."""
        profile = np.mean([self.log_numeric(recipe) for recipe in history], axis=0)[-len(self.profile_shift):]
        profile = (profile - self.profile_shift) / self.profile_scale
        return torch.as_tensor(profile, dtype=torch.float32, device=self.device).unsqueeze(0)

    def _preferences(self, history):
        """The user's preferred nutrients (1, n), as sampling_preference.ipynb computes them and the notebook's
        rerank_candidates serves them: the mean of the history's nutrients, each rating weighted by 1 / 2^(5 - rating),
        rounded to the nearest integer (a half up), then on the profile's log1p scale and scaled with its statistics.
        A recipe without a rating weighs as the user's mean rating; with no ratings at all, every recipe weighs the
        same."""
        ratings = np.array([np.nan if recipe.get("rating") is None else recipe["rating"] for recipe in history],
                           dtype=np.float64)
        ratings = np.full(len(history), 5.0) if np.isnan(ratings).all() else np.where(np.isnan(ratings),
                                                                                       np.nanmean(ratings), ratings)
        weights = 1 / 2.0 ** (5 - ratings)
        nutrients = np.expm1(np.stack([self.log_numeric(recipe) for recipe in history])[:, 1:])
        preferences = np.log1p(np.floor(weights @ nutrients / weights.sum() + 0.5))
        preferences = (preferences - self.profile_shift[1:]) / self.profile_scale[1:]
        return torch.as_tensor(preferences, dtype=torch.float32, device=self.device).unsqueeze(0)

    def _window_inputs(self, history, history_emb):
        positions, ratings, lengths = self.windows(history)
        positions = torch.from_numpy(positions)
        window_emb = history_emb[positions.clamp(min=0)] * (positions >= 0).unsqueeze(-1)
        return (window_emb.unsqueeze(0).to(self.device), torch.from_numpy(ratings).unsqueeze(0).to(self.device),
                torch.from_numpy(lengths).unsqueeze(0))

    @torch.no_grad()
    def window_states(self, history, history_emb):
        """One state (K, d) per window of the history, most recent first. The windowed GRU's is each window's final
        hidden state; the window Transformer has one state per rating, so a window's is the mean of its ratings'."""
        window_emb, ratings, lengths = self._window_inputs(history, history_emb)
        states, _ = self.model.history_encoder(window_emb, ratings, lengths)
        if self.kind == "window_gru":
            return states[0].cpu()
        # The Transformer's states are in (window, step) order: window 0's ratings first.
        per_window = torch.split(states[0, :int(lengths.sum())], lengths[0].tolist())
        return torch.stack([window.mean(0) for window in per_window]).cpu()

    @torch.no_grad()
    def user_keys(self, history, history_emb):
        """The user's attention keys and their padding mask, for history (date order) and its item embeddings."""
        if self.kind == "window_transformer":
            return self.model.user_keys(*self._window_inputs(history, history_emb), self._profile(history),
                                        self._preferences(history))
        if self.kind == "window_gru":
            return self.model.user_keys(*self._window_inputs(history, history_emb), self._profile(history))
        # Oldest model: the last t_max history recipes in date order go to the GRU as one sequence.
        t_max = self.config["t_max"]
        recent = history_emb[-t_max:].to(self.device).unsqueeze(0)
        history_rating = torch.as_tensor(self.normalized_ratings(history)[-t_max:], device=self.device).unsqueeze(0)
        profile = (np.mean([self.log_numeric(recipe) for recipe in history], axis=0)[-len(self.profile_mean):]
                   - self.profile_mean) / self.profile_std
        return self.model.user_keys(recent, history_rating, torch.tensor([recent.size(1)]),
                                    torch.as_tensor(profile, dtype=torch.float32, device=self.device).unsqueeze(0))

    @torch.no_grad()
    def score(self, history, history_emb, candidate_emb):
        """P(like) (C,) of candidates for the user who rated history, from item embeddings.

        history is in date order and history_emb (len(history), d) holds its item embeddings; candidate_emb is
        (C, d). Both come from encode_items or from the index's cache of it. The profile is the mean over all."""
        keys, key_padding_mask = self.user_keys(history, history_emb)
        # Each candidate is a separate query, so scoring them together equals scoring them one at a time.
        logits = self.model.score_candidates(candidate_emb.to(self.device).unsqueeze(0), keys, key_padding_mask)[0]
        return torch.sigmoid(logits).cpu()


def fingerprint(*paths, data=b""):
    """sha256 over the files' bytes and data: the index is stale when the model or the catalog changes."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    digest.update(data)
    return digest.hexdigest()


@torch.no_grad()
def encode_catalog(reranker, recipes, batch_size=1024):
    """item_emb (N, d) of every recipe, in catalog order."""
    return torch.cat([reranker.encode_items(recipes[start:start + batch_size])[0].cpu()
                      for start in range(0, len(recipes), batch_size)])


def average_linkage(distance, threshold, sizes=None, leaf_order=False):
    """The cluster of each row (0 to C - 1) under average-linkage agglomerative clustering of a square distance
    matrix, cut at threshold: scipy's linkage(method="average") with fcluster(threshold, "distance"), without scipy.
    sizes counts each row as that many identical points (1 each by default), so a group of duplicates is clustered
    once. distance is overwritten.

    When distances tie, average linkage depends on the order of the merges, in scipy as here (on 1,500 of the
    catalog's foods, whose 1.1 million tag distances take 71 values, scipy gave 32 to 35 clusters by row order). The
    order here only follows the rows', so the same rows always give the same clusters.

    Nearest-neighbour chain: follow each cluster's nearest neighbour until two are each other's nearest, merge them
    and update the merged row's distances by Lance-Williams (the size-weighted mean of the two). O(n^2) time.

    With leaf_order, also the rows in the order of the tree's leaves: each merge puts its two sides next to each other,
    so every sub-cluster, at every level of the tree, is one contiguous stretch of that order. Of the four ways to put
    two sides together (either first, either reversed), a merge takes the one whose meeting rows are nearest."""
    n = len(distance)
    original = distance.copy() if leaf_order else None
    np.fill_diagonal(distance, np.inf)
    size = np.ones(n) if sizes is None else np.asarray(sizes, dtype=float).copy()
    active = np.ones(n, dtype=bool)
    merges = []  # (height, a, b): a and b merged at that distance
    leaves = [[row] for row in range(n)]  # the rows under each cluster, in leaf order
    chain = []
    for _ in range(n - 1):
        while True:
            if not chain:
                chain.append(int(np.flatnonzero(active)[0]))
            a = chain[-1]
            b = int(np.argmin(distance[a]))
            # ties go to the previous cluster on the chain, so the chain always ends
            if len(chain) > 1 and distance[a, chain[-2]] <= distance[a, b]:
                b = chain[-2]
            if len(chain) > 1 and b == chain[-2]:
                break
            chain.append(b)
        chain.pop()
        chain.pop()
        merges.append((float(distance[a, b]), a, b))
        merged = (size[a] * distance[a] + size[b] * distance[b]) / (size[a] + size[b])
        distance[a], distance[:, a] = merged, merged
        distance[a, a] = np.inf
        distance[b], distance[:, b] = np.inf, np.inf
        size[a] += size[b]
        active[b] = False
        if leaf_order:
            left, right = leaves[a], leaves[b]
            arrangements = [(left, right), (left, right[::-1]), (left[::-1], right), (left[::-1], right[::-1])]
            first, second = min(arrangements, key=lambda pair: original[pair[0][-1], pair[1][0]])
            leaves[a] = first + second
        else:
            leaves[a] += leaves[b]
        leaves[b] = []
    # average linkage is monotone, so the cut keeps every merge at or below the threshold, whatever order they came in
    parent = np.arange(n)

    def root(row):
        while parent[row] != row:
            parent[row] = parent[parent[row]]
            row = parent[row]
        return row

    for height, a, b in merges:
        # Jaccard distances are ratios of small counts, so merges land exactly on a round threshold: float rounding
        # must not decide which side
        if height <= threshold + 1e-9:
            parent[root(a)] = root(b)
    labels = np.unique([root(row) for row in range(n)], return_inverse=True)[1].reshape(-1)
    if leaf_order:
        return labels, np.array([row for rows in leaves for row in rows], dtype=np.intp)
    return labels


def jaccard_distances(sets):
    """1 - |A & B| / |A | B| between every two rows of a (rows, tags) bool matrix; 0 between two empty sets."""
    members = np.asarray(sets, dtype=np.float64)
    intersection = members @ members.T
    sizes = members.sum(1)
    union = sizes[:, None] + sizes[None, :] - intersection
    return np.where(union > 0, 1.0 - intersection / np.maximum(union, 1.0), 0.0)


def recipe_words(recipe):
    """The lowercased words of a recipe's name and product terms, which the diet conditions screen."""
    products = recipe.get("entities", {}).get("product", [])
    phrases = [recipe.get("name", "")] + [" ".join(phrase) if isinstance(phrase, list) else phrase for phrase in products]
    return set(re.findall(r"[a-z]+", " ".join(phrases).lower()))


def condition_bitmaps(recipes):
    """Which foods meet each condition a preference can ask for: {condition: bitmap}, each bitmap a bool array over the
    catalog rows, True (1) where the row meets the condition, False (0) where it does not. The foods' tags
    (food_tags), which the macro-clusters group them by.

    "diet:<diet>" for each special_diet: the food's value for it in its "special_diet" columns (DIET_COLUMNS) when it
    has one, and otherwise none of the diet's excluded terms in its name or product terms. "nutrient:<key>" for each
    nutrient: at least the catalog's RICH_QUANTILE of it, for a preferred_nutrient; it does not filter a search yet,
    so food_tags leaves it out."""
    words = [recipe_words(recipe) for recipe in recipes]
    labels = [recipe.get("special_diet") or {} for recipe in recipes]
    bitmaps = {f"diet:{diet}": np.array([bool(label[DIET_COLUMNS[diet]])
                                         if label.get(DIET_COLUMNS[diet]) is not None
                                         else not row_words.intersection(terms)
                                         for label, row_words in zip(labels, words)], dtype=bool)
               for diet, terms in DIET_EXCLUDES.items()}
    for key in CATALOG_NUTRIENTS:
        values = np.array([float(recipe.get("nutrients", {}).get(key, np.nan)) for recipe in recipes])
        threshold = np.nanquantile(values, RICH_QUANTILE) if np.isfinite(values).any() else np.inf
        with np.errstate(invalid="ignore"):
            bitmaps[f"nutrient:{key}"] = values >= threshold
    return bitmaps


def nutrient_key(preferred):
    """The catalog nutrient key a preferred_nutrient names ("protein" → "protein_g"), or None."""
    name = re.sub(r"[^a-z]+", "_", str(preferred).lower()).strip("_")
    return next((key for key in CATALOG_NUTRIENTS if key == name or key.startswith(name + "_") or key.split("_")[0] == name),
                None)


def preference_conditions(preferences):
    """The conditions a user's preferences require of every food a search returns, besides the budget: their
    special_diet. The preferred_nutrient does not filter (it is for later)."""
    return [f"diet:{preferences['special_diet']}"] if preferences.get("special_diet") else []


def food_tags(recipes):
    """The tag names and a (rows, tags) bool matrix of which food carries which: one tag per special_diet a user can
    set, the only categorical condition a search filters by. The rich-nutrient bitmaps stay out (the preferred
    nutrient does not filter), and so does the price, a number the budget is checked against directly."""
    bitmaps = {name: bitmap for name, bitmap in condition_bitmaps(recipes).items() if name.startswith("diet:")}
    return list(bitmaps), np.stack(list(bitmaps.values()), axis=1)


@dataclass
class MacroCluster:
    """Foods with overlapping tags: its members as a Roaring bitmap of catalog rows, its centroid (the share of its
    members carrying each tag), and its groups, the rows of each distinct tag set in it."""

    members: BitMap
    centroid: np.ndarray
    groups: list  # [(tags: (n_tags,) bool, rows: BitMap)]


def macro_clusters(tag_matrix, threshold=TAG_CLUSTER_THRESHOLD):
    """Steps 1 and 2 of recommender.MD: the foods' tag sets clustered agglomeratively by Jaccard distance (average
    linkage) and cut at threshold, each cluster an inverted Roaring bitmap of its rows. Foods with the same tag set are
    clustered once, weighted by how many share it: the same as clustering every food, with the 10,658 foods down to
    their 692 tag sets. The tag sets come sorted, so the same catalog always gives the same clusters."""
    tag_sets, set_of_row, counts = np.unique(tag_matrix, axis=0, return_inverse=True, return_counts=True)
    set_of_row = set_of_row.reshape(-1)
    labels, leaves = average_linkage(jaccard_distances(tag_sets), threshold, counts, leaf_order=True)
    rows_of_set = [BitMap(np.flatnonzero(set_of_row == tag_set).tolist()) for tag_set in range(len(tag_sets))]
    # clusters and their tag-set groups in the tree's leaf order: below the cut the tree keeps clustering the groups by
    # Jaccard distance, so the sub-clusters of a cluster, at every level, sit next to each other, and the groups a query
    # keeps (sharing its tags) tend to be neighbours
    clusters = []
    for cluster in dict.fromkeys(labels[leaves].tolist()):
        sets = leaves[labels[leaves] == cluster]
        weights = counts[sets].astype(float)
        clusters.append(MacroCluster(
            members=BitMap.union(*(rows_of_set[tag_set] for tag_set in sets)),
            centroid=(tag_sets[sets] * weights[:, None]).sum(0) / weights.sum(),
            groups=[(tag_sets[tag_set], rows_of_set[tag_set]) for tag_set in sets]))
    return clusters


def thread_groups(num_buckets, num_threads):
    """The buckets each thread scores, as a 2D array: one contiguous group of bucket numbers per thread,
    num_buckets / num_threads each, the first num_buckets % num_threads groups taking one more. A thread scores its
    group in order, so the memory it reads stays contiguous rather than round-robin buckets: with 6 buckets on 3
    threads, [0, 1], [2, 3], [4, 5], not [0, 3], [1, 4], [2, 5]."""
    threads = max(1, min(num_threads, num_buckets))
    per_thread, extra = divmod(num_buckets, threads)
    groups, first = [], 0
    for thread in range(threads):
        count = per_thread + (thread < extra)
        groups.append(list(range(first, first + count)))
        first += count
    return groups

class ThreadExecutor:
    """Sub-threads that run buckets, kept track of by a main thread (recommender.MD).

    Each sub-thread owns a queue of buckets: _allocate() puts its contiguous group there (thread_groups), and it takes
    them from the front, so it reads memory in order; with its queue empty it sleeps until buckets are put in it. Every
    sub-thread reports its status to a queue.Queue, (thread, buckets left in its queue), after each bucket, and puts 0
    there when it has none left. The main thread, run(), reads the statuses; when a sub-thread puts 0 it assigns
    buckets to it: half of those left in the busiest queue, from its back (so the busy sub-thread's next buckets stay
    its own and in order). Assigning is the critical part, guarded by a semaphore of num_threads: an assignment enters
    it, puts the buckets in the sub-thread's queue, and releases it. Half rather than one bucket, as each redirect is a
    round trip through the main thread: on 200,000 rows it took 19.9 ms against 22.4 ms with uneven buckets, 8.3
    against 9.0 ms with even ones. One run at a time."""

    def __init__(self, num_threads):
        self.num_threads = num_threads
        self.threads = []
        # each sub-thread's buckets: a deque, as the sub-thread takes the front and a redirect takes the back
        self.thread_queue = [deque() for _ in range(num_threads)]  # list of queue
        self.lock = threading.Lock()  # guards the thread queues
        # one per sub-thread, on the queues' lock: putting buckets in a queue wakes its sub-thread
        self.ready = [threading.Condition(self.lock) for _ in range(num_threads)]
        # set when the main thread puts buckets in a sub-thread's queue, cleared when the sub-thread wakes for them: it
        # then puts its 0 once, even if its buckets were redirected away before it ran
        self.assigned = [False] * num_threads
        self.semaphore = threading.Semaphore(num_threads)  # guards assigning buckets to a sub-thread
        self.status = queue.Queue()  # (thread, buckets left in its queue); 0 once it is free
        self.run_lock = threading.Lock()  # one run at a time
        self.work = None  # what the current run does with a bucket
        self.error = None
        self.ran_by = []
        self.closed = False
        # allocate threads here and put to self.threads
        for index in range(num_threads):
            thread = threading.Thread(target=self._thread_run, args=(index,), name=f"remymy-scan-{index}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def _thread_run(self, index):
        """A sub-thread: sleeps until buckets are put in its queue, then takes them from the front, reporting what it has
        left after each one. Once its queue is empty, its buckets done or the rest redirected away, it puts 0: every
        time, so the main thread is never left waiting for it."""
        own, ready = self.thread_queue[index], self.ready[index]
        while True:
            with ready:
                while not self.assigned[index] and not self.closed:
                    ready.wait()
                if self.closed:
                    return
                self.assigned[index] = False
            while True:
                with self.lock:
                    if not own:
                        break
                    bucket = own.popleft()
                    left = len(own)
                try:
                    self.work(bucket)
                    self.ran_by[bucket] = index
                except Exception as error:  # stop the run: no bucket more, and run() raises it
                    self.error = error
                    with self.lock:
                        for other in self.thread_queue:
                            other.clear()
                if left:
                    self.status.put((index, left))
            self.status.put((index, 0))  # free: the main thread assigns it buckets, or it is done

    def _allocate(self, groups):
        """Each sub-thread's contiguous group of buckets into its queue, waking it; the sub-threads past the groups
        get none."""
        with self.lock:
            for index, own in enumerate(self.thread_queue):
                own.clear()
                own.extend(groups[index] if index < len(groups) else ())
                if own:
                    self.assigned[index] = True
                    self.ready[index].notify()

    def _redirect(self, index, left):
        """Half the buckets left in the queue with the most, by the last statuses (at least one), from its back into
        sub-thread index's queue, in their order, waking it; how many moved, 0 when every queue is empty."""
        with self.lock:
            for donor in sorted(range(self.num_threads), key=lambda other: -left[other]):
                own = self.thread_queue[donor]
                if own:
                    moved = [own.pop() for _ in range(max(1, len(own) // 2))]
                    left[donor] = len(own)
                    self.thread_queue[index].extend(reversed(moved))
                    self.assigned[index] = True
                    self.ready[index].notify()
                    return len(moved)
                left[donor] = 0
            return 0

    def run(self, num_buckets, work):
        """The main thread: work(bucket) for every bucket, on the sub-threads; which sub-thread ran each one."""
        with self.run_lock:
            groups = thread_groups(num_buckets, self.num_threads)
            self.work, self.error, self.ran_by = work, None, [None] * num_buckets
            with self.semaphore:  # the critical part: assigning each sub-thread its group
                self._allocate(groups)
            left = [len(group) for group in groups] + [0] * (self.num_threads - len(groups))
            idle = deque(range(len(groups), self.num_threads))  # free from the start: no group of their own
            done = 0
            while done < self.num_threads:
                while not idle:  # wait for a sub-thread to put 0: it is free
                    index, count = self.status.get()
                    left[index] = count
                    if count == 0:
                        idle.append(index)
                index = idle.popleft()
                with self.semaphore:  # the critical part: assigning that sub-thread buckets, then released
                    assigned = self.error is None and self._redirect(index, left)
                if not assigned:
                    done += 1
            if self.error is not None:
                raise self.error
            return self.ran_by

    def close(self):
        """End the sub-threads."""
        with self.lock:
            self.closed = True
            for ready in self.ready:
                ready.notify()


_scan_executors = {}
_scan_executors_lock = threading.Lock()


def scan_executor(num_threads):
    """The ThreadExecutor of num_threads sub-threads the exact scan runs its buckets on: made on first use, shared."""
    with _scan_executors_lock:
        if num_threads not in _scan_executors:
            _scan_executors[num_threads] = ThreadExecutor(num_threads)
        return _scan_executors[num_threads]


class RecipeIndex:
    """Item embeddings of the catalog and an HNSW (cosine) index over them. Labels are catalog row positions.

    With the recipes, it also routes a search under the user's constraints (recommender.MD): the foods are clustered by
    their tags into macro-clusters (macro_clusters), each an inverted Roaring bitmap of its rows; a query's required
    tags are matched against the clusters' centroids to find the rows it may return, and by the share of the catalog
    they are, HNSW searches them through a bitset mask or they are scanned exactly."""

    def __init__(self, fdc_ids, embeddings, index, recipes=None):
        self.fdc_ids = list(fdc_ids)
        self.embeddings = embeddings  # (N, d) float32, not normalized: the reranker takes them as they are.
        self.index = index
        # built from the recipes, whether the HNSW index was built or loaded: cheap, and always in step with them
        self.tags, self.clusters, self.prices = [], [], None
        if recipes is not None:
            self.prices = np.array([float(recipe.get("price", 0.0)) for recipe in recipes])
            self.tags, tag_matrix = food_tags(recipes)
            self.clusters = macro_clusters(tag_matrix)
        # Structure of arrays for the exact scan: the unit embeddings by column, each dimension's values for every row
        # contiguous in memory, so scoring a bucket is one multiply-add per dimension across all its rows (SIMD). The
        # rows are stored macro-cluster by macro-cluster and tag-set group by group, so a group is one contiguous range
        # of every column, and inside a group cheapest first, so a budget keeps the start of each group: the rows a
        # query routes to are a few long runs read in place rather than gathered.
        # order[position] is the row stored there, position_of[row] where a row is stored.
        self.order = (np.concatenate([group[np.argsort(self.prices[group], kind="stable")]
                                      for cluster in self.clusters for _, rows in cluster.groups
                                      for group in [np.asarray(rows.to_array(), dtype=np.intp)]])
                      if self.clusters else np.arange(len(self.fdc_ids)))
        self.position_of = np.empty_like(self.order)
        self.position_of[self.order] = np.arange(len(self.order))
        unit = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-12)
        self.columns = np.ascontiguousarray(unit[self.order].T, dtype=np.float32)  # (d, N), in storage order

    def required_tags(self, preferences):
        """Step 3.1: the tags a query's constraints require, as a bool vector over self.tags (its special_diet), and
        its budget (0 is no limit), which route() checks against each food's price."""
        required = np.zeros(len(self.tags), dtype=bool)
        budget = float(preferences.get("budget") or 0)
        for tag in preference_conditions(preferences):
            if tag in self.tags:
                required[self.tags.index(tag)] = True
            else:
                logger.debug("%r is not a tag of the catalog, so it does not narrow the search.", tag)
        return required, budget

    def route(self, preferences, exclude=()):
        """Step 3.2: the rows a search under the user's preferences may return, as a Roaring bitmap, and how many
        macro-clusters were taken whole, in part or skipped; None when nothing constrains the search.

        A cluster whose centroid has a required tag at 0 (no member carries it) is skipped, one whose centroid has
        every required tag at 1 (every member carries them) is taken whole, and of the rest only the tag-set groups
        carrying every required tag. The budget is then checked against each row's price."""
        required, budget = self.required_tags(preferences)
        if not required.any() and budget <= 0:
            return None
        matching = BitMap()
        plan = {"whole": 0, "partial": 0, "skipped": 0}
        for cluster in self.clusters:
            carried = cluster.centroid[required]
            if (carried == 0).any():
                plan["skipped"] += 1
            elif (carried == 1).all():
                plan["whole"] += 1
                matching |= cluster.members
            else:
                plan["partial"] += 1
                for tags, rows in cluster.groups:
                    if tags[required].all():
                        matching |= rows
        if exclude:
            matching -= BitMap(int(row) for row in exclude)
        if budget > 0:
            rows = np.asarray(matching.to_array(), dtype=np.intp)
            matching = BitMap(rows[self.prices[rows] <= budget].tolist())
        return matching, plan

    @classmethod
    def build(cls, reranker, recipes):
        embeddings = encode_catalog(reranker, recipes).numpy()
        index = hnswlib.Index(space="cosine", dim=embeddings.shape[1])
        index.init_index(max_elements=len(recipes), ef_construction=HNSW_EF_CONSTRUCTION, M=HNSW_M, random_seed=SEED)
        index.add_items(embeddings, np.arange(len(recipes)))
        return cls([recipe["fdc_id"] for recipe in recipes], embeddings, index, recipes)

    def save(self, index_dir, source_fingerprint):
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)
        np.save(index_dir / "item_embeddings.npy", self.embeddings)
        self.index.save_index(str(index_dir / "hnsw.bin"))
        (index_dir / "meta.json").write_text(json.dumps({
            "fingerprint": source_fingerprint, "dim": int(self.embeddings.shape[1]), "M": HNSW_M,
            "ef_construction": HNSW_EF_CONSTRUCTION, "fdc_ids": self.fdc_ids}))

    @classmethod
    def load(cls, index_dir, recipes=None):
        """The saved index; with the recipes it was built from, their macro-clusters and prices too."""
        index_dir = Path(index_dir)
        meta = json.loads((index_dir / "meta.json").read_text())
        index = hnswlib.Index(space="cosine", dim=meta["dim"])
        index.load_index(str(index_dir / "hnsw.bin"), max_elements=len(meta["fdc_ids"]))
        return cls(meta["fdc_ids"], np.load(index_dir / "item_embeddings.npy"), index, recipes)

    @classmethod
    def load_or_build(cls, reranker, recipes, index_dir, source_fingerprint, rebuild=False):
        """The saved index when it was built from the same model and catalog; otherwise a new one, saved."""
        meta_path = Path(index_dir) / "meta.json"
        if not rebuild and meta_path.exists() and json.loads(meta_path.read_text())["fingerprint"] == source_fingerprint:
            logger.info("Reusing the HNSW index in %s: the model and the catalog are unchanged.", index_dir)
            return cls.load(index_dir, recipes)
        reason = "rebuild asked for" if rebuild else "none saved" if not meta_path.exists() else "model or catalog changed"
        started = time.perf_counter()
        built = cls.build(reranker, recipes)
        built.save(index_dir, source_fingerprint)
        logger.info("Built the HNSW index over %d recipes in %.1fs (%s); saved to %s.", len(recipes),
                    time.perf_counter() - started, reason, index_dir)
        return built

    def search(self, query, k, conditions=None, exclude=(), ef=HNSW_EF_SEARCH, log=None):
        """Up to k (row, cosine similarity) nearest to query, most similar first, rows in exclude left out.

        With conditions (the user's preferences), only the rows route() finds may come back (step 3.3 of
        recommender.MD): from BROAD_MATCH_SHARE of the catalog up, HNSW searches them through a bitset mask, and under
        it they are scanned exactly. When HNSW cannot reach k of them, they are scanned exactly too."""
        log = log or (lambda *_: None)
        query = np.asarray(query, dtype=np.float32).reshape(-1)
        routed = self.route(conditions or {}, exclude) if self.clusters else None
        if routed is None:
            exclude = set(exclude)
            # Ask for enough neighbours that k remain after dropping the excluded rows.
            n = min(k + len(exclude), self.index.get_current_count())
            if k <= 0 or n == 0:
                return []
            self.index.set_ef(max(ef, n))
            rows, distances = self.index.knn_query(query.reshape(1, -1), k=n)
            return [(int(row), 1.0 - float(distance)) for row, distance in zip(rows[0], distances[0])
                    if row not in exclude][:k]

        matching, plan = routed
        share = len(matching) / max(len(self.fdc_ids), 1)
        n = min(k, len(matching))
        if n <= 0:
            results, how = [], "nothing to search"
        elif share < BROAD_MATCH_SHARE:
            results, how = self.scan(query, matching, n), "exact scan"
        else:
            results, how = self.masked_hnsw(query, matching, n, ef), "HNSW through a bitset mask"
            if results is None:
                results, how = self.scan(query, matching, n), "exact scan (HNSW could not reach k)"
        log(f"  routing: {plan['whole']} macro-clusters taken whole, {plan['partial']} in part, {plan['skipped']} "
            f"skipped of {len(self.clusters)}; {len(matching):,} matching rows ({share:.2%} of the catalog) -> {how}")
        return results

    def masked_hnsw(self, query, matching, n, ef):
        """The n nearest of the matching rows by HNSW, bitset masking the rest: its filter admits a row only when the
        Roaring bitmap holds it. None when the search cannot reach n of them."""
        self.index.set_ef(max(ef, n))
        try:
            rows, distances = self.index.knn_query(query.reshape(1, -1), k=n, filter=matching.__contains__)
        except RuntimeError:
            return None
        return [(int(row), 1.0 - float(distance)) for row, distance in zip(rows[0], distances[0])]

    def scan(self, query, rows, k):
        """The exact k nearest of rows (a Roaring bitmap) to query: a SIMD brute-force distance calculation over the
        structure-of-arrays columns, in buckets of SCAN_BUCKET rows split into one contiguous group per thread of a
        pool of SCAN_THREADS, then a priority queue (a min-heap) of the k most similar.

        The matching rows that are stored side by side (whole tag-set groups, for a routed query) are scored where they
        lie, as contiguous runs of every column; only the rest are gathered, once, into one contiguous block. Each
        bucket writes its similarities into its own slice of one array."""
        query = np.ascontiguousarray(query / (np.linalg.norm(query) + 1e-12), dtype=np.float32)
        ids = np.frombuffer(rows.to_array(), dtype=np.uint32)  # the bitmap's row ids read in place, not copied
        positions = self.position_of[ids]
        if not len(positions):
            return []
        if len(positions) * math.log2(len(positions) + 1) > len(self.order):
            # many rows: marking them in a storage-wide mask puts them in order faster than sorting
            stored = np.zeros(len(self.order), dtype=bool)
            stored[positions] = True
            positions = np.flatnonzero(stored)
        else:
            positions = np.sort(positions)

        # runs of consecutive storage positions (in SoA), and which of them are long enough to scan in place
        # divide into segment which is contiguous
        starts = np.flatnonzero(np.r_[True, np.diff(positions) != 1])
        lengths = np.diff(np.r_[starts, len(positions)])
        long_run = lengths >= SCAN_MIN_RUN
        segments = [(self.columns[:, first:first + length], np.arange(first, first + length))
                    for first, length in zip(positions[starts[long_run]].tolist(), lengths[long_run].tolist())]
        leftover_positions = positions[~np.repeat(long_run, lengths)] # scattered range
        if segments and len(leftover_positions):
            # push the leftover onto the last segment: its positions, then the leftover's, gathered into one block
            # in storage order, so the gather below reads the columns front to back
            merged = np.sort(np.concatenate([segments[-1][1], leftover_positions]))
            segments[-1] = (self.columns[:, merged], merged)  # replaces the last segment in place
        elif len(leftover_positions): # no segments at all
            segments.append((self.columns[:, leftover_positions], leftover_positions))

        # The buckets, precalculated: a segment of n rows makes n // SCAN_BUCKET full buckets, and its n % SCAN_BUCKET
        # remaining rows one last, smaller bucket.
        buckets = []  # array of array
        # iterate through segments
        for columns, scored in segments:
            full, remainder = divmod(len(scored), SCAN_BUCKET)
            bounds = [(bucket * SCAN_BUCKET, (bucket + 1) * SCAN_BUCKET) for bucket in range(full)]
            if remainder:
                bounds.append((full * SCAN_BUCKET, len(scored)))
            buckets.extend([(columns[:, begin:stop], scored[begin:stop]) for begin, stop in bounds])
        num_buckets = len(buckets)
        offsets = np.cumsum([0] + [len(scored) for _, scored in buckets])
        similarities = np.empty(offsets[-1], dtype=np.float32)

        def score(bucket):
            # one multiply-add per dimension over the bucket's contiguous run of it, straight into its slice
            columns, offset = buckets[bucket][0], offsets[bucket]
            np.dot(query, columns, out=similarities[offset:offset + columns.shape[1]])

        # the distance calculation, every bucket of it, before the priority queue sees any
        if SCAN_THREADS > 1 and num_buckets > 1:
            scan_executor(SCAN_THREADS).run(num_buckets, score)
        else:
            for bucket in range(num_buckets):
                score(bucket)

        best = []  # (similarity, row) of the k most similar so far, the least similar on top
        for bucket, (_, scored) in enumerate(buckets):
            part = similarities[offsets[bucket]:offsets[bucket + 1]]
            # NumPy picks what can enter the queue: once it is full, only rows beating its least similar, and of a
            # bucket only its own k most similar, so the queue sees a few rows, not every one
            candidates = np.flatnonzero(part > best[0][0]) if len(best) == k else np.arange(len(part))
            if len(candidates) > k:
                candidates = candidates[np.argpartition(part[candidates], len(candidates) - k)[len(candidates) - k:]]
            for index in candidates.tolist():
                entry = (float(part[index]), int(self.order[scored[index]]))
                if len(best) < k:
                    heapq.heappush(best, entry)
                elif entry[0] > best[0][0]:
                    heapq.heapreplace(best, entry)
        return [(row, similarity) for similarity, row in sorted(best, reverse=True)]


def bucket_of(similarity):
    """Bucket of a cosine similarity: bucket b holds [b * BUCKET_WIDTH, (b + 1) * BUCKET_WIDTH), the top one also 1."""
    scale = round(1 / BUCKET_WIDTH)
    return min(math.floor(similarity * scale), scale - 1)  # * 10, not / 0.1: 0.3 / 0.1 is 2.999...


def similarity_buckets(candidates):
    """{bucket: [(row, similarity), ...]} of (row, cosine similarity to the user embedding) candidates, each bucket's
    most similar first."""
    buckets = {}
    for row, similarity in sorted(candidates, key=lambda candidate: -candidate[1]):
        buckets.setdefault(bucket_of(similarity), []).append((row, similarity))
    return buckets


def history_ranges(history_similarity, max_similarity=MAX_SIMILARITY):
    """{(position, low, high)}, one per history recipe: the cosine similarities to the user embedding that a recipe at
    cosine max_similarity or more to that history recipe can have.

    Angles between vectors obey the triangle inequality, so the angle of such a recipe to the user embedding is the
    history recipe's ± arccos(max_similarity), 14 degrees for 0.97; the range is those angles' cosines. A fixed small
    margin would miss some: with the current model, ± 0.01 around the history recipe's similarity misses 9 of the 14
    near-duplicates in the example users' candidates."""
    margin = math.acos(min(max(max_similarity, -1.0), 1.0))
    ranges = set()
    for position, similarity in enumerate(history_similarity):
        angle = math.acos(min(max(similarity, -1.0), 1.0))
        ranges.add((position, math.cos(min(math.pi, angle + margin)) - RANGE_SLACK,
                    math.cos(max(0.0, angle - margin)) + RANGE_SLACK))
    return ranges


def drop_near_history(buckets, ranges, candidate_unit, history_unit, max_similarity=MAX_SIMILARITY):
    """The buckets without the candidates at cosine max_similarity or more to a history recipe, and those candidates as
    {row: (history position, cosine)}.

    candidate_unit[row] is a bucketed candidate's L2-normalized embedding, history_unit[position] a history recipe's.
    A candidate is compared only with the history recipes whose range holds its similarity: for each range, the
    buckets it overlaps are looked up in the dictionary, and in each a window slides down to the candidates inside
    it. Ranges go highest first, so a bucket's window only ever moves down."""
    windows = {}  # Bucket → (start, end) of its window over the bucket's candidates.
    near = {}
    for position, low, high in sorted(ranges, key=lambda item: (-item[2], -item[1])):
        for bucket in range(bucket_of(high), bucket_of(low) - 1, -1):
            if bucket not in buckets:
                continue
            items = buckets[bucket]
            start, end = windows.get(bucket, (0, 0))
            while start < len(items) and items[start][1] > high:
                start += 1
            end = max(start, end)  # Never narrower than the range; wider only compares a few more candidates.
            while end < len(items) and items[end][1] >= low:
                end += 1
            windows[bucket] = start, end
            for row, _ in items[start:end]:
                if row in near:
                    continue
                cosine = float(candidate_unit[row] @ history_unit[position])
                if cosine >= max_similarity:
                    near[row] = position, cosine
    kept = {bucket: [item for item in items if item[0] not in near] for bucket, items in buckets.items()}
    return {bucket: items for bucket, items in kept.items() if items}, near


def mmr(relevance, similarity, k, lambda_=MMR_LAMBDA, max_similarity=MAX_SIMILARITY):
    """Maximal marginal relevance: positions of up to k items, in the order they are picked.

    Each pick is the item maximizing lambda_ * relevance - (1 - lambda_) * its highest similarity to an item already
    picked; similarity is (N, N). lambda_ = 1 keeps the order of relevance (ties in input order), lower values trade
    relevance for diversity. An item whose similarity to a pick is max_similarity or more is never picked (None: no
    cutoff), so fewer than k come back when every other item is that close to a pick."""
    if not 0 <= lambda_ <= 1:
        raise ValueError(f"The MMR lambda must be between 0 and 1, not {lambda_}.")
    relevance, similarity = np.asarray(relevance, dtype=np.float64), np.asarray(similarity, dtype=np.float64)
    closest = np.zeros(len(relevance))  # Highest similarity to a pick; no penalty before the first pick.
    available = np.ones(len(relevance), dtype=bool)
    picked = []
    while len(picked) < k and available.any():
        score = np.where(available, lambda_ * relevance - (1 - lambda_) * closest, -np.inf)
        picked.append(int(np.argmax(score)))
        available[picked[-1]] = False
        if max_similarity is not None:
            available &= similarity[picked[-1]] < max_similarity
        closest = similarity[picked[-1]] if len(picked) == 1 else np.maximum(closest, similarity[picked[-1]])
    return picked


class RetrievalPipeline:
    """User profile → HNSW candidates → reranked → diversified (MMR) recommendations."""

    def __init__(self, reranker, recipes, recipe_index):
        if [recipe["fdc_id"] for recipe in recipes] != recipe_index.fdc_ids:
            raise ValueError("The index was built from a different catalog; rebuild it.")
        self.reranker = reranker
        self.recipes = recipes
        self.recipe_index = recipe_index
        self.row_of = {fdc_id: row for row, fdc_id in enumerate(recipe_index.fdc_ids)}
        self.user_states = {}  # user_id → windows and the model's state of each, from the last recommend call.

    @classmethod
    def from_recipes(cls, recipes, artifacts=DEFAULT_ARTIFACTS, index_dir=DEFAULT_INDEX_DIR, rebuild=False,
                     device=None):
        """recipes in the reranker's input format, as inference.service.catalog_recipes makes them from the catalog."""
        reranker = Reranker(artifacts, device=device)
        # The catalog by what it holds, not by its database file's bytes: SQLite can rewrite a page without changing
        # a food, and the API adds the CSV's new foods to that file on startup.
        source = fingerprint(Path(artifacts) / "model.pt", Path(artifacts) / "config.json",
                             data=json.dumps(recipes, sort_keys=True).encode())
        return cls(reranker, recipes, RecipeIndex.load_or_build(reranker, recipes, index_dir, source, rebuild))

    def resolve_history(self, history):
        """History recipes in date order and their item embeddings (len(history), d).

        An entry with "fdc_id" is that catalog recipe, with the entry's "rating" and "date"; its embedding comes from
        the index. Any other entry is a full recipe ("ingredients", "price", "nutrients", as in the inference
        script), tagged and encoded here."""
        # Stable sort: entries without a date keep their given order.
        history = sorted(history, key=lambda entry: str(entry.get("date", "")))
        resolved, embeddings, off_catalog = [], [None] * len(history), []
        for position, entry in enumerate(history):
            if "fdc_id" in entry:
                row = self.row_of.get(int(entry["fdc_id"]))
                if row is None:
                    raise KeyError(f"fdc_id {entry['fdc_id']} is not in the catalog.")
                resolved.append({**self.recipes[row], **entry, "fdc_id": int(entry["fdc_id"])})
                embeddings[position] = torch.from_numpy(self.recipe_index.embeddings[row])
            else:
                resolved.append(dict(entry))
                off_catalog.append(position)
        if off_catalog:
            recipes = [resolved[position] for position in off_catalog]
            self.reranker.tag_recipes(recipes)
            for position, embedding in zip(off_catalog, self.reranker.encode_items(recipes)[0].cpu()):
                embeddings[position] = embedding
        return resolved, torch.stack(embeddings)

    def user_state(self, history, history_emb):
        """The windows of a history (date order) and the model's state of each (Reranker.window_states), most recent
        first."""
        positions, _, lengths = self.reranker.windows(history)
        states = self.reranker.window_states(history, history_emb)
        windows = []
        for window, (row, length, state) in enumerate(zip(positions, lengths, states), start=1):
            members = [history[position] for position in row[:length]]
            rated = [recipe["rating"] for recipe in members if recipe.get("rating") is not None]
            windows.append({"window": window, "from": str(members[0].get("date", "")),
                            "to": str(members[-1].get("date", "")), "recipes": int(length),
                            "fdc_ids": [recipe.get("fdc_id") for recipe in members],
                            "mean_rating": float(np.mean(rated)) if rated else None,
                            "state_norm": float(state.norm()), "state": state.tolist()})
        return {"windows": windows, "states": states}

    @staticmethod
    def user_embedding(history_emb):
        """Representative embedding: the average of the history's embeddings, each L2-normalized first so every
        recipe weighs the same under cosine similarity."""
        return F.normalize(history_emb, dim=1).mean(0)

    def recommend(self, user, n_candidates=100, top_k=10, mmr_lambda=MMR_LAMBDA, max_similarity=MAX_SIMILARITY,
                  log=None, show=20):
        """Up to top_k of n_candidates retrieved recipes, picked by MMR over P(like) and the cosine similarity of their
        item embeddings, in pick order. A candidate whose cosine to a history recipe or to a pick is max_similarity or
        more is left out, so fewer than top_k come back when every other candidate is that close to one.
        mmr_lambda = 1 with max_similarity = None gives the top_k by P(like), most likely first.

        With log (e.g. print), each step is reported: the history, the user embedding, the HNSW candidates (the first
        `show` of them), the near-duplicates of the history dropped, how the reranker reorders the rest and what MMR
        picks."""
        log = log or (lambda *_: None)

        # the user's preferences as a plain dict, {} when none are given, whether they come as a dict or as a
        preferences = user.get("preferences") or {}
        if hasattr(preferences, "model_dump"):
            preferences = preferences.model_dump()
        preferences = dict(preferences)

        if not user.get("history"):
            raise ValueError("The pipeline needs at least one recipe in the user's history.")

        history, history_emb = self.resolve_history(user["history"])
        ratings = self.reranker.normalized_ratings(history)
        t_max = self.reranker.config["t_max"]
        window_of = {}  # History position → window number (1 = most recent), for the recipes the model sees.
        if self.reranker.windowed:
            positions, _, _ = self.reranker.windows(history)
            window_of = {int(position): window + 1 for window, row in enumerate(positions) for position in row
                         if position >= 0}
            log(f"\n=== Step 1. History of user {user.get('user_id', '?')}: {len(history)} recipes, date order; a gap "
                f"of {self.reranker.config['window_gap_days']}+ days starts a window (the last "
                f"{self.reranker.config['max_windows']} windows, last {t_max} recipes each, feed "
                f"{self.reranker.history_model}) ===")
        else:
            log(f"\n=== Step 1. History of user {user.get('user_id', '?')}: {len(history)} recipes, date order "
                f"(the last {t_max} feed the GRU) ===")
        log(f"  {'date':<10}  {'fdc_id':>8}  {'recipe':<48} {'rating':>6} {'model rating':>12} {'window':>6} "
            f"{'|emb|':>6}")
        for position, (recipe, emb, rating) in enumerate(zip(history, history_emb, ratings)):
            window = window_of.get(position, "-") if self.reranker.windowed else ""
            log(f"  {str(recipe.get('date', '')):<10}  {recipe.get('fdc_id', 'custom'):>8}  "
                f"{str(recipe.get('name', ''))[:48]:<48} {str(recipe.get('rating', '-')):>6} {rating:>12.3f} "
                f"{window:>6} {emb.norm():>6.2f}")

        query = self.user_embedding(history_emb)
        member_cosine = F.cosine_similarity(history_emb, query.unsqueeze(0)).tolist()
        log(f"\n=== Step 2. Representative embedding: mean of the {len(history)} L2-normalized history embeddings "
            f"({query.numel()} dims, norm {query.norm():.3f}) ===")
        log("  first 8 dims: " + " ".join(f"{value:+.3f}" for value in query[:8].tolist()))
        for recipe, cosine in zip(history, member_cosine):
            log(f"  cosine to {str(recipe.get('name', ''))[:48]:<48} {cosine:.3f}")

        seen = {self.row_of[recipe["fdc_id"]] for recipe in history if "fdc_id" in recipe}
        log(f"\n=== Step 3. Retrieval: the {n_candidates} nearest of {len(self.recipes):,} recipes by cosine similarity "
            f"({len(seen)} history recipes excluded) ===")
        # HNSW already returns them most similar first; the buckets and the retrieval ranks rely on it.
        retrieved = sorted(self.recipe_index.search(query.numpy(), n_candidates, conditions=preferences, exclude=seen,
                                                    log=log),
                           key=lambda candidate: -candidate[1])
        log(f"  {len(retrieved)} retrieved")
        log(f"  {'rank':>4}  {'fdc_id':>8}  {'recipe':<56} {'cosine':>7}")
        for rank, (row, similarity) in enumerate(retrieved[:show], start=1):
            log(f"  {rank:>4}  {self.recipes[row]['fdc_id']:>8}  {self.recipes[row]['name'][:56]:<56} {similarity:>7.3f}")
        if len(retrieved) > show:
            log(f"  ... {len(retrieved) - show} more")
        if not retrieved:
            return []

        buckets = similarity_buckets(retrieved)
        if max_similarity is not None:
            rows = [row for row, _ in retrieved]
            retrieved_unit = F.normalize(torch.from_numpy(self.recipe_index.embeddings[rows]), dim=1).numpy()
            ranges = history_ranges(member_cosine, max_similarity)
            kept, near = drop_near_history(buckets, ranges, dict(zip(rows, retrieved_unit)),
                                           F.normalize(history_emb, dim=1).numpy(), max_similarity)
            log(f"\n=== Step 4. Near-duplicates of the history: candidates bucketed by cosine to the user embedding; "
                f"each history recipe's range (where a recipe at cosine >= {max_similarity:g} to it can be) is looked "
                f"up in the buckets and slid over; {len(near)} such candidates dropped ===")
            log(f"  {'bucket':<10} {'retrieved':>9} {'kept':>5}")
            for bucket, items in sorted(buckets.items(), reverse=True):
                label = (f"[{bucket * BUCKET_WIDTH:.1f}, {(bucket + 1) * BUCKET_WIDTH:.1f}"
                         f"{']' if bucket == bucket_of(1.0) else ')'}")
                log(f"  {label:<10} {len(items):>9} {len(kept.get(bucket, [])):>5}")
            log(f"  {'history recipe':<48} {'cosine':>7}  {'range':<14} {'in range':>8} {'dropped':>7}")
            for position, low, high in sorted(ranges):
                inside = sum(low <= similarity <= high for _, similarity in retrieved)
                dropped = sum(near_position == position for near_position, _ in near.values())
                log(f"  {str(history[position].get('name', ''))[:48]:<48} {member_cosine[position]:>7.3f}  "
                    f"{f'[{low:.3f}, {high:.3f}]':<14} {inside:>8} {dropped:>7}")
            for row, (position, similarity) in list(near.items())[:show]:
                log(f"  dropped {self.recipes[row]['fdc_id']:>8}  {self.recipes[row]['name'][:44]:<44} cosine "
                    f"{similarity:.3f} to {str(history[position].get('name', ''))[:40]}")
            if len(near) > show:
                log(f"  ... {len(near) - show} more")
            buckets = kept
        candidates = [candidate for bucket in sorted(buckets, reverse=True) for candidate in buckets[bucket]]
        if not candidates:
            return []

        if self.reranker.windowed:
            self.user_states[user.get("user_id")] = state = self.user_state(history, history_emb)
            state_of = ("the Transformer's states of its ratings, averaged" if self.reranker.kind == "window_transformer"
                        else "the GRU's final hidden state (restarted per window)")
            log(f"\n=== Step 5. User state: for each window, {state_of}; stored in pipeline.user_states ===")
            log(f"  {'window':>6}  {'from':<10}  {'to':<10} {'recipes':>7} {'mean rating':>11} {'|state|':>8}  first 6 dims")
            for window in state["windows"]:
                log(f"  {window['window']:>6}  {window['from']:<10}  {window['to']:<10} {window['recipes']:>7} "
                    f"{'-' if window['mean_rating'] is None else format(window['mean_rating'], '.2f'):>11} "
                    f"{window['state_norm']:>8.3f}  "
                    + " ".join(f"{value:+.2f}" for value in window["state"][:6]))

        rows = [row for row, _ in candidates]
        candidate_emb = torch.from_numpy(self.recipe_index.embeddings[rows])
        p_likes = self.reranker.score(history, history_emb, candidate_emb)
        retrieval_rank = {row: rank for rank, (row, _) in enumerate(retrieved, start=1)}
        results = [{"fdc_id": self.recipes[row]["fdc_id"], "name": self.recipes[row]["name"],
                    "price": self.recipes[row]["price"], "p_like": p_like, "like": p_like >= THRESHOLD,
                    "similarity": similarity, "retrieval_rank": retrieval_rank[row]}
                   for (row, similarity), p_like in zip(candidates, p_likes.tolist())]
        reranked = sorted(results, key=lambda result: -result["p_like"])
        for rank, result in enumerate(reranked, start=1):
            result["rerank_rank"] = rank

        windows = self.user_states.get(user.get("user_id"), {}).get("windows", [])
        if self.reranker.kind == "window_transformer":
            keys = (f"the Transformer's states of the {sum(window['recipes'] for window in windows)} ratings in "
                    f"{len(windows)} window{'' if len(windows) == 1 else 's'}")
        elif self.reranker.windowed:
            keys = f"the hidden states of {len(windows)} windows"
        else:
            keys = f"GRU states of the last {min(len(history), t_max)} history recipes"
        log(f"\n=== Step 6. Reranker: each candidate attends over [user profile; {keys}] -> P(like) ===")
        log(f"  {'new':>4} {'was':>4} {'move':>5}  {'fdc_id':>8}  {'recipe':<48} {'cosine':>7} {'P(like)':>8}")
        for new_rank, result in enumerate(reranked[:show], start=1):
            move = result["retrieval_rank"] - new_rank
            log(f"  {new_rank:>4} {result['retrieval_rank']:>4} {move:>+5d}  {result['fdc_id']:>8}  "
                f"{result['name'][:48]:<48} {result['similarity']:>7.3f} {result['p_like']:>8.4f}")
        if len(reranked) > show:
            log(f"  ... {len(reranked) - show} more")

        unit = F.normalize(candidate_emb, dim=1)
        cosine = (unit @ unit.T).numpy()  # Candidates in retrieval order, like results.
        picked = mmr(p_likes.numpy(), cosine, top_k, mmr_lambda, max_similarity)
        recommended = [results[position] for position in picked]
        cutoff = "" if max_similarity is None else f"; none at cosine >= {max_similarity:g} to a pick"
        log(f"\n=== Step 7. MMR: each pick maximizes {mmr_lambda:g} * P(like) - {1 - mmr_lambda:g} * its highest cosine "
            f"to an earlier pick{cutoff} ===")
        log(f"  {'pick':>4} {'was':>4}  {'fdc_id':>8}  {'recipe':<48} {'P(like)':>8} {'closest':>7}")
        for pick, (position, result) in enumerate(zip(picked, recommended), start=1):
            closest = f"{cosine[position, picked[:pick - 1]].max():.3f}" if pick > 1 else "-"
            log(f"  {pick:>4} {result['rerank_rank']:>4}  {result['fdc_id']:>8}  {result['name'][:48]:<48} "
                f"{result['p_like']:>8.4f} {closest:>7}")
        replaced = sum(result["rerank_rank"] > len(recommended) for result in recommended)
        if max_similarity is None:
            cutoff = ""
        else:
            too_close = (cosine[:, picked] >= max_similarity).any(axis=1)
            too_close[picked] = False
            cutoff = f"; {int(too_close.sum())} candidates left out at cosine >= {max_similarity:g} to a pick"
        log(f"\n=== Step 8. {len(recommended)} of {top_k} kept, {replaced} of them from beyond the reranker's top "
            f"{len(recommended)}{cutoff}; {sum(r['like'] for r in reranked)} of {len(reranked)} candidates have "
            f"P(like) >= {THRESHOLD} ===")
        return recommended


def print_recommendations(user, results):
    print(f"\nRecommendations for user {user.get('user_id', '?')}: {len(user['history'])} history recipes")
    print(f"  {'fdc_id':>8}  {'recipe':<60} {'P(like)':>8} {'cosine':>7} {'retrieved':>9} {'reranked':>8}")
    for result in results:
        print(f"  {result['fdc_id']:>8}  {result['name'][:60]:<60} {result['p_like']:>8.4f} "
              f"{result['similarity']:>7.3f} {'#' + str(result['retrieval_rank']):>9} "
              f"{'#' + str(result['rerank_rank']):>8}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["build", "recommend"],
                        help="build: embed the catalog and save the HNSW index; recommend: score one user")
    parser.add_argument("--catalog", type=Path,
                        help="database holding the catalog (default DATABASE_PATH); filled from CATALOG_PATH if missing")
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS,
                        help="folder holding the reranker's model.pt and config.json")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR, help="where the index is saved")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the index even if it is up to date")
    parser.add_argument("--user", type=Path, default=DEFAULT_USER,
                        help='JSON user profile {"user_id", "history": [{"fdc_id", "rating", "date"}, ...]}')
    parser.add_argument("--candidates", type=int, default=100, help="recipes retrieved by HNSW")
    parser.add_argument("--top-k", type=int, default=10, help="recommendations kept after reranking")
    parser.add_argument("--mmr-lambda", type=float, default=MMR_LAMBDA,
                        help="MMR weight of P(like) against diversity, in [0, 1]; 1 keeps the reranker's order")
    parser.add_argument("--max-similarity", type=float, default=MAX_SIMILARITY,
                        help="a candidate whose cosine to a history recipe or to a recommendation is this or more is "
                             "left out; 2 turns it off")
    parser.add_argument("--output", type=Path, help="also write the recommendations to this JSON file")
    parser.add_argument("--quiet", action="store_true", help="print only the final recommendations, not each step")
    parser.add_argument("--show", type=int, default=20, help="rows printed per step")
    args = parser.parse_args()

    # Imported here: the service imports this module, and only the command line needs the repository.
    from sqlalchemy.orm import Session

    from ..db import database_path, sqlite_engine
    from ..repository.recipe_repository import RecipeRepository, init_catalog_database
    from .service import catalog_recipes

    database = args.catalog or database_path()
    if not init_catalog_database(database):
        parser.error("no catalog: pass --catalog, or set DATABASE_PATH or CATALOG_PATH")
    with Session(sqlite_engine(database)) as session:
        recipes = catalog_recipes(RecipeRepository(session).all_foods())
    pipeline = RetrievalPipeline.from_recipes(recipes, args.artifacts, args.index_dir,
                                              rebuild=args.rebuild or args.command == "build")
    if args.command == "build":
        print(f"Indexed {len(pipeline.recipes):,} recipes × {pipeline.recipe_index.embeddings.shape[1]} dims "
              f"in {args.index_dir}")
        return
    user = json.loads(args.user.read_text())
    results = pipeline.recommend(user, args.candidates, args.top_k, args.mmr_lambda, args.max_similarity,
                                 log=None if args.quiet else print, show=args.show)
    print_recommendations(user, results)
    if args.output:
        windows = pipeline.user_states.get(user.get("user_id"), {}).get("windows", [])
        args.output.write_text(json.dumps({"user_id": user.get("user_id"), "windows": windows,
                                           "recommendations": results}, indent=2))


if __name__ == "__main__":
    main()
