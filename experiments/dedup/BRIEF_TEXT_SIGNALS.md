# Brief: Improving Title & Description Matching for Property Deduplication

## Project Layout

This work lives in `experiments/dedup/` — a standalone uv project with a path dependency on the main `home-finder` package. **All commands below should be run from `experiments/dedup/`**, not the repo root. The sub-project has its own `pyproject.toml` and dependencies.

Key files in the experiment:
- `signals.py` — All signal implementations (this is where you'll make changes)
- `scorer.py` — Weighted scorer with `ScorerConfig` (enable signals by setting weight > 0)
- `evaluate.py` — Evaluation harness (precision/recall/F0.5 against labeled pairs)
- `generate_pairs.py` — Generates candidate pairs from a snapshot
- `collect.py` — Runs scrapers and saves JSON snapshots
- `data/snapshots/*.json` — Raw scraped property data with descriptions and image hashes
- `data/candidates.json` — Scored candidate pairs for labeling/evaluation
- `labels/*.json` — Ground truth labels

The main codebase (`src/home_finder/`) has the production scrapers and detail fetcher that populate the data. You may need to read `src/home_finder/scrapers/detail_fetcher.py` to understand how descriptions are extracted per platform.

## Goal

Improve the `signal_title_similarity` and `signal_description_tfidf` signals in `experiments/dedup/signals.py` so they contribute meaningful discriminative power to the property dedup scorer. Currently both signals are **disabled in production** (weight=0 in `scorer.py`) because they're not good enough. The gallery image signal (`gallery_images`, weight=40) and structural signals (postcode, coordinates, street name, price) do the heavy lifting. Title and description should be complementary signals that help resolve ambiguous cases — especially cross-platform pairs that share an outcode and street but lack full postcodes or coordinates (common with Rightmove).

## Context

This is a property rental dedup system that matches the same listing across 4 UK platforms: Rightmove, Zoopla, OpenRent, OnTheMarket. Properties are blocked by outcode+bedrooms, then scored with weighted signals. See the plan file at the repo root (`.claude/plans/cheeky-kindling-pearl.md`) for full architecture.

**Key metric:** F0.5 (precision-weighted) — false merges are worse than missed merges.

## Current State of Text Signals

### `signal_title_similarity` (signals.py ~line 288)

**Approach:** Strip boilerplate words ("to rent", "bed", "flat", etc.), then `rapidfuzz.fuzz.token_set_ratio`.

**Known problems:**
1. **Most titles are pure boilerplate.** "1 bed flat to rent" cleans to just "1". We added a min-length-4 guard so these don't fire, but it means the signal rarely fires at all. Zoopla titles are almost always "N bedroom flat/house to rent". OpenRent is similar. Only Rightmove sometimes includes street/building names in titles (e.g. "Apartment, High Street, London, E15").
2. **`token_set_ratio` is too generous.** It treats any subset overlap as high similarity. Two titles that both mention "E15" after cleaning will score high even if they're different properties.
3. **The signal overlaps with `fuzzy_address` and `street_name`.** When titles DO contain useful info, it's usually the address — which other signals already capture.

**Question to answer:** Is there any discriminative information in titles that isn't already captured by address signals? If not, this signal may just be noise and should stay at weight=0.

### `signal_description_tfidf` (signals.py ~line 337)

**Approach:** TF-IDF cosine similarity with prefix containment detection and truncate-to-shorter normalization.

**Known problems:**
1. **2-document TF-IDF produces poor IDF weights.** We fit a TfidfVectorizer on just the two descriptions being compared. With only 2 documents, IDF is nearly meaningless — every term appears in either 1 or 2 documents. A corpus-level vectorizer fitted across all descriptions in the snapshot would produce much better weights.
2. **Descriptions are often missing.** Zoopla descriptions required a recent fix to extract from `<p id="detailed-desc">` (see `detail_fetcher.py` ~line 303). Some properties still have no description. When one side is missing, the signal can't fire.
3. **Same-agent copy-paste works great (prefix containment catches it).** The real challenge is when different agents describe the same property differently — "spacious lounge" vs "generous living room". TF-IDF can't handle this.
4. **Boilerplate stripping is minimal.** Only removes property references and `<br>` tags. Lots of agent boilerplate remains ("call us today", "EPC rating C", standard disclaimers).

### `signal_feature_overlap` (signals.py ~line 402)

**Approach:** Regex extraction of ~25 amenity keywords (dishwasher, garden, balcony, etc.), Jaccard similarity.

**Current status:** Weight=0. Fires on TP and FP cases roughly equally in evaluation — not discriminative enough yet.

## Data & Evaluation Pipeline

Everything runs from `experiments/dedup/`:

```bash
# Collect fresh data (full scrape + detail pages + image hashes)
uv run python collect.py --hash-images

# Generate candidate pairs (blocked by outcode+bedrooms)
uv run python generate_pairs.py data/snapshots/<snapshot>.json

# Generate HTML labeling report
uv run python generate_report.py data/candidates.json

# Import labels from browser export
uv run python import_labels.py <downloaded-labels>.json

# Evaluate (the key command)
uv run python evaluate.py labels/<labels>.json data/candidates.json --errors --sweep
```

**Labels:** `labels/labels_20260210_011725.json` has 93 labels (38 match, 55 no_match). However, these were labeled against a previous full scrape. You'll need to either:
- Run a full `collect.py` (no `--max-per-scraper`) to get a snapshot that covers those property IDs, OR
- Label fresh pairs from a new scrape

**Scorer config:** `scorer.py` has `ScorerConfig` with per-signal weights. Currently `description_tfidf=0`, `title_similarity=0`, `feature_overlap=0`. The evaluate script uses `PRODUCTION_CONFIG` by default.

## First Step: Understand the Data

Before writing any code, read the actual property data to understand what titles and descriptions look like across platforms:

1. **Read a snapshot file** (`data/snapshots/snapshot_*.json`) — each property has `title`, `address`, `postcode`, and a `detail` sub-dict with `description`, `features`, and `gallery_urls`. Look at 10-20 properties per platform and note the patterns.

2. **Read the candidates file** (`data/candidates.json`) — each pair has `property_a`/`property_b` summaries plus `raw_a`/`raw_b` full dicts with descriptions. Look at both matched and non-matched pairs to understand what the signals are working with.

3. **Read the labels file** (`labels/labels_*.json`) — cross-reference labeled matches with their candidate data to see what true matches look like vs false matches.

Things to pay attention to:
- How much boilerplate is in titles per platform (Zoopla titles are almost always "N bedroom flat to rent")
- Whether different platforms describe the same property using the same agent text (copy-paste) or completely rewritten descriptions
- How often descriptions are missing entirely (check `"description": null` in raw dicts)
- What useful structured info is buried in description text (sqft, EPC rating, floor level, building name)

## Ideas to Explore

### For descriptions

1. **Corpus-level TF-IDF**: Fit the vectorizer on ALL descriptions in the snapshot (not just the pair). This gives meaningful IDF weights — "bedroom" gets low weight, "Lexington Building" gets high weight. The `generate_pairs.py` already loads all properties; you could pre-fit a vectorizer and pass it through.

2. **Sentence embeddings**: `all-MiniLM-L6-v2` via `sentence-transformers` (22MB model, already in pyproject.toml deps). Handles semantic similarity ("spacious lounge" ≈ "generous living room"). Could be a separate signal or replace TF-IDF.

3. **Better boilerplate stripping**: Remove agent contact details, EPC mentions, standard disclaimers, "call to arrange a viewing" etc. before comparison.

4. **Hybrid approach**: Use prefix containment for same-agent copy-paste (already works), sentence embeddings for different-agent same-property, and feature extraction as a cheap structural complement.

### For titles

1. **Honestly consider killing it.** If analysis shows titles never contain info beyond what address/street signals capture, remove it rather than adding complexity.

2. **Building/development name extraction**: The one case where titles help is when they contain building names ("The Lexington", "Ivy Point", "Legacy Tower"). Extract these and compare as a separate signal — more targeted than fuzzy title matching.

### For features

1. **Expand the keyword list** with property-specific terms (council tax band, EPC rating letter, specific appliance brands).
2. **Weighted Jaccard** — rare features (roof terrace) should count more than common ones (central heating).

## Files to Modify

- `experiments/dedup/signals.py` — Signal implementations
- `experiments/dedup/scorer.py` — Weights (to enable signals once they're good)
- `experiments/dedup/generate_pairs.py` — If you need corpus-level vectorizer pre-fitting
- `src/home_finder/scrapers/detail_fetcher.py` — If description extraction needs fixes

## Success Criteria

- Description signal has **lift > 2x** (TP firing rate / FP firing rate) in evaluation
- Enabling description/title signals with non-zero weights improves F0.5 over the baseline (currently ~1.0 on the small matched subset, but the real test is a full scrape with 90+ labels)
- No regression in precision — false merges must not increase
