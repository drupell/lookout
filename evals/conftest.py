"""Shared pytest configuration for evals."""

import os

import pytest


@pytest.fixture(autouse=True)
def set_test_mode():
    """Ensure TEST_MODE is set for all eval runs."""
    os.environ["TEST_MODE"] = "true"
    yield
    os.environ.pop("TEST_MODE", None)
