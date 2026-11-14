"""Fixtures for the SenorFungi tests (pytest-homeassistant-custom-component)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ from this repo."""
    return
