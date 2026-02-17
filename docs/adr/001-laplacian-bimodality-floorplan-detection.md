# ADR 001: Laplacian Bimodality Heuristic for Floorplan Detection

## Status

Accepted

## Date

2026-02-17

## Context

The PIL-based floorplan detector (`utils/floorplan_detector.py`) uses cheap pixel
statistics to classify images as floorplans — zero API cost, negligible latency.
It originally used 4 weighted heuristics: color saturation, brightness/white pixel
ratio, color diversity, and edge density.

These 4 heuristics produced a false positive on `zoopla:70833107` — a white kitchen
image scored **0.694** while a real floorplan in the same listing scored **0.667**.
No threshold can separate them on the existing scoring axis: the kitchen's combination
of white walls, low saturation countertops, and clean lines mimics a floorplan's
statistical profile. False-positive detection causes properties to incorrectly pass
the `require_floorplan` gate, wasting quality analysis API calls and sending incorrect
Telegram notifications.

## Decision Drivers

- **No new dependencies** — detection must stay PIL-only, no ML model downloads
- **Latency budget** — sub-10ms per image to keep the pipeline fast
- **Offline-only** — no API calls; this gate runs before quality analysis
- **Discriminate the specific failure mode** — must separate white-room photos from
  floorplans without regressing real floorplan recall

## Decision

We add **Laplacian bimodality** as a 5th heuristic signal (weight 0.20), reweighting
the existing 4 heuristics to sum to 1.0. This exploits a structural difference between
floorplans and photos:

- **Floorplans** have a bimodal Laplacian distribution — pixels are either *flat*
  (uniform white background) or *sharp* (wall lines), with very little in between.
- **Photos** have gradual tonal transitions from lighting, textures, and shadows,
  producing a unimodal distribution.

A minimum sharp-pixel guard prevents uniform images (all white, no edges) from scoring
high — bimodality requires *both* flat AND sharp populations to be present.

Result on the failing case: the white kitchen drops below the confidence threshold
while the real floorplan rises above it.

See `utils/floorplan_detector.py` lines 107–129 for implementation specifics
(kernel, thresholds, scoring boundaries).

## Alternatives Considered

### Threshold tuning on existing signals
Adjusting the 0.65 confidence threshold or individual heuristic thresholds. Rejected
because the kitchen and floorplan scores were too close (0.694 vs 0.667) on the existing
axis — any threshold that rejects the kitchen also rejects the floorplan.

### Saturation reweighting
Increasing the saturation heuristic weight, since kitchens typically have more color than
floorplans. Rejected because white/minimalist kitchens and bathrooms can have genuinely
low saturation, making this unreliable as a sole discriminator.

### CLIP / MobileCLIP embedding similarity
Using a vision-language model to compare images against "floorplan" / "floor plan"
text embeddings. Would likely be highly accurate but adds a heavy dependency
(~100MB+ model), significant latency, and complexity disproportionate to the problem.
Kept as a future option if heuristic-based detection proves insufficient.

### Claude Haiku validation
Sending borderline images (score 0.55–0.75) to Claude Haiku for binary
floorplan/not-floorplan classification. Accurate but adds API cost per borderline
image, latency, and a network dependency to what is currently a fully offline
detection step.

### Reorder pipeline to use Phase 1 `floorplan_detected_in_gallery`
The quality analysis Phase 1 already asks Claude whether floorplans were detected in the
gallery. Could gate on that instead. Rejected because it creates a circular dependency —
quality analysis runs *after* the floorplan gate, and restructuring the pipeline order
introduces complexity and changes the filtering semantics.

## Consequences

### Positive
- **Fixes the false positive**: the white kitchen case is correctly rejected
- **No new dependencies**: uses only PIL's `ImageFilter.Kernel`, already available
- **Validated on 1033 images** from the production image cache across all platforms
  (Zoopla, Rightmove, OpenRent, OnTheMarket) — no regressions observed
- **Principled signal**: bimodality captures a genuine structural property of
  line-drawing-on-white-background images, not just a statistical coincidence
- **Cheap**: ~2ms additional latency per image (one convolution + pixel counting)

### Negative
- **Another tunable parameter**: the flat threshold (±3), sharp threshold (±20),
  bimodal ratio boundaries (0.75, 0.90), and sharp guard (2%) are all magic numbers
  that may need future adjustment
- **Weight rebalancing**: changing from 4 equal weights to 5 unequal weights makes the
  scoring harder to reason about

### Risks
- **Textured floorplans**: floorplans with furniture illustrations, colored room fills,
  or photographic overlays may not exhibit clean bimodality — could produce false
  negatives on unusual floorplan styles
- **Very clean architectural photos**: CGI renders or sterile white-room photos might
  exhibit pseudo-bimodality — though the sharp guard should mitigate most of these

## Confirmation

- **Regression suite**: `pytest tests/test_utils/test_floorplan_detector.py` covers the
  white kitchen false positive and real floorplan true positive cases
- **Bulk validation**: Run `detect_floorplan()` against the production image cache
  (`{data_dir}/image_cache/`) to check for regressions across platforms — validated on
  1033 images at time of acceptance with zero regressions
- **Monitor in production**: Watch for new false positives (non-floorplan images passing
  the gate) or false negatives (real floorplans being rejected) in Telegram notifications
