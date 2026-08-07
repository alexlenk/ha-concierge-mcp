# Home Assistant Concierge MCP — Requirements & Design Document
**Status:** Draft for implementation
**Audience:** Implementing engineer/agent (Claude Code), reviewers
**Name:** Concierge MCP (domain: `concierge_mcp`) — see §17 for naming rationale
**Repository:** `alexlenk/ha-concierge-mcp`
**Distribution:** Public GitHub repo, listed on HACS as a custom integration
---
## 0. Document Purpose
This document specifies a Home Assistant **custom integration** that exposes a
subset of entities over the Model Context Protocol (MCP) to an external,
low-trust client (a guest-facing chatbot backend), using a credential that is
architecturally incapable of reaching anything outside that subset — including
Home Assistant's own REST/WebSocket API.
It is written for an implementing agent with no prior context on this
decision. Section 3 explains *why* this project exists instead of using
Home Assistant's built-in `mcp_server` integration or entity permission
system, with source-level evidence. Sections 5 onward are the actual spec.
---
## 1. Problem Statement & Goals
The operator runs a Home Assistant instance for a property also used as a
short-term rental. A guest-facing chatbot (custom backend: AWS Lambda behind
Bedrock AgentCore) needs read access — and possibly write access later — to a
small, curated set of entities (e.g. Wi-Fi credentials, checkout time,
door lock state). The operator also wants their own, separate, broad use of
Home Assistant's official MCP integration for personal AI use to keep working
unaffected.
**Goals:**
- G1. A second, independently-scoped MCP-protocol-compatible endpoint,
  usable by an MCP-capable client, exposing only an explicit allowlist of
  entities.
- G2. The credential used by the guest chatbot must not be usable against
  any other Home Assistant API (REST, WebSocket, the official MCP server,
  Assist, etc.) even if leaked.
- G3. Entity allowlist is configured through Home Assistant's UI (config/
  options flow), not by editing files on the host.
- G4. Ships as an installable HACS custom integration, publicly maintained
  on GitHub.
- G5. Sustainable to maintain against a fast-moving upstream (Home Assistant
  core ships breaking changes on a roughly monthly cadence).
**Non-goals for v1** (see §17 for what's deferred vs. explicitly out of scope):
- Multiple simultaneous guest profiles/secrets with different allowlists.
- Write/control actions (service calls) — read-only in v1, designed for
  extension (§8.8).
- Rate limiting/brute-force protection implemented in-integration (v1 relies
  on the operator's existing Cloudflare Zero Trust tunnel for this; see §9).
- Reproducing Home Assistant's Assist/conversation intent system. This
  integration does not use `llm.async_get_api()` or entity exposure — see §3.
---
## 2. Non-Goals (Explicit Exclusions)
- This is **not** a replacement for Home Assistant's built-in `mcp_server`
  integration. Both are expected to run side by side.
- This is **not** a general-purpose RBAC system for Home Assistant users. It
  does not touch `hass.auth`, users, or groups at all — see §3.2 for why.
- This is **not** designed to be exposed directly to the raw internet. TLS
  termination and network-layer access control are the operator's
  responsibility (Cloudflare Zero Trust tunnel in the reference deployment).
---
## 3. Background: Why a New Integration
This section documents two platform limitations that were confirmed by
reading Home Assistant core source directly (not documentation or forum
posts), because they are the reason this project exists rather than
reusing existing mechanisms. An implementing agent should not attempt to
route around this design by re-proposing those mechanisms without first
re-verifying that these constraints still hold against the target HA
version (see §12).
### 3.1 The built-in `mcp_server` integration cannot be scoped per-client
Source: `homeassistant/components/mcp_server/manifest.json` (home-assistant/core):
```json
"single_config_entry": true
```
Home Assistant's config-entry framework enforces exactly one instance of
this integration. A second, differently-scoped instance cannot be added
through the UI or otherwise.
There is a secondary route, `/api/mcp/{api_id}`
(`homeassistant/components/mcp_server/http.py`), that lets a request select
a specific registered LLM API by ID instead of the config entry's default.
It is explicitly gated:
```python
if api_id != llm.LLM_API_ASSIST and not request["hass_user"].is_admin:
    raise Unauthorized
```
Any API other than the built-in Assist API requires the connecting user to
be a Home Assistant **admin**. A non-admin guest-chatbot user can never reach
a second, narrower LLM API through this endpoint, and granting the chatbot
user admin to work around this would defeat the entire purpose (full blast
radius restored).
**Conclusion:** there is no way, using only the stock `mcp_server`
integration, to expose two differently-scoped, non-admin-reachable MCP
surfaces at once.
### 3.2 Home Assistant access tokens are not endpoint-scoped
Source: `homeassistant/components/http/auth.py`:
```python
refresh_token = hass.auth.async_validate_access_token(auth_val)
```
and `homeassistant/helpers/http.py`:
```python
requires_auth = True  # HomeAssistantView default
```
The `mcp_server` HTTP views do not override `requires_auth`, so they use the
same authentication path as every other Home Assistant HTTP endpoint. A
Bearer token (Long-Lived Access Token or OAuth access token) is only proof
of "this request is authenticated as user X." There is no scope field
restricting a token to a subset of the API — whatever user X's permissions
allow, that token can do, on **any** endpoint (`/api/mcp`, `/api/states`,
`/api/services/*`, etc.).
**Conclusion:** a Home Assistant access token, no matter which user it
belongs to or how narrowly that user's intended use is, is not a
credential that can be scoped to "MCP only." A credential with that
property has to be issued and checked entirely outside `hass.auth`.
### 3.3 Why not Home Assistant's entity permission system (`.storage/auth`)
Investigated and rejected as the mechanism for the guest chatbot specifically
(still valid to use for human users of the instance):
- No supported API creates custom groups with entity-scoped policies —
  `async_create_group` does not exist anywhere in `homeassistant/auth/`.
  The only way such a group exists is a hand-edit of `.storage/auth`.
- No schema validation occurs when such a group is loaded
  (`homeassistant/auth/auth_store.py` reads `group_dict.get("policy")`
  directly). A malformed policy will not fail at boot — it raises an
  uncaught `AssertionError` the first time something evaluates that user's
  access.
- Policy merging across a user's groups is most-permissive-wins
  (`homeassistant/auth/permissions/merge.py`): if a user is in both a
  scoped custom group and the built-in `system-read-only` group (whose
  policy is `{"entities": {"all": {"read": true}}}`), the merge result
  still grants read on every entity. Scoping only works if the user is
  removed from every broader group.
This mechanism is real, but unsupported, fragile, and — combined with §3.1
and §3.2 — still wouldn't produce a token that's provably restricted to a
single small surface. It's mentioned here so the implementing agent doesn't
propose it as an alternative.
---
## 4. Terminology
| Term | Meaning |
|---|---|
| Guest secret | The credential the external client presents to this integration's endpoint. Not a Home Assistant access token. |
| Allowlist | The operator-configured set of entities (and optionally domains) exposed through this integration. |
| Client | The external MCP client (AWS Lambda / Bedrock AgentCore backend) consuming this integration's endpoint. |
| Upstream | The `home-assistant/core` repository, specifically `homeassistant/components/mcp_server/`. |
---
## 5. Functional Requirements
- **FR-1**: The integration registers one HTTP endpoint implementing the MCP
  Streamable HTTP transport (JSON-RPC over HTTP POST), mirroring the shape
  of upstream's `/api/mcp` (see §8.7), at a configurable or fixed path
  distinct from `/api/mcp` (e.g. `/api/concierge_mcp`).
- **FR-2**: Every request to that endpoint is authenticated by a secret
  defined entirely by this integration (§8.5), independent of
  `hass.auth`. Requests without a valid secret receive `401`.
- **FR-3**: The exposed MCP tools operate only against entities present in
  the operator-configured allowlist. Any tool call referencing an entity
  outside the allowlist is rejected with a clear MCP-level error (not a
  crash, not a silent no-op).
- **FR-4**: v1 tools are read-only:
  - `get_state(entity_id)` — returns state + allowed attributes for one
    allowlisted entity.
  - `list_entities()` — returns the allowlisted entities and their
    friendly names, so the client can discover what's available without
    hardcoding IDs.
- **FR-5**: The allowlist is configured via Home Assistant's Options Flow UI
  (entity picker), not a YAML file. Config is stored in the config entry
  (`.storage/core.config_entries`), following standard HA integration
  patterns.
- **FR-6**: The guest secret is generated by the integration (not
  operator-supplied) at setup time, shown once in the config flow, and can
  be regenerated via the options flow (old secret invalidated immediately
  on regeneration).
- **FR-7**: The response shape for tool calls matches the official
  `mcp_server` integration's MCP tool-call framing (`types.TextContent`
  with JSON-encoded body) so an MCP client already written against the
  official integration works against this one with only a URL/secret
  change.
- **FR-8**: The integration must be independently addable alongside the
  official `mcp_server` integration with no interaction between the two
  (verified by an integration test — see §10).
---
## 6. Non-Functional Requirements
- **NFR-1 (Security)**: Compromise of the guest secret must not grant any
  capability beyond what's in the allowlist. No code path may fall back to
  accepting a standard HA access token, session cookie, or admin bypass.
- **NFR-2 (Isolation)**: The integration must not read or write
  `hass.auth` state, and must not require the calling context to have any
  particular Home Assistant user or permissions.
- **NFR-3 (Compatibility)**: Must run on Home Assistant Core, Supervised,
  Container, and OS install types (no assumptions about Supervisor being
  present).
- **NFR-4 (Maintainability)**: See §12. The upstream `mcp_server`
  integration and the `mcp` Python SDK both evolve independently of this
  project; the design must make drift detectable, not silent.
- **NFR-5 (Testability)**: Fully testable with `pytest-homeassistant-custom-component`
  without a live Home Assistant instance or live upstream network access.
- **NFR-6 (Distributability)**: Installable via HACS as a custom repository,
  and eligible for the HACS default store later if desired.
---
## 7. Architecture Overview
```
                         Cloudflare Zero Trust Tunnel
                                     │
                                     ▼
                     ┌───────────────────────────────┐
                     │  Home Assistant Core process   │
                     │                                 │
   /api/mcp  ───────►│  mcp_server (official, HA core) │──► llm.AssistAPI ──► broad,
                     │                                 │    Assist-exposed entities
                     │                                 │    (operator's own use)
                     │                                 │
/api/concierge_mcp ────►│  concierge_mcp (this integration)  │──► EntityAllowlist ──► curated
                     │                                 │    entity subset
                     │  requires_auth = False           │    (guest chatbot use)
                     │  own secret check (§8.5)          │
                     └───────────────────────────────┘
```
Both integrations run in the same HA process and read the same entity
state machine, but share **no** code path for authentication or entity
exposure. This is intentional (§3.2).
---
## 8. Detailed Design
### 8.1 Directory Layout
```
custom_components/concierge_mcp/
├── __init__.py
├── manifest.json
├── config_flow.py
├── const.py
├── auth.py              # secret generation, storage, constant-time check
├── allowlist.py         # allowlist schema, validation, entity resolution
├── http.py              # HomeAssistantView(s), requires_auth = False
├── mcp_protocol.py       # mcp.server.Server wiring, tool definitions
├── options_flow.py       # entity picker, secret regeneration
├── strings.json / translations/en.json
├── diagnostics.py        # redacts the secret; see §9
└── quality_scale.yaml    # optional, mirrors upstream convention
tests/
├── conftest.py
├── test_config_flow.py
├── test_auth.py          # includes the negative test in §10
├── test_allowlist.py
├── test_mcp_protocol.py
└── test_http.py
.github/workflows/
├── validate.yml          # hassfest + hacs/action, on every PR
├── test.yml               # pytest matrix, on every PR
├── upstream-sync-check.yml # scheduled, see §12
└── release.yml             # on tag push
hacs.json
CHANGELOG.md
README.md
```
### 8.2 `manifest.json`
Mirror upstream's shape but as an independent domain:
```json
{
  "domain": "concierge_mcp",
  "name": "Concierge MCP Server",
  "codeowners": ["@alexlenk"],
  "config_flow": true,
  "dependencies": ["http"],
  "documentation": "https://github.com/alexlenk/ha-concierge-mcp",
  "issue_tracker": "https://github.com/alexlenk/ha-concierge-mcp/issues",
  "integration_type": "service",
  "iot_class": "local_push",
  "requirements": ["mcp==<pin, see §13>"],
  "single_config_entry": false,
  "version": "0.1.0"
}
```
Notes:
- `dependencies` is `["http"]` only — deliberately **not** `["conversation"]`
  like upstream, since this integration does not use `llm`/Assist at all
  (§3, §8.7).
- `single_config_entry` should be considered: `false` allows multiple
  guest profiles later (§17 open decision) at the cost of needing a
  per-entry route or an entry-id-suffixed path. Default to `true` for v1
  scope (one profile) and revisit per §17.
- `version` here is the integration's own version, unrelated to Home
  Assistant's version — see §15.
### 8.3 Config Flow & Options Flow
**Config flow (initial setup):**
1. Single step, no user input required beyond confirmation.
2. On submit: generate the guest secret (§8.5), create the config entry
   with an empty allowlist, and show the secret once in the flow's result
   (`async_create_entry` description or a follow-up "show info" step —
   confirm which pattern the target HA version's `ConfigFlow` API
   supports for a "show this once" UX; this has changed across HA
   releases, so verify at implementation time rather than assuming a
   specific method signature from this doc).
**Options flow:**
1. Entity picker (`EntitySelector`, `multiple=True`) to manage the
   allowlist. Each entry additionally needs a way to mark whether it's
   read-only or (future) control-enabled — a per-entity dict, not just a
   flat entity_id list. See `allowlist.py` schema in §8.4.
2. A "Regenerate secret" action that invalidates the old one immediately.
3. Explicit entity IDs only for v1 (§17 — domain-wildcard allowlisting is
   deferred, not implemented, due to scope-creep risk: a wildcard domain
   entry silently starts covering any new entity added to that domain
   later, without operator review).
### 8.4 Data Model — Allowlist Schema
Stored in the config entry's `options`:
```python
import voluptuous as vol
ALLOWLIST_ENTRY_SCHEMA = vol.Schema({
    vol.Required("entity_id"): str,
    vol.Optional("read", default=True): bool,
    vol.Optional("control", default=False): bool,  # reserved, not used until v2
})
OPTIONS_SCHEMA = vol.Schema({
    vol.Required("entities", default=[]): [ALLOWLIST_ENTRY_SCHEMA],
})
```
`allowlist.py` exposes:
```python
def is_allowed(entry: ConfigEntry, entity_id: str, *, action: str = "read") -> bool: ...
def list_allowed(entry: ConfigEntry) -> list[dict]: ...
```
This is a from-scratch, minimal schema — it intentionally does **not**
reuse `homeassistant/auth/permissions/entities.py`'s `ENTITY_POLICY_SCHEMA`.
That schema is part of `hass.auth` and pulling it in would blur the
isolation boundary this project exists to create (§3.2). Structural
similarity (entity_id → {read, control}) is fine; a shared implementation
is not.
### 8.5 Auth Design
- On first setup, generate a secret: `secrets.token_urlsafe(32)`.
- Store it in the config entry's `data` (not `options` — data is for
  connection/credential info per HA convention; options is for the
  allowlist which is expected to change more often).
- Verification: the client sends `Authorization: Bearer <secret>` (reusing
  the header name for client-library compatibility, but this is **not** an
  HA access token and must never be checked against `hass.auth`).
- Compare with `hmac.compare_digest(provided, stored)` — never `==` — to
  avoid a timing side-channel.
- On regeneration (options flow action), overwrite the stored secret and
  reload the config entry so the new value takes effect immediately.
- **Never log the secret.** Implement `async_get_config_entry_diagnostics`
  in `diagnostics.py` and redact it explicitly, per HA's diagnostics
  convention (`async_redact_data`), so it can't leak via a support bundle.
### 8.6 HTTP Transport & Routing
`http.py` registers a `HomeAssistantView` with:
```python
class ConciergeMCPView(HomeAssistantView):
    url = "/api/concierge_mcp"
    name = "api:concierge_mcp"
    requires_auth = False  # deliberate — see §3.2, §9
    async def post(self, request: web.Request) -> web.StreamResponse:
        entry = _get_config_entry(request.app[KEY_HASS])
        if not _check_secret(request, entry):
            raise HTTPUnauthorized()
        ...
```
`_check_secret` reads the `Authorization` header and compares against the
stored secret (§8.5). This is the **only** authentication check in the
request path — no fallback to `hass.auth`, no admin bypass, no exemption
for local/loopback requests (loopback bypass exists elsewhere in HA for
other purposes and must not be inherited here).
v1 implements only the stateless Streamable HTTP transport
(request → single JSON-RPC response), matching upstream's
`_async_handle_streamable_message` pattern
(`homeassistant/components/mcp_server/http.py`). The legacy SSE transport
(`session.py`'s `SessionManager`) is **not** carried over for v1 — confirm
against Bedrock AgentCore's actual MCP client capabilities before deciding
whether SSE support needs to be added; do not assume it's needed by
default, since it adds real session-lifecycle complexity (see upstream's
`SessionManager.close()` handling for what that entails).
### 8.7 MCP Protocol Layer
Reuse (verbatim or near-verbatim, same shape as upstream's `server.py`):
- The `mcp.server.Server` object and its `@server.list_tools()` /
  `@server.call_tool()` decorator wiring pattern.
- The tool-call response framing: `types.TextContent(type="text", text=json.dumps(...))`.
Do **not** reuse:
- `llm.async_get_api()` / `llm.APIInstance` — this is the Assist/intent
  system, tightly coupled to entity exposure semantics this project
  deliberately avoids (§3, FR-8).
- `_format_tool`'s dependency on `llm.Tool` — define tool schemas directly
  as `mcp.types.Tool` objects instead, built from the allowlist.
Tool implementations (`mcp_protocol.py`) call `hass.states.get(entity_id)`
directly, after checking `allowlist.is_allowed(entry, entity_id)`. No
intent parsing, no natural-language matching — the client (Lambda) is
expected to pass exact entity IDs, which is fine since `list_entities()`
(FR-4) gives it the discovery it needs.
### 8.8 Future Extension Points (v2, not in scope for v1)
- `call_service(entity_id, service, data)` tool, gated by the `control`
  flag in the allowlist schema (already present in §8.4's schema, unused
  until then).
- Multiple guest profiles (multiple secrets, each with its own allowlist)
  — would need `single_config_entry: false` and a per-entry URL suffix
  (`/api/concierge_mcp/<entry_id>` or similar). Flagged in §17.
- Per-secret rate limiting / lockout after repeated failed auth attempts,
  if Cloudflare Access is ever removed from in front of this endpoint.
---
## 9. Security Considerations & Threat Model
| Threat | Mitigation |
|---|---|
| Guest secret leaked from Lambda/Secrets Manager | Only grants access to allowlisted tools/entities — not a HA credential, cannot reach any other endpoint (§3.2, §8.5). |
| Attacker reaches the endpoint directly (bypassing Cloudflare) | Out of scope for this integration to prevent network-layer; **must** be documented in README as a deployment requirement: this endpoint should only be reachable through the operator's TLS-terminating, access-controlled tunnel, never exposed raw. |
| Timing attack on secret comparison | `hmac.compare_digest`, not `==` (§8.5). |
| Secret guessing / brute force | Not implemented in-integration for v1 (relies on Cloudflare Access in front). Documented as a known gap; revisit if this integration is ever used without such a layer in front. |
| Secret exposure via logs or diagnostics | Never logged; explicitly redacted in `diagnostics.py` (§8.5). |
| Tool call referencing a non-allowlisted entity | Explicit rejection with a clear MCP error, not a fallback to "return nothing" (which could be mistaken for the entity being unavailable) and not an unhandled exception (§FR-3). |
| Config entry data at rest | Stored in `.storage/core.config_entries`, same as every other integration's credentials in Home Assistant (API keys, tokens). This is consistent with HA's existing threat model — host-level compromise already implies credential compromise for every integration, not unique to this one. |
---
## 10. Testing Strategy (local-first)
Use `pytest-homeassistant-custom-component` (the standard tool for testing
HA custom integrations without a live instance) throughout. All of this
must run locally before any GitHub Actions work begins (per the project's
stated phasing).
Required test coverage:
- **Config flow**: happy path creates an entry with a generated secret and
  empty allowlist; secret is not empty/predictable.
- **Options flow**: adding/removing allowlist entries persists correctly;
  regenerating the secret invalidates the old one.
- **Allowlist**: schema validation rejects malformed entries; `is_allowed`
  correctly distinguishes read vs. control per entry.
- **Auth — critical isolation tests** (these are the tests that actually
  prove the design goal, not just code coverage):
  - A request with the correct guest secret succeeds.
  - A request with no `Authorization` header is rejected (`401`).
  - A request with a wrong secret is rejected (`401`).
  - **A request bearing a valid Home Assistant Long-Lived Access Token
    (for any user, including an admin) is rejected.** This is the
    regression test that proves the endpoint does not silently fall back
    to `hass.auth` — write it explicitly, don't assume it passes because
    "we didn't call `hass.auth`" elsewhere in the code.
- **MCP protocol**: `list_tools()` reflects the current allowlist;
  `call_tool("get_state", {"entity_id": "<not allowlisted>"})` returns an
  explicit error, not the entity's real state and not a crash.
- **Cross-integration isolation** (FR-8): a test that sets up both this
  integration and the official `mcp_server` integration in the same `hass`
  fixture and confirms neither's config, secret, or entity exposure
  affects the other.
Local test commands should be documented in `README.md`/`CONTRIBUTING.md`
(e.g. `pytest tests/ --cov=custom_components.concierge_mcp`), and should be
runnable with no network access (mock any upstream calls).
---
## 11. CI/CD Strategy (GitHub Actions)
Per the stated plan, CI is added once the local suite is solid — but design
the workflows now so they can be dropped in without rework.
- **`validate.yml`** (on every PR): run `home-assistant/actions/hassfest`
  (validates `manifest.json` and integration structure against current HA
  requirements) and `hacs/action` (validates HACS repository requirements —
  `hacs.json`, README, etc.).
- **`test.yml`** (on every PR): run the `pytest` suite from §10. Matrix
  across:
  - The Python versions Home Assistant core currently supports (verify at
    implementation time — this changes roughly yearly; do not hardcode a
    version from this document).
  - At minimum, the latest stable `homeassistant` core release used as the
    test dependency; ideally also the previous stable minor, to catch
    breaking changes early.
- **`upstream-sync-check.yml`** (scheduled — see §12): does not gate PRs;
  opens/updates a tracking issue when upstream drift is detected.
- **`release.yml`** (on tag push matching `v*`): build/validate, then
  create a GitHub Release. HACS resolves versions from GitHub releases/tags,
  so this is also the distribution mechanism (§14).
- **Dependabot**: configured for both the GitHub Actions themselves and the
  Python dependencies (`mcp`, `aiohttp_sse`, `anyio`, plus dev/test deps).
---
## 12. Maintainability & Upstream Sync Strategy
This integration derives its protocol-handling approach from
`homeassistant/components/mcp_server/`, which is actively maintained by
the Home Assistant core team and will continue to change. Two independent
things can drift and need separate tracking:
**12.1 Drift in the upstream `mcp_server` integration itself**
- Vendor a reference copy of the specific upstream files this design was
  based on (`server.py`, `http.py`, `session.py`, `types.py`, `const.py`
  as they existed when this doc was written) into
  `vendor/upstream_reference/` in this repo, tagged with the exact
  `home-assistant/core` commit SHA they were pulled from.
- `upstream-sync-check.yml` runs on a schedule (weekly is reasonable),
  shallow-clones `home-assistant/core` at the current `dev` branch (or
  latest release tag — decide which at implementation time based on how
  much lead time is wanted before changes hit stable), diffs
  `homeassistant/components/mcp_server/*.py` against the vendored
  reference copy, and opens a GitHub issue (or updates an existing one) if
  they differ — with the diff attached — rather than trying to
  auto-merge anything. A human reviews whether the drift is relevant
  (most upstream changes to the `llm.API`-based tool logic won't be,
  since this project doesn't use that path — but changes to
  `requires_auth` handling, the HTTP view base class, or the `mcp` SDK
  usage pattern would be).
- Update the vendored reference copy and its recorded SHA whenever a sync
  review is completed, whether or not code changes resulted.
**12.2 Drift in Home Assistant core's platform APIs more broadly**
Independent of the `mcp_server` component specifically, this integration
depends on stable-but-evolving HA internals: `HomeAssistantView`,
config-entry/options-flow APIs, `EntitySelector`, diagnostics redaction
helpers. Mitigate via:
- The `test.yml` matrix (§11) against at least two HA core versions, so
  breaking changes surface in CI rather than in production.
- Subscribing to Home Assistant's breaking-changes blog category / release
  notes as part of routine maintenance — not automatable, call it out
  explicitly in `CONTRIBUTING.md` as an expected maintainer task before
  each HA minor release.
- `hassfest` in `validate.yml` catches a meaningful subset of
  manifest/schema-level breakage automatically.
**12.3 Minimum supported Home Assistant version**
Declare a minimum supported HA core version in `hacs.json`
(`"homeassistant": "<version>"`). Set this policy explicitly (e.g.,
"support the current stable release and the two prior minors") and revisit
it each time a sync review (§12.1) happens, rather than leaving it to grow
stale silently.
**12.4 Exit strategy**
If Home Assistant core ever adds native support for multiple scoped MCP
server instances or endpoint-scoped access tokens (either of the gaps
identified in §3.1/§3.2), this integration becomes unnecessary. Note this
explicitly in the README as a known possibility, so future maintainers
don't feel obligated to keep maintaining it past the point where the
platform gap it fills has closed.
---
## 13. Dependency Management Strategy
- Pin `mcp`, `aiohttp_sse`, and `anyio` in `manifest.json`'s
  `requirements` to versions **compatible with** whatever the
  currently-targeted upstream HA release itself pins in
  `homeassistant/components/mcp_server/manifest.json`. These run in the
  same Python environment as HA core — installing an incompatible `mcp`
  SDK version risks conflicting with what HA core's own `mcp_server`
  integration (if also installed, which is expected — see FR-8) requires.
  Check this pin explicitly during each upstream sync review (§12.1), not
  just once at initial implementation.
- Dependabot (§11) proposes updates automatically; each bump must pass the
  full `test.yml` matrix before merging, specifically including the
  cross-integration isolation test (§10) — a `mcp` SDK bump is exactly the
  kind of change that could subtly alter tool-call framing in a way that
  breaks client compatibility without breaking any other test.
- Dev/test dependencies (`pytest-homeassistant-custom-component`, `ruff`,
  etc.) tracked the same way, lower urgency than the runtime deps above.
---
## 14. HACS & Distribution Requirements
- `hacs.json` at repo root, minimum fields: `name`, `homeassistant`
  (minimum version, §12.3), `render_readme` (recommended `true`).
- Standard `custom_components/<domain>/` layout (already specified in §8.1)
  — this is what HACS expects for a "custom integration" category
  repository.
- `README.md` covering: what this is, why it's separate from the official
  `mcp_server` integration (a short version of §3, so users don't file
  "why not just use the built-in one" issues), installation via HACS,
  config flow walkthrough, security model summary (§9) — specifically the
  requirement that this endpoint sit behind a TLS-terminating,
  access-controlled proxy/tunnel, not be exposed raw.
- `CHANGELOG.md`, updated per release, following the versioning scheme in
  §15.
- Optional, not required for v1: submission to `home-assistant/brands` for
  an integration icon; only worth doing once the project is stable and
  public.
- License: Home Assistant core is Apache-2.0. Any code structurally
  derived from `homeassistant/components/mcp_server/` (§12.1's vendored
  reference, and the reused wiring pattern in §8.7) must retain
  appropriate attribution and use a compatible license (Apache-2.0
  recommended for this repo to keep that straightforward) — confirm
  license compatibility explicitly before first public release, don't
  assume this doc's mention of it is sufficient legal review.
---
## 15. Versioning & Release Process
- Semantic versioning (`vMAJOR.MINOR.PATCH`) for this integration's own
  releases — independent of Home Assistant's own version numbers, which
  use a `YYYY.M.P` calendar scheme. Don't conflate the two anywhere in
  code or docs.
- A GitHub Release + matching tag per version, built by `release.yml`
  (§11). HACS resolves installable versions from these.
- Breaking changes to the allowlist schema or MCP tool surface (i.e.,
  anything that could break an already-configured guest chatbot) bump
  MAJOR. New tools/config options bump MINOR. Fixes bump PATCH.
---
## 16. Milestones / Phased Delivery Plan
- **M1 — Local MVP (no CI yet)**: directory scaffold, manifest, config
  flow with secret generation, options flow with entity picker, HTTP view
  with secret-only auth, `get_state`/`list_entities` tools, full local
  pytest suite from §10 passing, manual end-to-end test against a real
  local HA instance and a simple MCP test client (not necessarily the real
  Lambda yet).
- **M2 — CI hardening**: `validate.yml` and `test.yml` added and green;
  Dependabot configured; first tagged release; installable via HACS custom
  repository URL.
- **M3 — Real client integration**: verify against the actual Bedrock
  AgentCore MCP client — this is when the SSE-vs-stateless transport
  question from §8.6 gets a real answer, and when the response-shape
  compatibility (FR-7) gets validated against real client code rather than
  assumption.
- **M4 — Upstream sync automation**: `upstream-sync-check.yml` added,
  vendored reference copy established (§12.1).
- **M5 — v2 (later, separate design pass)**: `call_service` support
  (§8.8), possibly multi-profile support (§17), revisit rate limiting if
  the Cloudflare layer ever changes.
---
## 17. Open Decisions for Implementer
These were intentionally left open rather than guessed at:
1. **Domain-wildcard allowlist entries.** v1 spec (§8.3) restricts to
   explicit `entity_id`s for least-privilege reasons. If config churn
   becomes painful in practice, domain/glob support can be added later —
   but should remain opt-in and clearly labeled as higher-risk in the UI,
   not the default.
2. **Multiple guest profiles.** Not in v1 (§1, §8.8). If needed later,
   requires revisiting `single_config_entry` (§8.2) and the URL scheme.
3. **SSE transport support.** Deferred until M3 (§16) confirms whether
   Bedrock AgentCore's MCP client actually needs it. Don't build it
   speculatively.
4. **"Show secret once" UX pattern in the config flow.** HA's `ConfigFlow`
   API for this has changed across versions — implementer should check the
   target HA version's current pattern rather than copy one from an older
   example.
5. **Naming — decided.** The project is named **Concierge MCP**
   (domain: `concierge_mcp`). Rationale: a concierge helps guests with a
   specific, curated set of things and holds no master key — a reasonably
   close analogy to what this integration actually does, and the name
   doesn't lock the project to the Airbnb use case specifically (it reads
   fine for any future "limited, curated read surface for an untrusted
   client" use). This name should now be treated as final wherever it
   appears in code (domain string, class prefixes, repo name) — changing
   a HACS-listed domain name after users have installed it is disruptive,
   so don't casually rename mid-implementation.
---
## 18. References
All of the following were read directly from `home-assistant/core` (branch
`dev`) during design of this document. Re-verify against the current state
of these files before implementation, since upstream changes over time
(§12.1):
- `homeassistant/components/mcp_server/manifest.json`
- `homeassistant/components/mcp_server/config_flow.py`
- `homeassistant/components/mcp_server/http.py`
- `homeassistant/components/mcp_server/server.py`
- `homeassistant/components/mcp_server/session.py`
- `homeassistant/components/mcp_server/types.py`
- `homeassistant/components/mcp_server/const.py`
- `homeassistant/components/mcp_server/quality_scale.yaml`
- `homeassistant/components/http/auth.py`
- `homeassistant/helpers/http.py`
- `homeassistant/auth/auth_store.py`
- `homeassistant/auth/permissions/{const,entities,merge,util,system_policies,models}.py`
- `homeassistant/auth/models.py`
