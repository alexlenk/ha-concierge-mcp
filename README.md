# Concierge MCP Server

A Home Assistant custom integration that exposes a small, operator-curated
allowlist of entities over the [Model Context Protocol](https://modelcontextprotocol.io)
to a low-trust, external client — for example a guest-facing chatbot for a
short-term rental — using a credential that cannot reach anything else in
Home Assistant, even if it leaks.

It registers a second MCP endpoint at `/api/concierge_mcp`, alongside (not
instead of) Home Assistant's own [`mcp_server`](https://www.home-assistant.io/integrations/mcp_server/)
integration, which keeps working unaffected for your own, broader use.

## Why not just use the built-in `mcp_server` integration?

Two platform limitations, confirmed by reading `home-assistant/core`
directly:

1. **It can't be scoped per client.** `mcp_server`'s manifest declares
   `"single_config_entry": true` — Home Assistant only allows one instance.
   A second, narrower instance can't be added through the UI.
2. **Home Assistant access tokens aren't endpoint-scoped.** A Long-Lived
   Access Token or OAuth token is proof of "authenticated as user X" —
   whatever user X can do, that token can do, on *any* HTTP endpoint
   (`/api/mcp`, `/api/states`, `/api/services/*`, ...). There's no way to
   mint a token that's restricted to "MCP only," let alone to a subset of
   entities.

Combined, there's no way to expose two differently-scoped, non-admin MCP
surfaces with the stock integration. This project exists to provide the
second, narrow one — with its own secret, its own entity allowlist, and no
code path that ever touches `hass.auth`.

If Home Assistant core ever adds native support for either of these gaps,
this integration becomes unnecessary — that would be a good problem to
have.

## What it does (v1)

- One HTTP endpoint, `/api/concierge_mcp`, implementing the MCP Streamable
  HTTP transport (stateless JSON-RPC over POST).
- Authenticated by a guest secret this integration generates and owns —
  never a Home Assistant access token, never checked against `hass.auth`.
- Three read-only tools:
  - `list_entities()` — discovery: the allowlisted entities and their
    friendly names.
  - `get_state(entity_id)` — state and attributes for one allowlisted
    entity.
  - `get_history(entity_id, hours=24)` — recent state transitions for one
    allowlisted entity (e.g. "when was the door unlocked"). Requires Home
    Assistant's `recorder` integration; capped at 7 days of lookback and
    100 returned transitions, both enforced server-side.
- Any call referencing an entity outside the allowlist is rejected with an
  explicit MCP-level error, never a silent no-op and never a crash.
- The allowlist is managed entirely through the integration's Options
  flow (an entity picker) — no YAML editing.
- An optional second, independent auth path for a human operator to use
  the endpoint interactively (e.g. adding it to Claude.ai for testing),
  via Cloudflare Access — see [Interactive access via Cloudflare
  Access](#interactive-access-via-cloudflare-access-optional) below. Off
  by default; the guest secret is unaffected either way.

Write/control actions are intentionally out of scope for v1 (see the
design document in this repo for what's planned for v2).

## Installation (HACS)

1. HACS → Integrations → ⋮ → Custom repositories → add this repository
   URL, category "Integration".
2. Install "Concierge MCP Server", restart Home Assistant.
3. Settings → Devices & Services → Add Integration → "Concierge MCP
   Server".
4. Copy the guest secret shown during setup — it is shown once.
5. Open the integration's options and pick the entities to expose.

## Updating

HACS installs and updates from tagged GitHub Releases, never from `main`
directly — a fix merged to `main` isn't available to install until a
release is tagged (this repo automates that: a manifest version bump on
`main` gets tagged and released automatically).

After updating, **restart Home Assistant fully** — "Reload" on the
integration is not enough. A custom component's Python is only re-imported
on a full restart, so a patched file with a stale process behind it will
look like the update had no effect.

## Security model

- **Compromise of the guest secret grants nothing beyond the allowlist.**
  It is not a Home Assistant credential and cannot reach `/api/states`,
  `/api/services/*`, `/api/mcp`, or anything else.
- Comparison uses `hmac.compare_digest`, not `==`, to avoid a timing
  side-channel.
- The secret is never logged and is redacted from diagnostics exports.
- **This endpoint must sit behind a TLS-terminating, access-controlled
  proxy or tunnel** (the reference deployment uses a Cloudflare Zero
  Trust tunnel). It is not designed to be exposed directly to the raw
  internet: there is no in-integration rate limiting or brute-force
  protection in v1.

## Interactive access via Cloudflare Access (optional)

The guest secret is built for a headless client (a chatbot backend) —
there's no browser to complete an OAuth redirect. If you want to use this
endpoint yourself interactively (for example, adding it to Claude.ai as a
custom connector for testing), the guest secret isn't the right fit for
that: OAuth, where you sign in as yourself, is.

This integration doesn't implement OAuth itself. Instead, it recognizes a
second, completely independent credential: a signed JWT from [Cloudflare
Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/)
sitting in front of this endpoint (through the same Cloudflare Tunnel
already required above). Cloudflare Access runs the entire OAuth flow at
its edge — sign-in, consent, token issuance — and forwards a signed
`Cf-Access-Jwt-Assertion` header once you're authenticated. This
integration verifies that JWT's signature against Cloudflare's own public
keys and checks its `aud` claim against the specific Access Application
you configure, which is what scopes it to only this endpoint.

**This path is off by default.** Configure it from the integration's
options ("Configure Cloudflare Access sign-in") only if you want it — both
the Cloudflare Access team domain and the Access Application's AUD tag
must be set, or every request is evaluated as if this feature doesn't
exist. It never weakens or replaces the guest secret; either credential is
independently sufficient, and compromising one path doesn't touch the
other.

### Troubleshooting: Claude.ai connector fails before any login screen appears

If Claude.ai's custom connector fails immediately with an error like
`Authorization with ... failed` or `Couldn't register with ...'s sign-in
service`, and referencing an `ofid_...` code, and **no request shows up in
Home Assistant's logs at all**, the request likely never reached Home
Assistant. Cloudflare's dashboard-level **"Block AI bots"** setting matches
Claude's backend user-agent (e.g. `Claude-User/1.0 (+https://claude.ai)`)
and returns a bare `403` before Cloudflare Access's own OAuth challenge is
ever issued — indistinguishable, from the outside, from a broken OAuth
setup.

**The diagnostic rule:** a `403` with no `WWW-Authenticate` header means the
request was blocked at Cloudflare's edge, before it reached Access or this
integration — check this before auditing anything about OAuth. Confirm with:

```
curl -sS -o /dev/null -w '%{http_code}\n' \
  -A 'Claude-User/1.0 (+https://claude.ai)' \
  -X POST https://<your-hostname>/api/concierge_mcp
```

- `403` (and no `WWW-Authenticate` on a `-I`/verbose request) → Cloudflare's
  bot blocking is the problem, not this integration or Access.
- `401` with `WWW-Authenticate` present → the request reached Access
  correctly; the challenge is being issued as expected.

**Fix:** in the Cloudflare dashboard, under Security → Bots, disable **Block
AI bots** / **Block AI training bots** for this hostname — Cloudflare Access
plus this integration's own JWT check still protect the endpoint. If the
setting must stay on zone-wide, add a WAF custom rule (Security → WAF →
Custom rules) that **Skip**s bot management for just this endpoint's paths,
placed first:

```
(http.host eq "<your-hostname>")
and (
  starts_with(http.request.uri.path, "/api/concierge_mcp")
  or starts_with(http.request.uri.path, "/.well-known/")
)
```

## Development

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
pytest tests/ --cov=custom_components.concierge_mcp
```

Tests run fully offline against `pytest-homeassistant-custom-component`
— no live Home Assistant instance or network access required.

## Project docs

- [`docs/DESIGN.md`](docs/DESIGN.md) — the full requirements and design
  document this integration was built from.
- [`docs/STATUS.md`](docs/STATUS.md) — what's implemented vs. outstanding
  against that design, and pointers to the open issues tracking the rest.
