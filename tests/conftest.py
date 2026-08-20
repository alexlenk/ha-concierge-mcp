"""Shared fixtures for the Concierge MCP test suite.

``enable_custom_integrations`` is deliberately *not* wired up as a
suite-wide ``autouse`` fixture here, even though most of this suite needs
custom_components to be discoverable. It depends on ``hass``, so an
autouse fixture requesting it would force Home Assistant's ``hass``
fixture to fully instantiate before any other fixture in the test gets a
chance to run — including ``recorder_mock``, whose own setup (via
``recorder_db_url``) must run *before* ``hass`` exists. Only the test
modules that actually need loader discovery (anything calling
``hass.config_entries.async_setup`` for this domain) opt in via
``pytestmark = pytest.mark.usefixtures("enable_custom_integrations")`` at
the top of the module instead.
"""
from __future__ import annotations
