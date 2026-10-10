# Checks and releases

Pull requests and pushes to `main` run lint, formatting, strict types, offline
tests with coverage, and package builds. Dependencies are installed from `uv.lock`;
the build job uses a cold cache to expose unavailable artifacts. CI has read-only
repository permissions and receives no production secrets.

`checks.yml` replaces the inactivity-disabled `ci.yml`. This lets the replacement
workflow validate its own pull request without re-enabling the legacy schedule or
automatic deployment. The existing disabled workflow can remain disabled.

Browser and live portal tests are explicit, default-off dispatch inputs. There is
no automatic live-test schedule. These inputs are not enabled by a release.

The **Release dashboard** workflow is manual, accepts only `main`, reruns checks
on that revision, then uses the `production` environment. Configure that
environment to allow only `main` and provision `FLY_PRODUCTION_API_TOKEN` before
the first release. Retire the legacy repository-wide `FLY_API_TOKEN` when the new
environment is ready. Merging code does not deploy it.

This CI bootstrap changes no application behaviour, database schema, product
scope, or agent execution queue. Runtime dependency upgrades and owner access
remain separate reviewed changes.
