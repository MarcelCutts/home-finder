# PR03: Patched runtime, compatible dependencies and map configuration

**Date:** 28 September 2026. **State:** implemented and verified for local commit, with explicit residual findings and activation checks; not deployed.
**Parent:** [delivery plan, PR03](2026-09-28-research-synthesis-and-delivery-plan.md#pr03--patch-the-supported-runtime-and-web-dependencies-fix-the-map-contract-m).
**Predecessor:** PR02, commit `84fb43b`. No deployment, source collection, paid API calls or notifications.

## Changes and decisions

The application retains Python 3.11 support and now tests both **3.11.16** and **3.14.7**. Metadata explicitly supports `>=3.11,<3.15`, matching aiogram's supported range rather than implicitly promising an untested future interpreter. Local development and containers use 3.14.7. CI sets `UV_PYTHON` explicitly for each matrix job so the repository's default version cannot silently override the minimum-version test.

uv is pinned to **0.12.19** across CI and containers. The downloaded local tool was checked against its release checksum: `23bf5552d220e0842b65c862097b2ebaeba0064b74eda5e565e77fd25969d8c8`. One Dockerfile now builds the `dashboard` and `collector` targets from the same interpreter and lock. The default/Fly target is `dashboard`, with `--serve --no-pipeline`. Runtime images run as UID 1000, omit build tools and the unused base pip/ensurepip installer, and receive only application build inputs. The Docker context was about 2 MB, excluding the working directory's credentials, databases and research logs.

Inspecting `python:3.14.7-slim-bookworm` showed **SQLite 3.40.1**, Debian `3.40.1-2+deb12u2`. The [vendor patch list](https://sources.debian.org/patches/sqlite3/3.40.1-2%2Bdeb12u2/) lacked the [WAL reset fix](https://www.sqlite.org/wal.html#walreset). Merely upgrading Python would not fix that library. The selected uv-managed build reports **SQLite 3.53.1** and **OpenSSL 3.5.8** on both tested interpreters and in the container. The image build checks Python's exact patch and rejects SQLite below 3.51.3. This establishes the WAL fix, not the absence of every SQLite defect; 3.53.4 is newer, and bundled native libraries require continued review.

The Debian trixie and uv images are pinned by digest in the Dockerfile. The Python download is selected and checksum-verified by pinned uv. Native package updates still need a fresh image audit; apt security packages are resolved during the build, so this is not a claim of bit-for-bit image reproducibility forever.

## Dependency repair and compatibility

The original pip-audit result contained **135 advisory entries across 18 packages**, representing **72 distinct advisory IDs**, not 135 distinct exploitable defects. Relevant paths include form parsing, HTTP redirects/response handling, listing/image parsing, and JSON repair. Other findings concern optional server features, developer tooling or local configuration. All reported package fixes were applied in compatible groups:

| Package | Previous | Selected |
|---|---|---|
| aiogram | 3.24.0 | 3.30.0 |
| aiohttp | 3.13.3 | 3.14.3 |
| anyio | 4.12.1 | 4.14.2 |
| click | 8.3.1 | 8.3.3 |
| curl-cffi | 0.14.0 | 0.15.0 |
| fastapi | 0.128.6 | 0.135.1 |
| idna | 3.11 | 3.15 |
| json-repair | 0.58.0 | 0.60.1 |
| lxml | 6.0.2 | 6.1.0 |
| pillow | 12.1.0 | 12.3.0 |
| protego | 0.6.0 | 0.6.2 |
| pydantic-settings | 2.12.0 | 2.14.2 |
| pygments | 2.19.2 | 2.20.0 |
| pytest | 9.0.2 | 9.0.3 |
| python-dotenv | 1.2.1 | 1.2.2 |
| python-multipart | 0.0.22 | 0.0.31 |
| requests | 2.32.5 | 2.33.0 |
| soupsieve | 2.8.3 | 2.9 |
| starlette | 0.52.1 | 1.3.1 |
| urllib3 | 2.6.3 | 2.7.0 |

The lock retains 127 entries. Direct and transitive security floors prevent resolving below the selected fixes. Starlette is a direct dependency because the application imports its middleware/responses and needs its fixed form parser. Twelve template calls now use the request-first API before the Starlette upgrade. Status forms explicitly allow at most four fields, zero files and 1 KiB per field, alongside PR02's 64 KiB complete-body bound. URL-encoded over-limit requests are rejected before changing status.

The aiogram update requires aiohttp 3.14 compatibility. Its expanded media union exposed mutable-list invariance in mypy; constructing a fresh list at the send boundary preserves the existing photo behaviour without a type-ignore or changed Telegram workflow. Existing album tests passed.

Anthropic remains **0.81.0**, bounded below 1 pending PR20. Crawlee remains **1.3.2b4**, using the original wheel URL and original SHA-256 `141437aa45abbac7932e6aaeff951402c576e4697cb3efaca29541d661fa0962`; that prerelease is no longer available through the current index listing. An explicit uv source prevents this dependency repair from silently changing the scraper transport. Cold installation verified the retained artifact remains downloadable.

Primary checks include the [Starlette form-limit advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq), [curl_cffi redirect advisory](https://github.com/lexiforest/curl_cffi/security/advisories/GHSA-qw2m-4pqf-rmpp), [Pillow 12.3 release](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html), and official PyPI release metadata. Upgrading curl_cffi does not establish a complete supplied-URL/redirect/egress policy; that is still required for PR22.

## Maps

Both map modules now use one CARTO raster helper with `HOME_FINDER_CARTO_BASEMAP_API_KEY`, correct provider origin in CSP, and visible OpenStreetMap/CARTO attribution. The browser-visible key is encoded in tile URLs; immutable asset versions propagate to the shared module import. The owner must provision a dedicated tile key and constrain allowed origins in CARTO's console. No account was created or terms accepted on the owner's behalf.

Without a key, the grid remains usable, map modes are disabled, and detail pages offer an external location link. Tile rejection or a missing Leaflet runtime falls back to the same usable interface. This avoids presenting a broken map or making keyless tile requests. Provider-error handling stops the map rather than endlessly requesting failed tiles.

The [current terms](https://www.carto.com/legal/basemap-terms/) are dated **25 September 2026**, newer than the key page's 23 September wording. They require a key and visible attribution, with 5 million monthly requests for non-commercial use or 1 million for commercial use, aggregated across keys. Business operation can qualify as commercial without charging users. The [key/pricing page](https://www.carto.com/basemaps/apikey/) lists $500/month or $5,000/year above the commercial free allowance, up to 10 million requests. These are provider terms, not an application budget forecast. Set usage alerts/review actual traffic before increasing cohorts; do not automatically buy a plan.

Browser tests intercept tiles with clearly labelled fixture images. They verify both URL/key contracts, loaded tiles, attribution, absent-key behaviour and provider rejection, without sending fake credentials to CARTO. **A real key, its origin restrictions and live tile appearance remain an activation check.** The passing browser suite does not claim that an unprovisioned production map works.

## Verification and audit limits

| Check | Result |
|---|---|
| Full default suite, Python 3.11.16 / SQLite 3.53.1 | **2,448 passed**, 6 warnings, **85.45%** coverage, 231.28 seconds |
| Full default suite, Python 3.14.7 / SQLite 3.53.1 | **2,448 passed**, 4 warnings, **85.33%** coverage, 249.57 seconds |
| Affected subsystem checks before full suites | 1,145 passed |
| Final aiogram album/media checks | 4 passed; all sends mocked |
| Full Chromium dashboard suite | **55 passed**, 2 warnings, 24.06 seconds |
| Ruff lint/format, strict mypy, JavaScript syntax | Passed; 165 Python files formatted, 64 source files checked |
| actionlint and diff whitespace | Passed |
| Cold locked install, all extras, new environment | Passed; 125 packages, followed by successful `uv pip check` |
| Uncached source archive/wheel build | Passed |
| Both Docker targets | Built locally; no push/deployment |
| Fly build configuration | TOML parsed and `build-target` checked against official configuration docs; Fly CLI validation requires authentication and remains a release check |
| Isolated dashboard runtime smoke | Passed: empty temporary DB, schema startup, health, anonymous rejection, owner pages/diagnostics; no external network |
| pip-audit 2.10.1 after updates | No known vulnerabilities reported for the installed environment |
| Trivy 0.74.0 | Additional findings described below; not a zero-finding image |

Local evidence and fixture screenshots are under `/tmp/home-finder-pr03/`. Chromium uses the same Ubuntu 24.04 build workaround documented in PR02 because this workstation runs Ubuntu 26.04. Residual warnings concern the older TestClient HTTPX integration and Uvicorn's legacy WebSocket adapter; no WebSocket application is enabled by PR02. SDK/transport migration remains deliberately separate.

Container scanning matters: it identified unused pip-vendored msgpack/setuptools that the installed-environment audit did not see. Base pip and ensurepip are removed from the final runtime, while package installation remains in the builder. The final Python scan retains only the low-severity Crawlee finding below. The [committed audit inventory](../reviews/2026-09-28-runtime-audit.json) records all remaining native/Python findings, versions and image identity. The final image ID is `sha256:79ec4c1a0e16570aac290b2fb9bb3b499602c6312e066d81cab40529eec01398`.

Known residuals and their boundaries:

- **Crawlee CVE-2026-46497 / GHSA-3r75-xc34-5f44 (low):** [the maintainer advisory](https://github.com/apify/crawlee-python/security/advisories/GHSA-3r75-xc34-5f44) requires sitemap/robots sitemap loading. The application invokes neither; Rightmove seeds its crawler directly. Collection remains paused/gated. Upgrade or remove Crawlee in PR11 before introducing sitemap discovery or new source capabilities. Do not treat pip-audit's zero result as overriding this finding.
- **Debian image findings:** the initial/final baseline has 154 package/advisory rows covering 68 distinct IDs: 43 high, 53 medium, 56 low and two unknown rows, with no fixed Debian version offered by the scanner. Many rows repeat one util-linux advisory across binary packages. There are no critical rows. These counts are inventory, not 154 demonstrated application vulnerabilities.
- **High groups:** privileged mount/nsenter operations, ACL symlink operations, ncurses terminal handling, systemd-homed and Perl Archive::Tar. The service runs as UID 1000 and exposes none of these as application operations; systemd-homed and Archive::Tar are absent. Debian's [systemd record](https://security-tracker.debian.org/tracker/CVE-2026-16742), [nsenter record](https://security-tracker.debian.org/tracker/CVE-2026-78408) and [ncurses record](https://security-tracker.debian.org/tracker/CVE-2025-69720) were checked alongside image contents. This is a bounded reachability assessment, not proof that all local escalation chains are impossible.
- **Medium/native residuals:** glibc/DNS, archive/encoding and native-library findings need continuing vendor monitoring. DNS is used by outbound clients, so these must not all be labelled unreachable. System SQLite remains a Debian package for system utilities; the application's Python uses its separately bundled SQLite with the WAL fix. Keep untrusted arbitrary SQL/database imports and extension loading out of the application. Review current Debian/native fixes again before public hosted rollout and after every base-image change.

CI now tests both interpreter endpoints, builds both container targets, and runs the Python dependency advisory gate. This does not replace periodic container/native-library review, source qualification or the actual ingress checks in PR02. No broad ignore list was added to make scanners green.

## Rollout and next segment

Release through the manual workflow only after the access/backup/source gates already in the delivery plan. Preserve the existing data volume's UID-1000 access. Confirm the actual runtime/SQLite versions at deployment, owner access at ingress, and the map's fallback or provisioned key. Keep a previous protected image with a compatible schema for recovery; do not restore the old public application or replace live databases.

**Next: PR04**, complete database transaction/read ownership. It needs no destructive schema change and can proceed locally while external activation checks remain pending. Continue verified segment commits under the owner's latest instruction. PR03 does not complete the overall delivery goal.
