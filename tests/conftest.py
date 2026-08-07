"""Shared fixtures for the Concierge MCP test suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components discoverable, for every test in this suite."""
    yield
