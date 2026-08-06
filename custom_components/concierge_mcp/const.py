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

MCP_SERVER_NAME = "concierge-mcp"
MCP_SERVER_VERSION = "0.1.0"
MCP_PROTOCOL_VERSION = "2025-06-18"

TOOL_GET_STATE = "get_state"
TOOL_LIST_ENTITIES = "list_entities"

ERROR_NOT_ALLOWED = "entity_not_allowed"
ERROR_NOT_FOUND = "entity_not_found"
