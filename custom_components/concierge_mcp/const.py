"""Constants for the Concierge MCP integration."""

DOMAIN = "concierge_mcp"

CONF_SECRET = "secret"
CONF_ENTITIES = "entities"
CONF_ENTITY_ID = "entity_id"
CONF_READ = "read"
CONF_CONTROL = "control"

DEFAULT_NAME = "Concierge MCP Server"

API_URL = "/api/concierge_mcp"

HEADER_AUTHORIZATION = "Authorization"
AUTH_SCHEME = "Bearer"

SECRET_BYTES = 32

# Second, independent, opt-in auth path: a human operator testing this
# endpoint interactively (e.g. via Claude.ai) through a Cloudflare Access
# application, instead of the guest secret used by the headless guest
# chatbot backend. Off by default — both config values must be set.
CONF_CF_ACCESS_TEAM_DOMAIN = "cf_access_team_domain"
CONF_CF_ACCESS_AUD = "cf_access_aud"
HEADER_CF_ACCESS_JWT = "Cf-Access-Jwt-Assertion"
CF_ACCESS_JWT_ALGORITHM = "RS256"
CF_ACCESS_JWKS_LIFESPAN_SECONDS = 3600

MCP_SERVER_NAME = "concierge-mcp"
MCP_SERVER_VERSION = "0.1.1"
MCP_PROTOCOL_VERSION = "2025-06-18"

TOOL_GET_STATE = "get_state"
TOOL_LIST_ENTITIES = "list_entities"
TOOL_GET_HISTORY = "get_history"

ERROR_NOT_ALLOWED = "entity_not_allowed"
ERROR_NOT_FOUND = "entity_not_found"
ERROR_HISTORY_UNAVAILABLE = "history_unavailable"

DEFAULT_HISTORY_HOURS = 24
MAX_HISTORY_HOURS = 168  # 7 days
MAX_HISTORY_STATES = 100
