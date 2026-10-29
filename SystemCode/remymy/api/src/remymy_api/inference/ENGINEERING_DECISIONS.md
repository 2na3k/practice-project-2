# Retrieval Engine: Engineering Decisions and Techniques

How Remymy's recommendation retrieval finds foods that match a user's taste and also obey hard rules (diet, budget),
the decisions behind it, and the measurements that confirmed or changed them. The design is in
[`recommender.MD`](recommender.MD); the code is in
[`model_inference_score_recommend.py`](model_inference_score_recommend.py).

All measurements are on the 10,658-food catalog (64-dimension embeddings) unless marked as synthetic, where a
2,000,000-row catalog stood in for a larger one.

---

## The problem

Filtered vector search. A user's recommendations must be similar to what they like **and** meet their rules. Plain
HNSW with a filter breaks down when few items match: the search has to walk much of the graph to collect enough
allowed items (graph fragmentation and traversal starvation).

---

## 1. Cluster-first routing

- **Offline clustering on metadata.** Items are grouped by their tags with agglomerative hierarchical clustering on
  **Jaccard distance**, before any query arrives.
- **Inverted index of Roaring bitmaps.** Each macro-cluster maps to its member item IDs.
- **Cluster matching by centroids.** Per query, a cluster where no member meets a required tag is skipped, one where
  every member does is taken whole, and from the rest only the matching tag-set groups are taken.

**Result:** the routed items always equal exactly what the rules allow (verified against the raw conditions on every
food).

## 2. Index selection by match size

| Share of catalog matching | Strategy |
|---|---|
| 10% or more | HNSW, with a bitset mask so only matching items can be returned |
| Under 10% | Skip HNSW; exact brute-force scan of the matching items |

**Measured:** below 10%, masked HNSW needed a much wider search (ef ≈ k / share) and took 3–3.4 ms at recall 0.999,
against 0.4–0.8 ms for the exact scan. Scan cost grows with the matches *m*, HNSW cost with *k·N/m*, so they cross at
*m ≈ √(k·N)* ≈ 9.7% of the catalog for k = 100, matching the 10% threshold.

## 3. Only diet values in the inverted index

- The index holds one tag per `special_diet` value: **vegetarian, halal, Hinduism**.
- **Budget** is a number, not a category, so each item's exact price is checked against it after routing.
- **Preferred nutrient** does not filter; it is reserved for later (likely ranking).
- Diet values come from the food data's columns when present, with a word screen of the food's name as fallback.

**Result:**

| Tags in the index | Tag sets | Macro-clusters |
|---|---|---|
| Diet + price bands + rich nutrients (15) | 692 | 43 |
| **Diet only (3)** | **7** | **6** |

## 4. Contiguous storage: sub-clustering and price order

- **Sub-clustering.** Inside each cluster, groups are ordered by the clustering tree's leaf order, so similar groups
  sit next to each other in memory. Each merge is arranged so the two closest groups meet at the boundary.
- **Cheapest first inside each group.** Any budget then keeps one unbroken stretch at the start of each group.

**Measured** (runs of matching rows across six diet and budget queries; fewer runs means more contiguous):

| Layout | Total runs |
|---|---|
| Diet + price-band tags | 415 |
| Diet tags only, groups unsorted | 2,611 |
| **Diet tags only, groups sorted by price** | **20** |

## 5. Structure of arrays (SoA)

Embeddings are stored as **(dimensions × foods)**, each dimension one contiguous array. A bucket's dot product is then
one SIMD multiply-add per dimension across all of its foods at once.

## 6. Segments: read in place, copy only what must be copied

- Matching rows that sit side by side in a **long run** (256+ rows) are scanned **in place**, as a slice: no copy.
- All other matching rows are **gathered once** into one contiguous block.
- The **leftover rows are pushed onto the last segment**, with positions **sorted** so the copy reads memory front to
  back, and the segment is **replaced in place**.

**Measured** (synthetic, 200,000 matching rows in contiguous groups): copying every row took 31 ms of which the dot
products were only 1.4 ms; reading long runs in place brought the scan from **28.9 ms to 9.8 ms**.

## 7. Buckets and threads

- **Precomputed buckets.** A segment of *n* rows gives *n // 1000* full buckets plus one smaller bucket of the
  *n % 1000* remainder.
- **Contiguous per-thread groups.** Each of the 4 threads owns one unbroken stretch of buckets (not round-robin), so
  it reads memory in order and the pool gets 4 tasks instead of one per bucket.
- **Threads compute distances only;** a single-threaded priority queue (min-heap) picks the top *k* afterwards, so it
  needs no locking. NumPy pre-filters what enters the queue.
- **A main thread redirects work** (`ThreadExecutor`). Sub-threads report their status, (thread, buckets left), to a
  `queue.Queue` after each bucket and put 0 when they run out; a semaphore counts the free sub-threads (+1 when one is
  free, -1 when one gets a bucket). The main thread waits on the semaphore, finds which sub-thread put 0, and redirects
  half of the busiest queue's remaining buckets to it, from the back, so no sub-thread idles while work remains.

**Measured** (synthetic, whole scan, k = 100):

| Scan | One task per bucket | **Contiguous groups** | Inline |
|---|---|---|---|
| 200,000 rows, contiguous | 9.2 ms | **6.6 ms** | 9.2 ms |
| 1,000,000 rows, contiguous | 47.6 ms | **27.1 ms** | 35.0 ms |

Redirecting work: lock-based stealing (each idle thread took a bucket itself) ran 7.3 / 17.9 ms on equal / uneven
buckets; the main-thread coordinator redirecting one bucket at a time 8.2 / 21.5 ms, as each redirect is a round trip
through the main thread; redirecting half the busiest queue at once brought it to 8.3 / 19.9 ms (same-process
comparisons, 200,000 rows).

On the 10,658-food catalog a scan is one or two buckets, so threads do not pay there; it stays under 0.5 ms either way.

## 8. Clustering details

- **Weighted duplicates.** Distinct tag sets are clustered once, weighted by how many foods share each: the same
  clusters as clustering every food (verified against SciPy on tie-free data), with 10,658 inputs reduced to 7.
- **Ties at the cut.** Jaccard distances are ratios of small counts and often land exactly on the threshold, so a
  small tolerance stops float rounding from deciding which side a merge falls.

## 9. Multi-interest queries

- **One mean misses.** A user's liked recipes sit far apart: the median cosine between two of them is 0.41. Their
  mean lands between their tastes, near none of them.
- **Cluster the liked history.** The recipes a user liked (rated 3 or more, the reranker's training label) are
  clustered by cosine distance with average linkage (the same `average_linkage` as the tags), cut at 0.5. Every
  cluster is one of the user's interests, with no cap on how many: the cut alone decides, and each searches with the
  mean of its recipes. This clusters the
  user's history, not the catalog: the catalog is still routed by tags (see "What the measurements changed", 2).
- **Liked recipes only, in every approach.** A disliked recipe makes no interest: a cluster of them would search
  for more of what the user dislikes. The normal approach takes only the liked recipes too: a cut of 2, the largest
  cosine distance, keeps them all in one interest, one mean of them (`--interest-threshold 2`). A user who liked
  nothing gets no recommendations: searching near what they disliked would bring more of it, and the reranker could
  not tell, as it reads ratings centered on the user's mean, all 0 then. Disliked recipes still count in the
  near-duplicate check and in the reranker.
- **Candidates split by size,** by largest remainder: 100 over interests of 3, 1 and 1 recipes is 60, 20 and 20.
- **Routed once, none twice.** The constraints are routed once for every interest (`search_many`), and each
  interest's search leaves out the rows the larger ones returned, so the 100 candidates stay distinct.
- **Near-duplicates per interest.** The bucket ranges rely on cosines to one query, so each interest's candidates
  are bucketed by their cosine to its own embedding.

**Measured** (the last liked recipe of 737 Food.com users of the catalog left out, each with 3+ earlier ratings
there; is it among the 100 candidates?):

| Query | Held-out recipe retrieved |
|---|---|
| Normal approach: one mean of the liked recipes (the baseline) | 4.1% |
| One mean of the whole history, dislikes included (the code before) | 4.1% |
| **Up to 3 interests, cut at 0.5** | **6.5%** (+2.4 points over the baseline, 95% CI +0.7 to +4.3) |

Cuts from 0.4 to 0.6 with 2 to 5 interests all gave 6.1–6.8%; 0.5 with 3 sits in the middle. The cap of 3 is now
removed, so every cluster at the 0.5 cut retrieves; that setting is not measured yet. Through the whole
pipeline, the held-out recipe reached the top 10 for 0.8% of the users against 0.4% with the baseline, too few to
tell apart: the reranker, not retrieval, limits that now. A recommendation took 3.6 ms instead of 3.2 ms (median).

Taking only the liked recipes changed nothing by itself here. Of the 137 users with a disliked recipe, a median 17%
of the history is disliked, and the two means point almost the same way (median cosine 0.979); 88 of the 137 have
only 0-ratings, which on Food.com can be a review left without stars. The API's dislikes are explicit, so there it
may matter more; this data cannot show it.

---

## What the measurements changed

Three ideas met data that disagreed, and each fix came from finding the cause rather than dropping the idea:

1. **Threads first lost to inline code.** The cause was the per-bucket hand-off and round-robin access, not
   threading itself; contiguous per-thread groups made them win.
2. **Clustering by embedding distance lost recall** (0.66–0.91 against 1.00 for plain HNSW). The design moved to
   clustering by metadata tags, which routes exactly.
3. **Price-band tags scattered budget matches** that fell between two bands. Removing them and storing groups
   cheapest first made every budget contiguous.

## Open items

- `users.py` CHECK constraints currently reject every user on a fresh database.
- `recommend.py`'s budget filter slices the unsorted list (one failing test); harmless while routing applies the
  budget.
- The catalog has no diet columns yet; diets come from the word screen until they are added.
