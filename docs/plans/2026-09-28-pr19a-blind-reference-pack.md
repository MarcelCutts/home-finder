# PR19a: Retained development evidence and independent review tooling

**Date:** 28 September 2026. **State:** committed as `9498f39`; human labels remain pending.
**Predecessor:** PR05a authority/restore, `ad34ba9`; PR13b spend enforcement, `58d538b`.
**Parent:** [PR19 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr19--build-independent-evaluation-data-and-task-specific-rubrics-m--human-labelling).

## Outcome and scope

The recovered production cache now supports a **48-case private development pack** with real retained image bytes, a versioned rubric, blank independent-review labels and an offline browser interface. It uses one canonical example per conservatively connected identity group. It imports no model answers, user statuses, private notes or viewing messages. No model, portal, route or notification request is part of this operation.

This is **PR19a tooling and development curation**, not completion of human reference work or qualification of a model. All **336 factual labels remain unreviewed**; no agent labels have been assigned to the real corpus. Prior exposure is unproven, so the builder deliberately cannot produce a holdout. The plan's fresh-text and independent-label gates remain open. This is a practical first review for a rental decision aid, not a representative housing survey or a reason to delay independent storage work.

## Verified material and coverage

Input is the private, verified PR05a package `data/backup-rehearsal-2026-09-28/fresh-backup`. The builder re-verifies the backup before reading it, then accesses SQLite with `mode=ro&immutable=1`, `query_only=ON`, explicit column allowlists and no application initialization. Its database checksum remains `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639`.

The source has 1,016 canonical rows. Existing gallery/floorplan files match 970 homes and fall into 796 connected groups with media. Connections come from source aliases, repeated exact provider IDs/URLs and identical bytes among matched cached images, including transitive connections. Generic shared images can conservatively over-group homes; crops/re-encodings or an unlinked relisting can escape byte equality. These groups reduce leakage but do not establish distinct physical dwellings. They never merge application records. For dependent samples, evaluations need group separation rather than random row splitting. [scikit-learn grouped cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data).

| Selected material | Observed result |
| --- | --- |
| Cases / groups | 48 / 48, one canonical example per selected group |
| Actual retained images | 352; 48,090,492 bytes; no selected undecodable/unsupported images |
| Cases with / without floorplans | 24 / 24 |
| Historical primary source | Rightmove 11, Zoopla 13, OpenRent 13, OnTheMarket 11 |
| Areas | E2, E3, E5, E9, E10, E15, E17, N15, N16, N17; four or five cases each |
| Historical bedroom buckets | 0: 14; 1: 16; 2: 18 |
| Gallery coverage | 22 sparse cases with fewer than five gallery candidates; 26 with five or more |
| Other matched image candidates omitted | 100, because cases have a maximum of 12 retained images |
| Source descriptions retained in selected cases | **0** |
| Reviewed labels / qualified holdout cases | **0 / 0** |

Selection greedily improves coverage across source, outcode, bedroom bucket, floorplan presence and gallery size, with a deterministic seeded tie break. Floorplans are selected before gallery images; within each role, hashes provide deterministic ordering. This is explicit curation, not a statistical sample. Human coverage review still needs room-versus-whole-home, price bands, address uncertainty, low-quality/ambiguous media and text polarity. A studio-like bedroom count is not proof of tenure or letting type.

The description gap is decisive: this recovered material can support visual and floorplan checks, but cannot qualify fresh amenity, advertised-cost or negation extraction. Unknown is an appropriate reference when a required statement is absent. Recovering the cache does not establish the exact bytes or image selection used by historical model calls. Collection time, source observation time and file recovery time remain different facts.

Private output: `data/reference-pack-2026-09-28/`. The completion manifest's SHA-256 is `72e972754348c24d7147c13d32858924a87a17d3dd4074f190b6d5c58c9a42bc`. This pack contains original photos and potentially identifying material, so pseudonymous case IDs do not make it anonymous. The whole pack is git-ignored, with directories created as 0700 and files as 0600. No production data is included in a release artifact or committed fixture.

## Labels, rubric and provenance

The [versioned rubric](../../src/home_finder/data/reference_rubric_v1.md) and [label contract](../../src/home_finder/models/reference.py) distinguish:

- Unreviewed, supported, unknown and conflicting factual evidence. Supported answers require evidence from that exact case. Printed/advertised claims also require an exact transcription. Unknown has no invented value; conflicting evidence needs two distinct citations and a reason.
- Seven initial facts: printed total area, bedrooms labelled on a plan, advertised current EPC, advertised council-tax band, explicit pets permission, explicit inclusion of all utility bills, and visible bathtub presence. Definitions preserve conditional/negated statements and explain when absence is insufficient to assert false.
- Owner workspace/hosting fit and retention-for-review judgments, with a named profile version and rationale. These are preferences, not universally correct factual labels. Insufficient evidence carries no numeric scores.
- Human reviewer ID, aware review time, declared independence from candidate answers, rubric version, exact case-input checksum and correction revision/parent checksum. A second-review declaration must name a different person. Syntax validation cannot certify a person's identity, truthfulness or that an adjudication actually occurred.

Printed area retains an exact decimal string, its original unit and its measurement definition. A gross total cannot silently become usable living area. The international foot is exactly 0.3048 m, giving 0.09290304 m² per square foot; the tested 500 sq ft reference is exactly 46.45152 m² while retaining its original definition. [NIST foot definition](https://www.nist.gov/pml/us-surveyfoot). No external EPC record is automatically substituted for the floorplan reference; date and method reconciliation remains necessary.

Every case lists content-addressed image/text files and omissions. The private origin map records canonical IDs, connected members and exact cache filenames for review. The completion manifest freezes input JSON, rubric, origin map and rendered pages by SHA-256. Labels live outside that immutable input set so a human can supply them separately. Changed evidence, wrong-case labels, external citations and quotes absent from the retained text fail validation. Checksums detect mismatch against this retained manifest, not malicious replacement of the manifest itself.

The builder requires a new destination and actual retained media. Limits are 60 cases, 12 images and 20 descriptions per case, 20 MB and 40 million pixels per image, 1 MB per source description, 600 MB selected material, and 10,000 canonical rows. Supported image bytes must decode as a single-frame JPEG, PNG or WebP. It retains original bytes; suffixes alone are not evidence of format. Oversized or undecodable candidates are skipped and counted; total size overflow fails and removes only the newly created output. Missing material never triggers a network refetch.

The manifest documents origins, selection, omissions and known limits because these determine what later measurements mean. It is deliberately explicit about development status and absent labels rather than treating a recovered dataset as evaluation evidence by default. [Datasheets for Datasets](https://arxiv.org/abs/1803.09010).

## Operator and reviewer workflow

The command loads neither `.env` nor provider credentials. To produce a new pack from another verified backup:

```sh
uv run python -m home_finder.pipeline.reference_pack build \
  --backup /private/verified-backup \
  --destination /private/new-reference-pack \
  --max-cases 48 --seed reference-v1

uv run python -m home_finder.pipeline.reference_pack validate \
  --pack /private/new-reference-pack
```

For this actual pack, open `data/reference-pack-2026-09-28/index.html` locally. If the browser restricts file URLs, serve **only this private directory**, bound to loopback, then visit `http://127.0.0.1:8765/index.html`:

```sh
uv run python -m http.server 8765 --bind 127.0.0.1 \
  --directory data/reference-pack-2026-09-28
```

Read the rubric before the first pass. The forms show retained evidence, explicit unknown/conflicting states, citations and a separate optional preference section. They have no submission endpoint or remote resource dependency. Text is escaped and a restrictive content policy blocks external connections. A download is a local JSON file; a page reload starts blank and does not save progress. Save downloads privately and keep original reviewer files. The browser form creates revision 1; corrections use the versioned JSON contract and retain their prior file/checksum. The validator checks a correction's declaration but does not reconstruct its full audit chain.

For an external label directory, start with a private copy of all blank `labels/*.json`, then replace each matching file with its human download while retaining originals separately. There must be one label file for every case. The following checks all seven factual fields have been reviewed; owner preferences can remain unreviewed:

```sh
uv run python -m home_finder.pipeline.reference_pack validate \
  --pack data/reference-pack-2026-09-28 \
  --labels /private/owner-first-pass \
  --require-reviewed
```

Without `--require-reviewed`, partial reviews are valid and their state counts are reported. A complete unknown label set still reports `promotion_ready: false`: completeness is not a holdout, an independent-check certificate or model qualification. Validation can check a text quote literally; the reviewer must verify image transcriptions visually.

## Verification and continuation

The focused offline suite passes **42 cases**: strict evidence/area/preferences/revision contracts, deterministic curation, four identity-link mechanisms, byte retention/decoding, sensitive-column exclusion, escaped source HTML, corrupt/unsafe inputs, caps/cleanup, wrong-case or invented citations/quotes, incomplete reviews and CLI reporting. Synthetic fixture content is the only material labelled by tests or browser automation.

Playwright exercised the synthetic first-review form, rejection of a missing declaration, a local JSON download and parsing that download with the real Python contract. The real pack was inspected without editing labels at desktop and 390-pixel mobile sizes: images loaded, no horizontal overflow, all labels remained unreviewed, and page resources used only the local server. There were no JavaScript runtime failures; the minimal static server's unconfigured favicon generated an inconsequential 404. Private screenshots and browser evidence are under `/tmp/home-finder-pr19/output/playwright/`. Wheel and source archive builds include the rubric/templates and exclude the private corpus.

The full suite passes **2,899 tests on both Python 3.11.16 and 3.14.7**, with **87.94% / 87.84%** combined line/branch coverage. Ruff lint/format (212 files), strict mypy (90 source files) and `git diff --check` pass. Logs and build artifacts are under `/tmp/home-finder-pr19/`. No production setting, collector, model budget or delivery path was activated.

**Next independent engineering segment: PR06b**, implementing replayable versioned source observations against the retained authority decision. It does not depend on human labels. PR19b requires owner review, an exposure audit, a suitable independent check for ambiguous critical cases, additional fresh retained text and a frozen comparison protocol. PR21 remains gated by those inputs and an explicitly approved experiment budget; its production promotion additionally needs the later recovery/artifact/delivery controls. Continue verified commits while those gates remain pending.
