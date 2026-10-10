# Bind fetched details to the advert that was requested

Status: implementation, author review and local verification complete; draft publication pending. Base: `d1689642223ab898a4e3a168812a7f0c63215b70`, [draft #34](https://github.com/MarcelCutts/home-finder/pull/34). Branch: `codex/stack/32-detail-response-identity`, intended position 32 in native stack #29. This is corrective queue item 2. Schema remains 015.

## Problem and outcome

The independent review's OnTheMarket example reproduced on the preceding head: a request for advert `111` received a response whose URL and main property ID both identified `222`, yet produced an immutable observation attributed to `onthemarket:111`. Request provenance was being mistaken for returned-page identity. The same missing final-URL check existed in every detail adapter; Rightmove's existing embedded-ID check covered only part of the problem.

All four detail adapters now require agreement between the requested provider identity, the final response URL and the supported identity markers in the returned page. A refusal returns no `DetailPageData`, so its description, features, area and image references cannot enter enrichment or the observation ledger. The checks also run when recording observations is disabled, including offline capture replay. They do not change deduplication thresholds or decide whether two different adverts describe the same home.

This is a prospective ingestion repair. Previously retained observations are not rewritten, repaired or retrospectively certified by the change. A wrong or unrecognized response is unresolved evidence, not proof that the requested property has left the market.

## Identity contract

The existing exact provider-identity resolver checks the declared ID against its recognized listing URL before any HTTP request. Numeric IDs and recognized historical URL-shaped IDs must resolve to the same provider advert. Arbitrary IDs cannot borrow the number from a URL. Each successful response then uses the transport's actual `response.url`, which must identify that same advert on an allowed provider host and exact listing path.

The page must contain at least one supported identity marker. Every marker present in the selected main record or page head must agree; a matching canonical URL does not excuse a conflicting main ID, or vice versa.

| Adapter | Main record checked | Other page identity |
| --- | --- | --- |
| Rightmove | Decoded legacy/indexed `propertyData.id`; the indexed contract still requires its ID | Head canonical/Open Graph URLs, if present; a legacy record without an ID requires a page identity URL |
| Zoopla | `props.pageProps.listing.id` and `listingId`, if present; IDs on the RSC taxonomy actually selected for description/area are also checked | HTML/RSC fallbacks require a head canonical or Open Graph URL |
| OnTheMarket | `props.initialReduxState.property.id` and its `canonicalUrl`, if present | Head canonical/Open Graph URLs; the main record must be an object |
| OpenRent | Existing HTML extraction, after page qualification | Head canonical or Open Graph URL is required |

IDs accept exact strings or integers, not booleans, floating-point approximations or nulls. Supported `.com`/`.co.uk`, `www`, path, trailing-slash and query/fragment variants are matched through the existing provider path rules. Page identity URLs may use explicit relative listing paths. Query-only or fragment-only self-references cannot establish body identity. Head marker collection is bounded at sixteen tags; diagnostic marker names are deduplicated.

Related-advert links, image filenames, agent IDs and nested feature IDs are not treated as main advert identity. The retained OnTheMarket captures contain recommendations, so recursively comparing every `id` in the page would reject valid detail pages. This change qualifies the selected page/record; it does not prove semantic ownership of every image or field found by the existing HTML/RSC fallback extractors. Those extractors and current source coverage still need their ordinary qualification evidence.

## Research and retained-format verification

HTTPX documents that a response after followed redirects exposes the final URL, with earlier responses available in history. The existing Rightmove client enables redirect following, so checking the requested `Property.url` alone was insufficient. A socket-blocked `httpx.MockTransport` experiment exercised the real redirect machinery: a redirect to an equivalent host/path alias was accepted; a redirect to another advert was refused even when its body still declared the originally requested ID. Each case made exactly two mock transport dispatches. [HTTPX redirect documentation](https://www.python-httpx.org/quickstart/#redirection-and-history).

For curl-backed adapters, the installed curl_cffi implementation populates `Response.url` from `CurlInfo.EFFECTIVE_URL`. libcurl documents this as the last URL actually used, which can differ from the input when following redirects. The implementation checks that field; it does not mistake `redirect_url` for the completed response's location. This is returned-data qualification, not a new redirect transport policy. [curl_cffi response API](https://curl-cffi.readthedocs.io/en/stable/api.html), [libcurl effective URL](https://curl.se/libcurl/c/CURLINFO_EFFECTIVE_URL.html).

Both retained successful OnTheMarket pages expose a main ID and matching canonical URL. Both retained OpenRent pages expose a canonical URL on the older `.co.uk` host. Offline probes retain successful extraction for those four unchanged files and the sanitized, retained Rightmove indexed fixture: gallery counts are respectively 10, 10, 9, 10 and 9. Changing the supplied final URL rejects all five; changing the main ID/canonical in each OTM/OpenRent body also rejects it. Original capture hashes match the old request ledger and remain unchanged.

The old collection capture did **not** retain final redirect targets. These probes therefore supply the recorded request URL as a simulated final URL; they verify parser compatibility and adversarial rejection, not historical redirect behavior or current transport availability. Zoopla has synthetic contract coverage only here: the retained collection search was a 403 and supplied no successful detail capture. No fresh portal request was made.

## Diagnostics, tests and author review

`detail_identity_refused` records source, requested-URL SHA-256 and a bounded reason such as `response_listing_mismatch`, `embedded_listing_mismatch`, `page_listing_mismatch` or `missing_page_identity`. Invalid request identities fail before fetching. `detail_identity_verified` records the page check, numeric provider ID, final-response URL hash and marker names. It is not an assertion that later parsing, persistence, image download or assessment succeeded. New diagnostics include no raw response URL, query string, page contents or credentials.

The detail parser fingerprint now covers the identity qualifier and reused identity resolver. A changed qualification policy cannot silently reuse the preceding parser fingerprint; existing observation payloads remain immutable. `ObservationRequest` continues to describe request intent, and is not relabeled as stored proof of the final URL.

Seventy-seven new regression cases exercise inconsistent requests without HTTP, redirects to another advert, foreign/generic/missing/credential-bearing response URLs, supported aliases, exact legacy IDs, absent and conflicting body identity, malformed main records, selected RSC taxonomy conflicts, relative page markers and marker bounds. They also exercise actual enrichment and capture replay: a refused OTM advert produces no retained observation, wrong-advert text or image download, and remains failed/unresolved.

Older parsing fixtures now declare numeric advert IDs, real provider response paths and the main-record/head identity that the successful tests require. Their existing parsing, quantity and capture assertions are preserved. The first full matrix exposed twelve additional floor-area cases using `test-1`/`example.com` or missing final URLs; those fixture identities were corrected without changing area expectations. The updated floor-area file passes all fifty tests. An AST comparison proves all **385 existing assertions** in the four corrected test files are unchanged.

Both full socket-blocked suites pass **3,491 tests**: Python **3.11.16** with **88.69%** line/branch coverage and Python **3.14.7** with **88.62%**. The new qualifier has 100% measured line/branch coverage on both. Focused checks also passed 373 parser/capture cases and the fifty floor-area cases. Ruff lint and formatting (270 files), strict mypy (121 source files) and diff checks pass. All 155 application files in the wheel and 328 checkout files in the source archive match byte-for-byte; private artifacts are excluded. An isolated wheel import exercises the new qualifier and fingerprints all four parsers. Counts, hashes and limits are in the [verification record](2026-10-10-detail-response-identity-verification.json). Private scripts, original-failure logs, corrected runs, manifests and retained-byte probes are in `.worktrees/evidence/32-detail-response-identity`, outside Git. This is an author review, not independent approval. No browser behavior was changed or newly certified.

## Stack boundary and next action

This layer keeps response identity and its ingestion regressions together. Its architectural prerequisites are the shared Rightmove decoder/capture boundary (#13), detail observations (#19), verified replay (#21), and exact provider resolver (#27). Its immediate Git base is #34; neither reviewed regrouping nor further projection machinery is needed to justify this parser repair. Earlier stack heads are preserved.

Next implement **qualified bill inclusion and uncertain council-tax band wording**, then frozen-export hot-journal validation. The later readiness/CI and small core-flow pilot steps remain as ordered in the delivery plan. Reviewed regrouping stays disabled by default, automatic matching is unchanged, and the seventeen historical ownership conflicts remain separate.

Remote CI remains `disabled_inactivity`; passing remote checks are not claimed. No release, active database mutation, paid model request, portal request or Telegram send occurred. The full hosted-service delivery goal remains active.
