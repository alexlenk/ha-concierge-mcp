# Implementation Status

This tracks where the project actually is against `docs/DESIGN.md`'s
phased plan (§16), so a future session — human or agent — can pick up
without re-deriving context from scratch. Update this file whenever a
milestone's status changes; don't let it go stale.

## Done

**M1 — Local MVP** (design doc §16)

- Full `custom_components/concierge_mcp` integration: config flow (secret
  generation + one-time display), options flow (entity-picker allowlist +
  secret regeneration), the `ConciergeMCPView` HTTP endpoint at
  `/api/concierge_mcp` with its own `hmac.compare_digest` secret check,
  `get_state`/`list_entities`/`get_history` MCP tools, diagnostics
  redaction.
- `get_history(entity_id, hours=24)`: recent state transitions for one
  allowlisted entity via Home Assistant's `recorder` (`after_dependencies`,
  not a hard dependency — an operator can legitimately run without it).
  Same `CONF_READ` allowlist gate as `get_state`. Follows the same
  tool-design guidance as the other two tools: a natural-language `hours`
  duration instead of requiring the caller to compute ISO-8601
  start/end timestamps; out-of-range `hours` is clamped (not rejected)
  with the effective window reported back; results capped at 100
  transitions (`MAX_HISTORY_STATES`), keeping the most recent when
  truncating, with a truncation message mirroring `list_entities`'s.
  Returns an explicit `history_unavailable` error rather than crashing if
  `recorder` isn't set up.
- Tool responses were checked against Anthropic's tool-design guidance
  (namespacing, high-signal responses, natural-language identifiers,
  truncation for large results): `get_state` strips low-signal HA
  internals (`icon`, `supported_features`, etc.) from `attributes` rather
  than passing the raw dict through, and `list_entities` caps at 50
  results with a truncation message rather than being unbounded.
- Added a second, independent, opt-in auth path
  (`cloudflare_access.py`) for a human operator to use the endpoint
  interactively — e.g. adding it to Claude.ai for testing — via a
  Cloudflare Access JWT (`Cf-Access-Jwt-Assertion` header), verified
  against Cloudflare's own public keys with `aud` checked against a
  specific Access Application. Entirely additive: inert unless both
  `cf_access_team_domain` and `cf_access_aud` are explicitly configured
  in options, never a fallback that weakens the guest-secret path. Found
  and fixed a latent bug while adding this: each options-flow step was
  overwriting `entry.options` wholesale instead of merging, which would
  have let saving the allowlist silently wipe out Cloudflare Access
  config (or vice versa) — all steps now merge into the existing options.
- Full local pytest suite (51 tests) using
  `pytest-homeassistant-custom-component`, run against a real installed
  `homeassistant` (2026.2.3 at the time this was written) — not just
  written and assumed correct. 100% line coverage, `ruff` clean.
- Explicitly includes the two tests the design doc calls load-bearing
  (§10): the regression test proving a genuine HA access token is
  rejected at this endpoint (`tests/test_http.py::test_valid_home_assistant_access_token_is_rejected`),
  and the cross-integration isolation test running the real official
  `mcp_server` integration alongside this one in the same `hass` fixture
  (`tests/test_http.py::test_cross_integration_isolation_with_official_mcp_server`).
- Manual end-to-end test against a real local HA instance now **has**
  happened — the maintainer connected Claude.ai's custom connector against
  a live deployment behind Cloudflare Access. This surfaced real
  MCP-transport conformance gaps no unit test caught, since they only
  matter against a real client: JSON-RPC errors returning HTTP 400 (killed
  the client's session on any error at all), no protocol-version
  negotiation, and missing `ping`/`resources/list`/`prompts/list` handlers
  (issues #22, #23, #28 — fixed, tests backfilled). Also surfaced a
  Cloudflare-side gotcha unrelated to this integration's code: Cloudflare's
  "Block AI bots" setting 403s Claude.ai's backend before Access's OAuth
  challenge is even issued (#29, documented in README).
- That same round of live testing found four more real bugs in the
  Cloudflare Access auth path, now fixed with regression tests: a
  non-ASCII `Authorization` header crashed the guest-secret check with an
  unhandled 500 (#26); a JWKS fetch failure that isn't a `PyJWTError`
  (e.g. DNS failure) could do the same on the Cloudflare Access path
  (#27); the team-domain field didn't accept the hostname/URL forms
  Cloudflare itself displays, silently breaking JWKS lookups (#24); and
  rejected JWTs logged at `debug`, making the most common
  misconfiguration undiagnosable (#25).

**M2 — CI hardening** (§16)

- `.github/workflows/validate.yml` (hassfest + hacs/action),
  `test.yml` (pytest), `release.yml` (tag-triggered GitHub Release),
  `.github/dependabot.yml` (pip + GitHub Actions) are all written and
  committed.
- **Not yet observed running** — these were written and pushed but this
  session had no way to trigger/watch GitHub Actions before opening a PR.
  Check the Actions tab (or the PR's check runs) the first time this repo
  gets a PR, and fix anything that doesn't match what passed locally.
- Not done: first tagged release. No `v0.1.0` tag exists yet.

## Outstanding (tracked as GitHub issues)

| # | Title | Design doc ref |
|---|---|---|
| [#1](https://github.com/alexlenk/ha-concierge-mcp/issues/1) | M3: Validate against the real Bedrock AgentCore MCP client; decide on SSE transport | §8.6, §16 (M3), §17.3 |
| [#2](https://github.com/alexlenk/ha-concierge-mcp/issues/2) | M4: Automate upstream mcp_server drift detection | §12.1, §12.3, §13, §16 (M4) |
| [#3](https://github.com/alexlenk/ha-concierge-mcp/issues/3) | v2: Add call_service (control) tool support | §8.4, §8.8 |
| [#4](https://github.com/alexlenk/ha-concierge-mcp/issues/4) | v2: Multiple guest profiles | §8.2, §8.8, §17.2 |
| [#5](https://github.com/alexlenk/ha-concierge-mcp/issues/5) | Revisit in-integration rate limiting | §8.8, §9 |
| [#6](https://github.com/alexlenk/ha-concierge-mcp/issues/6) | Optional: domain-wildcard allowlist entries | §8.3, §17.1 |
| [#7](https://github.com/alexlenk/ha-concierge-mcp/issues/7) | Optional: submit icon to home-assistant/brands | §14 |

None of these block using the integration as-is for its stated v1 scope
(read-only, single guest profile). They're staged for when the real
client (#1) or real usage patterns (#3–#7) actually demand them — the
design doc is explicit that several of these should *not* be built
speculatively (see §17).

## Things worth knowing if you pick this up

- **Test dependency pinning was fiddlier than expected.** `pip`'s default
  resolution kept landing on ancient, mutually-incompatible versions
  (`homeassistant==0.31.1`!) when `mcp` was added to an unpinned install
  alongside `pytest-homeassistant-custom-component`. What worked: install
  `pytest-homeassistant-custom-component` pinned to an exact version
  first (its own `requires_dist` pins an exact `homeassistant==` version —
  check that pin via PyPI's JSON API, not `pip index versions`, which
  returned a stale/truncated list in this environment), then add `mcp`
  separately. `requirements_test.txt` reflects a known-good, verified
  combination as of this write-up; don't assume it'll resolve cleanly
  forever without re-checking.
- **The cross-integration isolation test needs upstream `mcp_server`'s own
  transitive deps** (`aiohttp_sse`, `hassil`, `home-assistant-intents`),
  pinned to whatever `homeassistant.components.conversation`'s
  `manifest.json` declares for the installed HA version — these are
  *not* implied by `mcp_server`'s own `manifest.json` requirements in an
  obvious way; check `conversation`'s manifest directly if this breaks.
- **Two real API-drift issues surfaced during implementation** (exactly
  the kind of thing §12.2 warns about) and are already fixed in the
  committed code, not just noted here:
  - `OptionsFlow.config_entry` is a read-only property on current HA
    (resolved from `self.hass`/`self.handler` after construction) — it
    must not be assigned in `__init__`. Older HA required assigning it
    manually. `options_flow.py` has a comment explaining this; re-check
    it before bumping the minimum supported HA version.
  - `manifest.json`'s `"single_config_entry": true` makes HA's flow
    framework itself abort a second setup attempt with reason
    `"single_instance_allowed"`, before `async_step_user` ever runs — a
    manual `_async_current_entries()` check in the config flow was dead
    code and has been removed.
- `hacs.json`'s `"homeassistant": "2024.12.0"` minimum is a reasonable
  floor (roughly where the `OptionsFlow.config_entry` property pattern
  above became current) but was not exhaustively bisected — treat it as
  a starting point to revisit during the first upstream sync review
  (issue #2), per §12.3's instruction not to leave it stale silently.
