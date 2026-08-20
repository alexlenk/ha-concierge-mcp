# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-20

### Added

- Initial implementation: `/api/concierge_mcp` MCP Streamable HTTP
  endpoint, independent guest-secret authentication, UI-managed entity
  allowlist, and the `get_state` / `list_entities` read-only tools.
- `get_state` filters low-signal Home Assistant attributes out of its
  response; `list_entities` caps results at 50 with a truncation message.
- Optional second auth path for interactive use (e.g. testing via
  Claude.ai): a Cloudflare Access JWT, verified independently of the
  guest secret, off by default.
- `get_history(entity_id, hours=24)` tool: recent state transitions for an
  allowlisted entity, via Home Assistant's `recorder`. Same allowlist gate
  as `get_state`; capped at 7 days of lookback (larger requests are
  clamped, not rejected) and 100 returned transitions (oldest dropped
  first), both reported back to the caller when hit. Fails with an
  explicit `history_unavailable` error, not a crash, if `recorder` isn't
  running.

### Fixed

- MCP Streamable HTTP conformance, found and fixed during live testing
  against Claude.ai's connector (#22, #23, #28): JSON-RPC-level errors
  (unknown method, bad params) now return HTTP 200 instead of 400 — a 4xx
  on a well-formed request made the official MCP client's
  `raise_for_status()` tear down the whole session; `initialize` now
  negotiates the requested protocol version instead of always answering
  with its own; added the spec-mandatory `ping` handler and empty-list
  responses for `resources/list`, `resources/templates/list`, and
  `prompts/list`; any `notifications/*` method (not just
  `notifications/initialized`) now gets a bare 202; `GET`/`DELETE` on the
  endpoint now explicitly return 405 rather than falling through to
  aiohttp's default.
- `verify_secret`'s constant-time comparison no longer raises an unhandled
  `TypeError` (surfacing as HTTP 500) on a non-ASCII `Authorization`
  header — reachable in normal operation, since Cloudflare Access forwards
  its own opaque bearer token in this same header (#26).
- Cloudflare Access team-domain config now normalizes the hostname/URL
  forms Cloudflare itself displays (`myteam.cloudflareaccess.com`,
  `https://myteam.cloudflareaccess.com`) instead of only accepting the
  bare team name — pasting the displayed value used to silently double
  the suffix and break JWKS lookups with no operator-visible error (#24).
- A JWKS fetch failure that isn't a `PyJWTError` (e.g. `OSError` from a
  DNS or connection failure) is now caught explicitly so the Cloudflare
  Access auth check still fails closed instead of surfacing as an
  unhandled 500 (#27).
- Rejected Cloudflare Access JWTs are now logged at `warning`, naming the
  team domain and expected `aud`, instead of `debug` — the previous level
  made the most common misconfiguration (a mismatched `aud` tag)
  practically undiagnosable (#25).
