"""Enable custom integration loading in the real HA test runtime."""

import pytest


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Allow Home Assistant to load the integration from the checkout."""
