# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial implementation: `/api/concierge_mcp` MCP Streamable HTTP
  endpoint, independent guest-secret authentication, UI-managed entity
  allowlist, and the `get_state` / `list_entities` read-only tools.
- `get_state` filters low-signal Home Assistant attributes out of its
  response; `list_entities` caps results at 50 with a truncation message.
- Optional second auth path for interactive use (e.g. testing via
  Claude.ai): a Cloudflare Access JWT, verified independently of the
  guest secret, off by default.
