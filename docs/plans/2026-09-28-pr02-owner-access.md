# PR02: Owner access and callback integrity

**Date:** 28 September 2026. **State:** committed as `84fb43b`; not deployed.
**Parent:** [delivery plan, PR02](2026-09-28-research-synthesis-and-delivery-plan.md#pr02--protect-the-private-dashboard-mutations-and-telegram-callbacks-m).
**Predecessor:** PR01, commit `1dfdcb4`. No production configuration or deployment has been changed.

## Behaviour and scope

Anonymous requests previously reached property pages, HTMX fragments, cached images and writable routes. A pure ASGI access boundary now covers the complete application, including mounts and OpenAPI. FastAPI's HTTP Basic implementation provides the browser login challenge; the application compares both credential fields using constant-time byte comparisons. A random ASCII owner password of at least 24 characters is required. Missing, short or incompatible credentials in configuration fail closed with HTTP 503. The health endpoint remains a minimal `{"status":"ok"}`; pipeline diagnostics moved behind authentication at `/diagnostics`.

This is a single-owner access mechanism for the existing modular application. It adds neither a homemade session protocol nor the accounts/tenant model reserved for PR25. Hosted Basic authentication requires HTTPS. Credentials belong in the deployment secret store, never links. Browser-managed Basic credentials do not provide an application logout screen; use a separate private browsing session and rotate the password to revoke access. Sensitive HTML/JSON responses use `no-store`; owner image/static responses use private caching. Existing copies already downloaded by a browser cannot be recalled by password rotation.

Mutations require an exact origin match (scheme, hostname and port), using `Origin` or, when absent, `Referer`. Explicit null origins, cross-site fetches and missing origin evidence are rejected. The configured canonical `WEB_BASE_URL` is authoritative behind a proxy; local development can derive its origin from the request. Form actions and base URLs are constrained by CSP too. Client-side scripts do not need embedded credentials or CSRF query parameters. Non-browser owner clients must include a matching `Origin` header.

The access boundary enforces 64 KiB request bodies, including chunked uploads or dishonest Content-Length, and a 10-second body deadline before form/JSON parsing. Limits are 10 failed authentication attempts/minute per ASGI connection peer and 30 mutations/minute each for owner and webhook traffic, with `Retry-After`. Client bookkeeping is bounded to 1,024 keys. These are process-local limits, not a distributed quota or a substitute for trusted proxy connection limits. Forwarded client identity must be configured at the ASGI server/ingress rather than trusted from arbitrary headers in this middleware. Operate one app worker at this stage.

Repeated reanalysis requests retain the first pending timestamp, including bulk requests. The existing queue remains CLI-drained; paid execution budgets and durable claims are PR13/PR12b. HTTP mutations are serialised within one app instance to protect the immediate web path; shared pipeline/CLI connection ownership remains an explicit PR04 requirement.

## Telegram changes and schema

Callbacks require all three of: the Telegram webhook secret, the configured chat, and the configured owner user. A positive private chat ID supplies the default owner; a group chat requires an explicit positive `TELEGRAM_OWNER_USER_ID`. Missing chat context, wrong senders, invalid update IDs and malformed payloads cannot mutate state. Unknown exact property IDs are acknowledged without an update. Only interested/archived button actions are accepted.

Schema migration 007 adds nullable `status_events.telegram_update_id` and a unique partial index. The replay check, property status write and event insertion share a transaction. After a delivery is committed, retrying its update ID cannot duplicate history or undo a newer owner decision, including after restart. IDs are compared for equality, not against a high watermark: Telegram documents that IDs can restart from a random value after a week without updates. Legacy history has null IDs and stays intact. The migration is repeatable for legacy version-zero databases as well as a normal schema-6 upgrade.

Status buttons use the full property ID. If their payload exceeds Telegram's 64-byte limit, the notifier omits the action buttons and keeps source/details links. It never truncates an identity. Stable short callback tokens remain part of the future identity work in PR07; no historical collision is claimed.

## Verification

The initial complete-app access tests failed against the baseline: anonymous dashboard/count/fragment reads returned 200. They pass with the access boundary. Regression cases cover authorised HTMX changes, cached-image authentication/private caching, same-origin/Referer/proxy behaviour, wrong or malformed credentials, failure/mutation limits and recovery, oversized/chunked/misdeclared bodies, concurrent web writes, concurrent duplicate callbacks, owner/chat mismatch, explicit group owners, malformed payloads, replay after a later owner decision, and durable deduplication after restart. All Telegram answers are mocked; no messages were sent.

The default offline suite passed **2,445 tests**, with **85.24%** branch-inclusive coverage, in 194.74 seconds. After the final ASCII-password validation/recovery case was added, **53 access/migration tests** passed again. Ruff lint/format and strict mypy passed (165 Python files formatted, 64 source files checked). Existing template deprecations are addressed in PR03.

The browser status check exposed a pre-existing layout bug: the status popover could open beyond the viewport, preventing an ordinary click. A small CSS fix adds anchor-position fallbacks and viewport bounds. The complete Chromium dashboard suite passed **52 tests** in 22.67 seconds, including a real status click/reload/reset and the existing filter/HTMX flows. Fixture screenshots are under `/tmp/home-finder-pr02-browser-output/`. The locked Playwright browser was initially absent; this Ubuntu 26.04 host is not recognised by that installer. The Ubuntu 24.04 Chromium build downloaded into `/tmp/home-finder-browsers` runs successfully with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`; CI itself uses Ubuntu 24.04. No browser version in the lock was changed in this segment.

Migration rehearsal used SQLite backups into `/tmp/home-finder-pr02-migration/`, never application startup against the frozen reference. SHA-256 of `data/production-review/2026-09-27T122222Z/properties.db` remained `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. Schema 7 preserved every pre-existing column value across all 13 tables. Counts include 1,016 properties, 3,267 source listings, 1,001 analyses, 30 status events, 106 price-history rows and one viewing message. The migrated copy passed integrity and foreign-key checks. Restoring the baseline backup to another temporary database reproduced all table fingerprints. The script and full report are local temporary artifacts, not published property data.

## Rollout and recovery

1. Keep deployment/collection gates in the parent plan. Back up the actual authoritative database consistently; the frozen export rehearsal does not replace a live backup.
2. Provision the owner username/password, canonical HTTPS `WEB_BASE_URL`, webhook secret and Telegram owner/chat settings through the deployment's secret/configuration mechanisms. Check the intended ingress and proxy trust configuration. An unset password deliberately produces 503 for private routes while public liveness remains green.
3. Deploy only through the reviewed manual release flow. Verify unauthenticated pages/images/mutations are rejected at the actual public ingress, authorised pages and HTMX changes work, and another Telegram user/chat cannot act. Local checks do not establish that today's deployed service is protected.
4. For credential mistakes, correct the secret and restart the same protected application. Regression tests exercise recovery after a fail-closed startup. Basic-auth browser credential caches may require a new private session after rotation.
5. If application rollback is necessary, use a protected maintenance service or ingress. The previous application rejects schema 7, so a compatible application or a consistent pre-migration backup is needed. Restoring that backup would discard subsequent writes: capture and reconcile those first. Do not lower `user_version` on a live database or restore the former publicly writable application.

Next eligible implementation segment: **PR03**, compatible runtime/security dependency repair and the map contract, followed by PR04's complete transaction ownership boundary. No per-segment pause is required under the owner's latest instruction. The existing native goal still reported paused during this turn; its exposed tools cannot resume it. Implementation proceeded under the owner's instruction while awaiting the goal UI's Resume control. Do not mark the overall delivery goal complete for this segment.

## Primary references checked

- [FastAPI HTTP Basic authentication](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/): browser challenge and constant-time comparisons.
- [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html): exact origin checking, missing-origin handling and canonical target configuration behind proxies.
- [Telegram Bot API](https://core.telegram.org/bots/api#update): update identity/retries, callback sender/chat context, webhook secret and callback payload limits.
- [Playwright browser contexts](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-http-credentials): scope browser test credentials to the local fixture server's origin.
- [MDN anchor position fallbacks](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/position-try-fallbacks): keep the status menu within the viewport.
