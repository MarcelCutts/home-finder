# WEB area findings (src/home_finder/web/)

Method: read every Python module, template and JS module in the area; ran three scratch scripts
(`scratchpad/web/css_audit.py`, `css_audit2.py`, `css_vars.py`, `css_dups.py`) that extract every
class/id selector and custom property from `static/style.css` (comments stripped) and look each one up,
as a whole token, in `templates/*.html`, `static/modules/*.js` and every `src/**/*.py`; dynamic class
prefixes (`foo-{{`, `"foo-" +`, `foo{% if`) were collected and each prefixed class checked by hand
against the values the template can emit. Every DEAD claim below was then re-checked with
`git grep` over the whole tree (src, tests, scripts, docs, experiments) and
`git grep ... origin/codex/review-opus-experiments` (PR #77): no hits unless stated. `git status`
shows no untracked files in this checkout (Marcel's machine may differ). Coverage numbers come from
`scratchpad/evidence/coverage.json`.

Prior items: **A17 PARTIAL** (WEB-1..7, WEB-13 are what remains), **A18 OUTSTANDING** (WEB-11),
**T09 DONE** (validated `PropertyFilter.query_params()` drives `page_url`, `hx-ext="response-targets,morph"`
+ idiomorph loaded in base.html, applied-state sync in filters.js, `tests/test_web/test_filter_navigation.py`
and browser suite per `docs/reviews/2026-10-13-core-reliability-completion-audit.md:221`),
**B7 PARTIAL** (WEB-14), **A15** instance in WEB-5/WEB-6.

---

### WEB-1: Dead CSS + JS for the replaced fit-breakdown bars, donut and factor chips
- Category: SUPERSEDED  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~205 lines
- Location: src/home_finder/web/static/style.css:1422-1423,1427 (two selectors in the `.fit-dim-low-conf` groups), :1446 (`.fit-breakdown-card h4` half of the selector; the card heading is now an h3), :1452-1544, :1551-1596, :3550-3561, :3565-3571, :3597-3604; src/home_finder/web/static/modules/sections.js:1-16
- Evidence: Classes `fit-breakdown-{donut-wrap,donut,donut-value,tier,score,bars,row,label,track,fill,value,weight,group}`, `fit-factors`, `fit-factor`, `fit-factor-{earned,missed,unknown}` have zero references in templates, JS, Python, tests, docs or PR #77 (`git grep`), only style.css (and sections.js for `.fit-breakdown-group`). `git log -S"fit-breakdown-group" -- src/home_finder/web/templates` -> eb8309c "open each listing with the brief, annotate the agent's words, show the score's working" removed the markup; 37f1210's message says the working table "has its own row classes instead of the old flex bar rows". Still live and must stay: `.fit-breakdown-card`, `.fit-breakdown-band`, `.fit-breakdown-legend`, `.fit-brief-title`, `.fit-dim-*` (popover macro), `.fit-working*`. `sections.js:1-16` queries `.fit-breakdown-group`, which no template emits, so the tap-to-toggle block never attaches.
- Recommendation: Delete the listed rule blocks and the selector fragments, and delete sections.js lines 1-16. Then run the browser suite (`pytest -m browser tests/test_web/test_browser_e2e.py`) and look at the detail page at 375px and 480px, because the mobile media blocks are being edited.
- Prior item: A17   | Owner decision needed: no

### WEB-2: Other CSS selectors that no template, JS or Python can emit
- Category: DEAD  | Confidence: CERTAIN (first group) / HIGH (second group) | Action: DELETE | Risk: low | Effort: S | Size: ~75 lines
- Location: style.css:926 (`.property-card.card-actions-visible …` selector line), :1667 `.badge-estimate`, :2073-2076 `.meta-dim`, :2696-2703 `.benchmark-current`, :2764-2784 `.area-explore-link`, :2922-2926 `.rating-marker`, :3006-3010 `.map-popup-stars`, :4006-4014 `.signal-buyer/seller/neutral`, :4023-4028 `.negotiation-source`; second group :1225-1232 `.highlight-value-fair/.poor`, :1280-1283 `.condition-none`, :2527 `.quality-item-good` selector line
- Evidence: First group: zero hits for each name anywhere in the repo or in PR #77 except style.css. History: `meta-dim` left the templates in 228b237, `negotiation-source` in 3570e26. `rating-marker` and `map-popup-stars` are left over from the star-rating map markers; markers are now `price-pill-marker` (map-dashboard.js:75). Second group: the classes are built dynamically, but the emitters limit the values. `_property_card.html:102-103` emits `highlight-value-{{ value_rating }}` only inside `{% if value_rating in ('good','excellent') %}`. `_property_card.html:118` clamps severity to `minor|moderate|serious`, so `condition-none` never appears. The `_macros.html:8` `quality_item_class` macro emits only `-excellent/-fair/-poor/-neutral`, and `_quality_card.html:117` emits only `-excellent/-poor`. Note: `.value-excellent/good/fair/poor` (1243-1258) ARE live, built at map-dashboard.js:150.
- Recommendation: Delete. For the second group, delete only the unreachable variants and keep the siblings that can be emitted.
- Prior item: A17   | Owner decision needed: no

### WEB-3: `_status_badge.html` partial and its `.status-badge` CSS are orphaned
- Category: SUPERSEDED  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~16 lines
- Location: src/home_finder/web/templates/_status_badge.html:1-4; src/home_finder/web/static/style.css:3607-3618
- Evidence: `git grep "_status_badge"` matches only docs/tickets/ticket-7-property-status-tracking.md, a historical plan. No route returns the partial and no template includes it: `update_property_status` returns `_property_card.html` or `_status_controls.html` (routes.py:915-936). `.status-badge` is used only by this partial. Nothing in tests references it.
- Recommendation: Delete both. Leave the ticket doc alone; it records history.
- Prior item: A17   | Owner decision needed: no

### WEB-4: Detail route computes area-context keys that detail.html never renders
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~12 lines
- Location: src/home_finder/web/routes.py:684-686 (`micro_area_count`, `hosting_tolerance`, `creative_scene`), :699-703 (`noise_enforcement`, oddly nested under `if qa is not None`), imports :24-25 (`CREATIVE_SCENE`, `HOSTING_TOLERANCE`)
- Evidence: detail.html reads only `area_context.{description, reference_note, matched_micro_area, crime, benchmarks, acoustic_profile}`, and `_quality_card.html`/`_cost_breakdown.html` read no `area_context` at all (output of `grep -o "area_context\.\w*"`). The hosting badge on detail.html:549-550 comes from `ma.hosting_tolerance`, the matched micro-area's data, not from the outcode-level key. `noise_enforcement` is rendered only on area.html, where `area_detail` builds it itself (routes.py:853-857). No test asserts these keys (`git grep` over tests).
- Recommendation: Delete the four assignments and the two imports. Cross-area lead for DATA: `CREATIVE_SCENE` (data/area_context.py:132) and the outcode-level `creative_scene` JSON block would then have no consumer (`git grep CREATIVE_SCENE` finds only routes.py and its definition). Micro-area `creative_scene` stays, because area.html:37 uses it.
- Prior item: A17   | Owner decision needed: no

### WEB-5: Template context keys that no template reads, a filter property that only tests use, and redundant `request`
- Category: DEAD (+TEST-ONLY) | Confidence: CERTAIN (context keys) / HIGH (property) | Action: DELETE | Risk: low | Effort: S | Size: ~45 lines (incl. 17 test lines)
- Location: routes.py:489 `source_names` (dashboard), :500 `any_quality_filter_active`, :505 and :817 `user_statuses`, :808 `source_badges` (detail), :814 `fit_breakdown` (detail); `"request": request` at routes.py:452,482,586,653,661,802,847,863,919,931; filters.py:398-419 `quality_fields_active`; tests/test_web/test_filters.py:180-195 `TestQualityFieldsActive`
- Evidence: grep of templates and static/modules for each word-bounded key. `user_statuses` and `any_quality_filter_active` have 0 hits. Dashboard renders dashboard/_results/_property_card and none uses `source_names`, which appears only in detail.html. detail.html never uses `source_badges` or the bare `fit_breakdown`; the card macro reads `prop.fit_breakdown`. `quality_fields_active` is read only by routes.py:500, whose key is unused, and by its 5 tests (`git grep quality_fields_active`). Starlette 1.3.1 `TemplateResponse(request, …)` already does `context.setdefault("request", request)` (checked with inspect), so the 10 explicit entries are redundant. `user_statuses` was last used by the ticket-7 dropdown design (docs/tickets/ticket-7…:250).
- Recommendation: Remove the keys, the property and `TestQualityFieldsActive`. Keep `secondary_filter_count`, which is used twice. Dropping `"request"` is optional, a mechanical tidy.
- Prior item: A17, A15   | Owner decision needed: no

### WEB-6: `fit_working()` computes `adjusted` and `WorkingRow.weight` for tests only
- Category: TEST-ONLY  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~8 lines
- Location: src/home_finder/web/working.py:10,19,45,48-49,64; tests/test_web/test_working.py:27,36
- Evidence: 37f1210 ("claim only what the evidence supports…") replaced `{% if working.adjusted %}…` in detail.html with fixed legend text (`git show 37f1210 -- …/detail.html`). Since then no template reads `working.adjusted` or `row.weight` (grep), and only test_working.py asserts `adjusted`.
- Recommendation: Drop `adjusted`, the row `weight` and the two assertions, unless the owner plans to show the nominal weight again. The module is 2 days old, so ask whether removal is welcome.
- Prior item: A15   | Owner decision needed: no (low stakes)

### WEB-7: Dead or stale branches in detail.html, including one visible bug
- Category: DEAD  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~6 lines changed
- Location: src/home_finder/web/templates/detail.html:355, :203, :441, :332-333, :168/:417/:440; routes.py:689-697, :766-773 (defensive getattr)
- Evidence: (a) `has_listing_data` at :355 still ORs `epc_rating`, `service_charge_pcm`, `bills_included` and `council_tax_band`. Commit 3570e26 moved those rows out of the `.listing-kv-grid` into `_cost_breakdown.html`, and the grid (:360-379) renders only type, deposit, pets, parking, furnished and broadband. A listing that states only those four fields therefore renders an empty "Listing Details" heading. (b) :203 `qa.one_line.one_line if qa.one_line is mapping` is dead. `qa` is a `PropertyQualityAnalysis` (db/web_queries.py:575) with `one_line: str | None` and a before-validator that unwraps dicts (models/quality.py:547,598-618). (c) :441 `qa and negotiation`: `generate_negotiation_brief` always returns a non-empty dict. (d) The comment `{# 3. Costs … #}` at :332 sits above the annotated-description section, not the Costs section. (e) "(Ticket 7)/(Ticket 10)" breadcrumbs in detail.html:168,417,440 and routes.py:775,779,782. (f) routes.py uses `getattr(qa, "listing_extraction", None)` etc. on a typed model.
- Recommendation: Trim `has_listing_data` to the six rendered fields; that also fixes the bug. Drop the mapping branch, `negotiation` guard, ticket breadcrumbs, and use attribute access in the route. Keep the off-market/availability blocks (A17 says keep).
- Prior item: A17   | Owner decision needed: no

### WEB-8: CLAUDE.md "Web Dashboard Notes" documents a buggy XSS pattern and non-existent variables
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: medium (an agent following it reintroduces a fixed bug) | Effort: S | Size: ~12 lines
- Location: CLAUDE.md "Web Dashboard Notes" (Security bullet; Template variables; Templates list)
- Evidence: CLAUDE.md says descriptions use `| e | replace("\n", "<br>") | safe`. Rendering that with Jinja autoescape yields `'a&lt;script&gt;&lt;br&gt;b'`, a literal `<br>`, which is the bug 5d36b40 says it fixed. The templates now use `| e | replace("\n", "<br>" | safe)` (detail.html:338,520), which yields `'a&lt;script&gt;<br>b'`; both verified with jinja2 in the venv. Dashboard variable `min_rating` does not exist; the field is `min_fit_score`. The card dict lists `quality_rating` (not read by the card) and `status` (it is `user_status`), and omits `fit_score/lead/brief_check/lifestyle_icons/is_off_market/processing_*`. `_status_controls.html` is described as "status pill groups", but it is a popover menu.
- Recommendation: Replace the XSS line with the current pattern, keeping the escape-first rule; do not loosen escaping. Prune the variable lists to "see routes.py context dicts" or update them.
- Prior item: none   | Owner decision needed: no

### WEB-9: README web endpoint table and feature list contradict the code
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~8 lines
- Location: README.md:103,107,157,164 (endpoint table 152-165); docs/operations.md:480
- Evidence: README:157 describes `GET /count` as "Property count JSON", but routes.py:328 returns `text/plain`. README:164 says PATCH status "returns card or badge partial"; it returns the card or `_status_controls.html`, and the badge partial is dead (WEB-3). The table omits `GET /deliveries` and `POST /telegram/webhook`. README:103 says markers are "color-coded by quality rating (green 4-5, amber 3, red 1-2, grey unrated)", but they are price pills with only an off-market variant (map-dashboard.js:71-80). README:107 mentions "Pill-based status controls"; the control is a popover menu. The view toggle is grid/split/map. operations.md:480 calls `/deliveries` "the **Alerts** navigation link", but it is a footer link labelled "Alert delivery" (base.html:64).
- Recommendation: Correct these lines. The DOCS auditor may also own README; coordinate so it is changed once.
- Prior item: none   | Owner decision needed: no

### WEB-10: `update_property_status` docstring is stale, and its card branch duplicates `property_card`
- Category: STALE-DOC (+DUP) | Confidence: CERTAIN | Action: REWRITE-DOC / MERGE | Risk: low | Effort: S | Size: ~20 lines
- Location: src/home_finder/web/routes.py:879-884, :907-924 vs :568-591
- Evidence: The docstring says it returns a card "when HX-Target indicates a card-level swap" and "a status badge partial for detail-page swaps". The code switches on form field `source == "card"` (:908, matching the card's `hx-vals` in _property_card.html:148) and returns `_status_controls.html`. Lines 909-924 repeat property_card's fetch → `_enrich_fit_scores` → `_resolve_cached_thumbnail` → render sequence, and the comment says "same as /property/{id}/card".
- Recommendation: Fix the docstring. Extract `_render_card(request, prop, data_dir)` and use it in both places.
- Prior item: none   | Owner decision needed: no

### WEB-11: Social metadata cannot work behind owner auth, and the PNG icons/og-image still use the pre-restyle purple brand
- Category: DEAD (+STALE-DOC) | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~35 lines + 4 PNGs
- Location: templates/base.html:17-31 (og/twitter block), detail.html:6-17; routes.py:788-796 (`og_image_url`), :812; static/og-image.png, icon-192.png, icon-512.png, apple-touch-icon.png, manifest.json; scripts/generate_icons.py:1-27
- Evidence: `OwnerAccessMiddleware` exempts only `/health` and `POST /telegram/webhook` (access.py:141-157), so link-preview crawlers get a 401 for every page, every `/static/og-image.png` and every `/images/...` og:image. routes.py:791-795 is uncovered by the test run (coverage.json). Brand drift: 5d36b40 ("two-voices look") restyled favicon.svg (#141414/#FBFAF7) and manifest theme/background (#FBFAF7) but not the PNGs. Viewing og-image.png and icon-192.png shows a black ground with a purple-gradient house, and generate_icons.py:10-11 still claims "the same brand language as the dashboard: dark background … purple gradient". The tests that touch this: test_routes.py:1658 asserts `og:description`.
- Recommendation: The owner chooses. Option 1: delete the og/twitter blocks, `og_image_url`, og-image.png and the og part of generate_icons.py, and keep `meta description`. Option 2: keep them for a future public page, regenerate the PNGs in the new palette and fix the script docstring. Either way, fix the script docstring and the icon/favicon mismatch.
- Prior item: A18 (OUTSTANDING)   | Owner decision needed: yes

### WEB-12: The CSP nonce is generated per request but never used
- Category: DEAD  | Confidence: HIGH | Action: SIMPLIFY (or KEEP-BUT-DOCUMENT) | Risk: low | Effort: S | Size: ~3 src lines + ~25 test lines
- Location: src/home_finder/web/app.py:38-39,46; tests/test_web/test_app.py:99,101-119; tests/test_web/test_access.py:145; tests/test_web/conftest.py:107 (comment)
- Evidence: `git grep csp_nonce` finds only the write at app.py:39; no template reads `request.state.csp_nonce`. The only inline `<script>` elements are `type="application/json"` data blocks (_results.html:53-54, dashboard.html:282), which CSP does not execute. All executable scripts are external (`'self'` or jsdelivr with SRI). The `'nonce-…'` source therefore authorises nothing.
- Recommendation: Either drop the nonce (the CSP becomes equal or stricter) and the three nonce tests, or keep it deliberately with a comment that it is reserved for future inline scripts. This is not an escaping change.
- Prior item: none   | Owner decision needed: no (small security-adjacent; mention in PR)

### WEB-13: Write-only and unreachable code in map-dashboard.js, and map payload fields JS never reads
- Category: DEAD  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~6 JS lines (+3 DB lines)
- Location: static/modules/map-dashboard.js:13,250,259 (`markersByPropertyId`), :34 (hard-coded FIT_TIERS fallback); db/web_queries.py:453,455 (`rating`, `title` in `get_map_markers`)
- Evidence: `markersByPropertyId` is assigned and filled but never read (grep). The :34 fallback runs only when `#fit-tiers-data` is missing, but map-dashboard.js is loaded only by dashboard.html, which always renders that element (dashboard.html:282). The fallback also duplicates `fit_score.FIT_TIERS`. JS reads only `p.{id,lat,lon,price,bedrooms,postcode,image_url,url,fit_score,one_line,commute_minutes,value_rating,is_off_market}` (`grep -o "p\.[a-z_]*"`). `rating` (the old star rating) and `title` are serialised into every page's `properties_json` and never used. `is_assessed` is read only by tests (test_processing_visibility.py:98, test_score_consistency.py:63).
- Recommendation: Remove the variable and the fallback. Hand the `rating`/`title` payload trim to the DB auditor and keep `is_assessed` while those tests use it.
- Prior item: A17   | Owner decision needed: no

### WEB-14: "Offer preparation" block now only repeats other parts of the page
- Category: OVERBUILT  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~15 template lines + utils/negotiation.py (~45) + its test
- Location: templates/detail.html:440-454; routes.py:782-786; src/home_finder/utils/negotiation.py; tests/test_utils/test_negotiation.py
- Evidence: After the scores were removed (B7), the brief holds two signals. "First observed … N days ago" repeats the header's "First seen" pill (detail.html:118). "Recorded N decrease(s)…" repeats the Price History card directly above (:418-438). The rest is fixed boilerplate. `NegotiationSignal.category` is never rendered, and the CSS for the scored era (`signal-*`, `negotiation-source`) is dead (WEB-2).
- Recommendation: The owner decides whether to keep a short fixed guidance line or drop the section and the util. B7 asks for "only actionable, non-duplicative guidance".
- Prior item: B7 (PARTIAL)   | Owner decision needed: yes

### WEB-15: `POST /property/{id}/reanalyze` has no UI caller and is blocked for plain API use
- Category: DEAD  | Confidence: MEDIUM | Action: ASK-OWNER (prefer DELETE) | Risk: low | Effort: S | Size: ~13 src + ~45 test lines
- Location: routes.py:939-951; README.md:165 (and :124 "queues reanalysis"); tests/test_web/test_routes.py:1985-2030; tests/test_web/test_access.py:217-240
- Evidence: No template or JS posts to it (`git grep reanalyze` over web templates/static finds nothing, and `git log -S` shows it was never in a template in retained history). Non-GET requests must pass `_same_origin` (access.py:103-110,174), so a `curl` without a matching Origin or Referer gets 403. The CLI `--reanalyze --request-only` (main.py:69-126) already flags properties. The endpoint queues paid analysis.
- Recommendation: If the owner does not use it, delete the route and its tests. Note that `test_mutations_are_rate_limited_and_coalesce` uses it as its sample mutation, so move that test to `PATCH /property/{id}/status` first. Update README:124,165.
- Prior item: none   | Owner decision needed: yes

### WEB-16: Filter plumbing repeats the field list and clamping logic many times
- Category: DUP  | Confidence: HIGH (duplication) / LOW (value) | Action: SIMPLIFY | Risk: medium (filter semantics; T09 tests guard) | Effort: M | Size: ~60 lines
- Location: web/filters.py:119-146,222-240 (four copies of "parse int, then clamp"), :161-180 (outdoor/pets re-implement `_validate_enum_field`), :452-506 (`parse_filters` restates 24 fields twice), :398-444 (two parallel field lists; one goes with WEB-5), :14 "(moved from routes.py)"; templates/dashboard.html:144-230 (option lists duplicate `VALID_*` sets), :79-83 and _status_controls.html:67-71 (status groups duplicated)
- Evidence: Read side by side. FastAPI 0.142.2 in the venv supports Pydantic query-parameter models (`Annotated[PropertyFilter, Query()]`, with alias `tag` for `tags`).
- Recommendation: Optional. Add a `_clamped_int(lo, hi)` helper, use `_validate_enum_field` for outdoor/pets, and pass the `VALID_*` tuples and status groups to templates. Consider a FastAPI query model only with the browser/navigation tests green, because invalid-value handling must stay "silently discard", not 422.
- Prior item: T09 (do not regress)   | Owner decision needed: no

### WEB-17: Small duplication and defensive leftovers in the Python helpers
- Category: OVERBUILT  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~15 lines
- Location: routes.py:287 (`settings: Settings | None = None` and the :318 branch; all three callers pass settings), routes.py:172-192 vs :233-243 (`listing_age_filter` and `days_since_filter` parse ISO dates separately), brief.py:96-105 vs annotate.py:76-85 (identical `_field`, near-identical `_lowlights`), routes.py:342 (`getattr(app.state, "pipeline_lock", None)`; lifespan always sets it)
- Evidence: `git grep _enrich_fit_scores` shows 3 callers, all passing `request.app.state.settings`. The rest is visible by reading.
- Recommendation: Make `settings` required, have `listing_age` reuse `days_since`, and share `_field` in one place (for example a small `web/_analysis_access.py` or `brief.py`). This is low value; fold it into any PR touching these files.
- Prior item: none   | Owner decision needed: no

### WEB-18: CSS compatibility aliases, misleading names and two duplicated rules from the restyle
- Category: COMPAT  | Confidence: LOW | Action: KEEP-BUT-DOCUMENT | Risk: low | Effort: M | Size: ~30 lines (if renamed: ~250 references)
- Location: style.css:124-147 ("Older role names, kept so the rules below read as before": `--bg*`, `--border*`, `--text*`, `--status-*-bg/text`), :1662 (`.badge-green/amber/red` "names predate the look"), :1309 (`fit-green/accent/amber/dim`, names from fit_score.py), duplicate rules :873-877 vs :4085-4088 (`.card-entrance` reduced-motion) and :2407 vs :3174 (`.value-assessment-card h4 margin-bottom`); framework-imports.css:1 comment ("moved from inline <style> blocks")
- Evidence: The css_vars.py run finds NO unused custom property; every alias is still referenced (for example `--text-muted` 66 times and `--border` 42). css_dups.py found only these two duplicated property sets. The other duplicate selectors are the deliberate figure-font grouping at :262/:269.
- Recommendation: Delete the duplicated reduced-motion and h4 rules now. The alias and naming cleanup is a mechanical follow-up the owner may not want right after a restyle.
- Prior item: none   | Owner decision needed: no

## Verified keep (looks dead, isn't)
- routes.py `dashboard`, `health_check`, `filter_count`, `diagnostics`, `delivery_status`, `serve_cached_image`, `property_card`, `property_detail`, `area_detail`, `update_property_status`; telegram_webhook.py `telegram_webhook`; app.py `SecurityHeadersMiddleware.dispatch` (vulture 60%) are FastAPI/Starlette entry points. `/count` is used by `hx-get` at dashboard.html:267, `/property/{id}/card` by map-dashboard.js:206, `/deliveries` by the base.html:64 footer, `/area/` by detail.html:590, and PATCH status by `hx-patch` in _property_card.html and _status_controls.html.
- routes.py:595 `mtime_ns` (vulture 100%, ruff ARG001) is the deliberate `lru_cache` key that invalidates stale hashes.
- web/filters.py `coerce_*`, `validate_*`, `clean_area`, `filter_tags` (vulture 60%) are Pydantic `field_validator`s.
- brief.py `must_total/must_met/must_broken/must_unknown` are read by _property_card.html:60 and `kicker` by detail.html:104; working.py `points` is read by detail.html:244.
- All Jinja filters and globals registered in routes.py:219-246 are used: listing_age, bed_label, gbp, short_date, lowlight_label, json_script_safe, days_since, static_version, fit_tier_label/class, fit_color_class, FIT_TIERS. All 5 macros in _macros.html are used.
- All 8 JS modules are loaded. leaflet-patch.js is loaded by base; filters, map-dashboard and status by dashboard; gallery, map-detail, sections and status by detail; sections by area; map-basemap is imported dynamically by both map modules.
- Static assets favicon.svg, apple-touch-icon.png, manifest.json, icon-192/512.png and og-image.png are all referenced (base.html:10-12,25; manifest.json). Their value is WEB-11.
- CSS `.value-excellent/good/fair/poor` are built at map-dashboard.js:150 from the `value_rating` the marker payload supplies. `verdict-*`, `brief-met/broken`, `mark-*`, `detail-lead-*`, `card-lead-*`, `condition-minor/moderate/serious`, `highlight-value-excellent/good` and `quality-item-fair/neutral` are built from bounded template values. `fit-green/accent/amber/dim` come from `fit_color_class()` (filters/fit_score.py:77-106). `leaflet-*`, `marker-cluster*` and `htmx-*` belong to the libraries.
- No unused CSS custom properties and no unused `#id` selectors (css_vars.py, css_audit.py).
- telegram_webhook.py:33-40 repeats the middleware's secret checks. This is defence in depth, it is covered, and it should stay.
- `_resolve_cached_thumbnail` falls back to original gallery files when thumbnails are missing (routes.py:397-404). It is needed until B5 makes thumbnails universal.
- `| json_script_safe | safe` in the JSON script blocks and `| e | replace(..., "<br>" | safe)` are XSS-relevant and correct. Keep both.
- `status_meta is defined` (_property_card.html:6) and `last_price_change is defined` (:130) are harmless guards. All callers pass these values, but they are not worth churning.

## Notes
- Cross-area leads: DATA: `CREATIVE_SCENE` loses its only consumer after WEB-4. DB: `get_map_markers` returns unused `rating`/`title` (WEB-13), and `quality_rating` is selected for card/detail queries the templates never read. DOCS: README and operations.md lines in WEB-9. TESTS: nonce tests (WEB-12), `TestQualityFieldsActive` (WEB-5), reanalyze route tests (WEB-15). SCRIPTS: generate_icons.py (WEB-11).
- base.html loads Leaflet, markercluster and leaflet-patch.js on every page, including area, deliveries and error pages that have no map. It is harmless but could move into `page_scripts` for the two map pages. Low value, not raised as a finding.
- Suggested PR grouping: (1) WEB-1, 2, 3, 13 and the duplicate rules from WEB-18: dead CSS/JS/partial, browser-suite check. (2) WEB-4, 5, 6, 7, 10, 17: route/context/template tidy, `tests/test_web` single files. (3) WEB-8, 9: docs. (4) Owner decisions: WEB-11 (A18), WEB-12, WEB-14 (B7), WEB-15.
- Approximate removable total without owner input: ~330 CSS lines, ~22 JS lines, ~60 Python lines and ~20 test lines.
